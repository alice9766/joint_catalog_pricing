#!/usr/bin/env python3
"""Frozen 300-second joint scan control layer. No solver imported by this file.

static-check is read-only with respect to processes/resources; probe, smoke, run
are deliberately separate commands. Worker and vendor bytes are archived originals.
"""
from __future__ import annotations
import argparse
import ast
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import t_scan_runner as legacy
from logic300 import OUTPUT_FIELDS, STOP_STATUSES, aggregate, classify, recover, recovered_launch_evidence, resumable

ROOT = Path(__file__).resolve().parent.parent
STABLE_KEYS = ("system", "release", "machine", "python", "executable", "parent_affinity",
               "worker_cpu", "cpu_model", "cpu_governor", "rlimit_as_parent",
               "cgroup_memory_max_bytes", "thread_environment")
PROTO_ID = "SafeRefresh-R26-Joint-MT-300s-v1"


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy.atomic_json(path, value)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def event(path, name, **data):
    legacy.append_json(path, {"event": name, "timestamp": legacy.now(), **data})


@contextmanager
def durable():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def local(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("unsafe frozen path")
    result = (root / rel).resolve()
    result.relative_to(root.resolve())
    return result


def verify(root=ROOT):
    if sys.flags.optimize:
        raise RuntimeError("Python -O/PYTHONOPTIMIZE is forbidden: original worker/replay assertions must remain active")
    freeze = read(root / "protocol/freeze.json")
    config = read(root / "protocol/protocol_frozen.json")
    if config["protocol_id"] != PROTO_ID or config["protocol_status"] != "FROZEN_BEFORE_PREFLIGHT":
        raise ValueError("wrong frozen protocol")
    for rel, expected in freeze["files"].items():
        if legacy.sha(local(root, rel)) != expected:
            raise ValueError("frozen bytes changed: " + rel)
    actual_py = {str(p.relative_to(root)) for folder in ("code", "vendor") for p in (root/folder).glob("*.py")}
    if not actual_py.issubset(freeze["files"]):
        raise ValueError("unfrozen Python source in active code/vendor")
    limits = config["limits"]
    expected = {"per_attempt_wall_seconds": 300, "per_worker_address_space_bytes": 4*1024**3,
                "suite_wall_seconds": None, "parallel_workers": 1, "cpu_threads": 1,
                "timeout_kill_grace_seconds": 1}
    if limits != expected:
        raise ValueError("frozen limits differ from 5.3")
    rows = read(root / "protocol/plan.json")["planned_attempts"]
    old = read(root / "archive_reference/protocol/plan.json")["planned_attempts"]
    if len(rows) != 42 or len({r["attempt_id"] for r in rows}) != 42:
        raise ValueError("exactly 42 distinct planned IDs required")
    case_reps = {}
    for r, o in zip(rows, old, strict=True):
        if r["attempt_id"] != "P300--" + o["attempt_id"] or r["original_attempt_id"] != o["attempt_id"]:
            raise ValueError("original attempt order changed")
        for k, v in o.items():
            if k != "attempt_id" and r[k] != v:
                raise ValueError("original plan metadata changed: " + k)
        if legacy.sha(local(root, r["input_file"])) != r["input_sha256"]:
            raise ValueError("input hash mismatch")
        case_reps.setdefault(r["case_id"], []).append(r["repetition"])
    if len(case_reps) != 14 or any(sorted(v) != [1,2,3] for v in case_reps.values()):
        raise ValueError("14 inputs repeated three times required")
    if legacy.sha(root/"code/t_scan_worker.py") != config["origin"]["original_worker_sha256"]:
        raise ValueError("worker must retain original bytes")
    if legacy.sha(root/"code/t_scan_runner.py") != config["origin"]["original_runner_sha256"]:
        raise ValueError("helper runner must retain original bytes")
    hashes = legacy.source_hashes(config, root/"vendor")
    return {"root": root, "config": config, "freeze": freeze, "plan": rows, "source_hashes": hashes}


def identity(bundle, cpu):
    env = legacy.environment(cpu, bundle["config"])
    # Read-only extra context; dynamic controls are verified by separate probe.
    env["cgroup_cpu_max"] = legacy.proc_text("/sys/fs/cgroup/cpu.max")
    env["cgroup_memory_events"] = legacy.proc_text("/sys/fs/cgroup/memory.events")
    env["dependencies"] = "Python standard library plus two frozen vendor files; no external numeric package used by exact worker."
    stable = {k: env.get(k) for k in STABLE_KEYS}
    stable["cgroup_cpu_max"] = env["cgroup_cpu_max"]
    environment_id = hashlib.sha256(legacy.json_bytes(stable)).hexdigest()
    fp = {"protocol_id": PROTO_ID, "freeze_sha256": legacy.sha(bundle["root"]/"protocol/freeze.json"),
          "environment_identity": stable, "environment_id": environment_id}
    return fp, env


def snapshot(bundle, out):
    target = out/"snapshot"
    target.mkdir()
    for rel, expected in bundle["freeze"]["files"].items():
        dst = local(target, rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local(bundle["root"], rel), dst)
        if legacy.sha(dst) != expected:
            raise RuntimeError("snapshot copy changed bytes")
    shutil.copy2(bundle["root"]/"protocol/freeze.json", target/"protocol/freeze.json")
    return target


def make_job(entry, folder, source, bundle, cpu):
    return {"mode":"solve", "attempt_id":entry["attempt_id"], "attempt_dir":str(folder),
            "input_path":str(local(source,entry["input_file"])), "input_sha256":entry["input_sha256"],
            "source_dir":str(source/"vendor"), "source_hashes":bundle["source_hashes"], "cpu":cpu,
            "memory_limit_bytes":4*1024**3, "K":entry["K"], "M":entry["M"], "T":entry["T"],
            "N":entry["N_raw"], "ceilings":entry["ceilings"], "total_weight":entry["total_weight"]}


def progress(folder):
    try:
        p = read(folder/"worker_progress.json")
    except (OSError, ValueError):
        p = None
    return {"worker_progress":p, "solver_started":bool(p and p.get("solver_called"))}


def launch_evidence(out, folder, row):
    event_pid = None
    events = out/"events.jsonl"
    if events.exists():
        for line in events.read_text().splitlines():
            try:
                item=json.loads(line)
            except ValueError:
                continue  # A torn final event is not treated as launch evidence.
            if item.get("event")=="WORKER_STARTED" and item.get("attempt_id")==row["attempt_id"]:
                event_pid=item.get("pid")
    return recovered_launch_evidence(row,progress(folder)["worker_progress"],event_pid)


def payload_checks(payload, job):
    validation = payload.get("validation", {})
    for name in ("external_canonical_replay", "capacity_frontier", "thresholds_and_groups", "edge_and_path_certificate"):
        if validation.get(name) != "PASS":
            raise ValueError("worker validation is incomplete: " + name)
    cert = Path(payload["certificate_path"])
    if cert.resolve() != (Path(job["attempt_dir"])/"certificate.json").resolve():
        raise ValueError("certificate outside attempt directory")
    if legacy.sha(cert) != payload["certificate_sha256"]:
        raise ValueError("certificate hash mismatch")
    controls = payload.get("controls", {})
    if controls.get("affinity") != [job["cpu"]] or controls.get("rlimit_as") != [4*1024**3]*2:
        raise ValueError("worker did not confirm frozen CPU/memory limits")
    if any(controls.get("thread_environment", {}).get(k) != "1" for k in legacy.THREAD_VARS):
        raise ValueError("worker thread environment changed")
    if not payload.get("solver_called") or not payload.get("solver_module_imported"):
        raise ValueError("missing exact-solver completion provenance")


def execute(job, deadline, events, worker):
    """wait4 owns child reaping; no Popen.poll/wait and no solver import here."""
    folder = Path(job["attempt_dir"])
    job = dict(job, parent_pid=os.getpid(), thread_vars=list(legacy.THREAD_VARS))
    write(folder/"job.json", job)
    command = [sys.executable, str(worker), str(folder/"job.json")]
    worker_env = os.environ.copy()
    worker_env.update({k:"1" for k in legacy.THREAD_VARS})
    worker_env.update(PYTHONHASHSEED="0", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1", PYTHONOPTIMIZE="0")
    write(folder/"command.json", {"argv":command,"working_directory":str(Path.cwd()),
                                 "thread_environment":{k:worker_env[k] for k in legacy.THREAD_VARS},
                                 "pythonhashseed":"0","deadline_seconds":deadline,"cleanup_grace_seconds":1})
    proc = None
    reaped = None
    first_observed = term_at = killed_at = None
    interrupted = False
    launch_error = None
    started_at = legacy.now()
    started = time.monotonic()
    def child_controls():
        os.sched_setaffinity(0, {job["cpu"]})
        resource.setrlimit(resource.RLIMIT_AS,(job["memory_limit_bytes"],)*2)
    with (folder/"stdout.log").open("wb") as stdout, (folder/"stderr.log").open("wb") as stderr:
        try:
            proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=worker_env,
                                    start_new_session=True, preexec_fn=child_controls)
            event(events,"WORKER_STARTED",attempt_id=job.get("attempt_id"),pid=proc.pid,pgid=proc.pid)
            while reaped is None:
                pid, wait_status, usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    reaped = (wait_status,usage)
                    first_observed = time.monotonic()
                    if term_at is None and first_observed-started >= deadline:
                        term_at = first_observed
                        legacy.kill_group(proc.pid,signal.SIGTERM)
                    break
                now = time.monotonic()
                if term_at is None and now-started >= deadline:
                    term_at = now
                    legacy.kill_group(proc.pid,signal.SIGTERM)
                    event(events,"TIME_LIMIT_SIGNAL",attempt_id=job.get("attempt_id"),signal="SIGTERM")
                if term_at is not None and killed_at is None and now-term_at >= 1:
                    killed_at = now
                    legacy.kill_group(proc.pid,signal.SIGKILL)
                time.sleep(.01)
            if term_at is not None:
                remaining = 1-(time.monotonic()-term_at)
                if remaining > 0:
                    time.sleep(remaining)
                legacy.kill_group(proc.pid,signal.SIGKILL)
        except KeyboardInterrupt:
            interrupted = True
        except Exception as exc:
            launch_error = repr(exc)
        finally:
            if proc is not None and (interrupted or launch_error):
                legacy.kill_group(proc.pid,signal.SIGKILL)
            if proc is not None and reaped is None:
                _pid,wait_status,usage = os.wait4(proc.pid,0)
                reaped = (wait_status,usage)
                first_observed = time.monotonic()
            if proc is not None and reaped is not None:
                proc.returncode = os.waitstatus_to_exitcode(reaped[0])
    finished = time.monotonic()
    elapsed = first_observed-started if first_observed is not None else None
    exit_code = proc.returncode if proc is not None else None
    payload = None
    try:
        payload = read(folder/"worker_result.json")
    except (OSError,ValueError):
        pass
    status, complete = classify(elapsed,deadline,exit_code,payload,interrupted,term_at is not None)
    if launch_error and not interrupted:
        status,complete = ("SOLVER_ERROR" if proc is not None else "LAUNCH_ERROR"),False
    if complete:
        try:
            payload_checks(payload,job)
        except Exception as exc:
            status,complete = "RECONSTRUCTION_FAIL",False
            launch_error = repr(exc)
    row = {k:None for k in OUTPUT_FIELDS}
    if complete:
        row.update({k:payload.get(k) for k in OUTPUT_FIELDS})
    usage = reaped[1] if reaped is not None else None
    row.update(status=status,complete_return=complete,started_at=started_at,finished_at=legacy.now(),
               process_launched=proc is not None,pid=proc.pid if proc else None,
               process_wall_seconds=elapsed,controller_wall_seconds=finished-started,
               timeout_signal_elapsed_seconds=term_at-started if term_at else None,
               cleanup_wall_seconds=finished-first_observed if first_observed else None,
               cpu_user_seconds=usage.ru_utime if usage else None,cpu_system_seconds=usage.ru_stime if usage else None,
               cpu_total_seconds=usage.ru_utime+usage.ru_stime if usage else None,
               peak_rss_linux_kib=usage.ru_maxrss if usage else None,peak_rss_bytes=usage.ru_maxrss*1024 if usage else None,
               return_code=exit_code,termination_signal=-exit_code if exit_code is not None and exit_code<0 else None,
               resource_termination_source="parent_wall_limit" if term_at else ("worker_MemoryError_under_RLIMIT_AS" if status=="MEMORY_LIMIT" else None),
               worker_result_status=payload.get("status") if payload else None,worker_message=launch_error or (payload or {}).get("message"),
               worker_result_path=str(folder/"worker_result.json") if (folder/"worker_result.json").exists() else None,
               stdout_path=str(folder/"stdout.log"),stderr_path=str(folder/"stderr.log"),
               missing_output_reason=None if complete else "No complete verified result within deadline; partial files are diagnostic only.")
    row.update(progress(folder))
    write(folder/"controller_result.json",row)
    return row,payload


def static_check(args,bundle,cpu):
    fp,env = identity(bundle,cpu)
    ast_files = []
    for folder in ("code","vendor"):
        for p in sorted((ROOT/folder).glob("*.py")):
            ast.parse(p.read_text(),filename=str(p))
            ast_files.append(str(p.relative_to(ROOT)))
    report = {"status":"STATIC_PASS" if env["environment_status"]=="PASS" else "STATIC_IDENTITY_PASS_ENV_BLOCKED",
              "scope":"STATIC_IDENTITY_AST_AND_ENVIRONMENT_READ_ONLY", "protocol_id":PROTO_ID,
              "full_fingerprint":fp,"environment":env,"ast_files":ast_files,
              "formal_inputs":14,"planned_attempts":42,"all_planned_statuses":"NOT_RUN",
              "solver_calls":0,"worker_processes_started":0,"resource_probes":0,
              "dynamic_gates":"NOT_RUN: resource probes, exact smoke, formal run, real interruption/resume"}
    write(args.output,report)
    print(json.dumps({"status":report["status"],"report":str(args.output),"solver_calls":0}))
    return 0


def resource_probe(args,bundle,cpu):
    out = legacy.new_output(args.output)
    fp,env = identity(bundle,cpu)
    report = {"status":"ENV_BLOCKED","scope":"NO_SOLVER_RESOURCE_PROBES","full_fingerprint":fp,
              "environment":env,"solver_calls":0,"formal_attempts":0,"checks":[],"created_at":legacy.now()}
    write(out/"probe.json",report)
    if env["environment_status"]!="PASS":
        return 2
    snap = snapshot(bundle,out)
    libc = ctypes.CDLL(None,use_errno=True)
    if libc.prctl(36,1,0,0,0)!=0:
        raise OSError(ctypes.get_errno(),"PR_SET_CHILD_SUBREAPER")
    for mode,expected,deadline in (("probe","PREFLIGHT_OK",10),("dummy_success","DUMMY_OK",10),
                                   ("dummy_error","SOLVER_ERROR",10),("dummy_memory","MEMORY_LIMIT",10),
                                   ("dummy_timeout","TIMEOUT",.5)):
        folder=out/mode;folder.mkdir()
        job={"mode":mode,"attempt_id":"PROBE--"+mode,"attempt_dir":str(folder),"cpu":cpu,"memory_limit_bytes":4*1024**3}
        row,payload=execute(job,deadline,out/"events.jsonl",snap/"code/t_scan_worker.py")
        passed=row["status"]==expected and (payload is None or (payload.get("solver_module_imported") is False and payload.get("solver_called") is False))
        cleanup=None
        if mode=="probe":
            controls=(payload or {}).get("controls",{})
            passed=passed and bool((payload or {}).get("oversized_mmap_blocked")) and controls.get("affinity")==[cpu] and controls.get("rlimit_as")==[4*1024**3]*2
            passed=passed and all(controls.get("thread_environment",{}).get(k)=="1" for k in legacy.THREAD_VARS)
        if mode=="dummy_timeout":
            child=(row.get("worker_progress") or {}).get("child_pid")
            reaped=False
            if child:
                try:
                    waited,_=os.waitpid(child,0);reaped=waited==child
                except ChildProcessError:
                    pass
            cleanup={"child_pid":child,"adopted_descendant_reaped":reaped,"not_running":bool(child and not Path(f"/proc/{child}").exists())}
            passed=passed and all(cleanup[k] for k in ("adopted_descendant_reaped","not_running"))
        report["checks"].append({"mode":mode,"expected":expected,"actual":row["status"],"passed":bool(passed),"group_cleanup":cleanup})
        write(out/"probe.json",report)
        if row["status"]=="INTERRUPTED":
            break
    endfp,endenv=identity(verify(),cpu)
    report["status"]="PASS" if len(report["checks"])==5 and all(c["passed"] for c in report["checks"]) and endfp==fp and endenv["environment_status"]=="PASS" else "PROBE_FAILED"
    report["finished_at"]=legacy.now();write(out/"probe.json",report)
    print(json.dumps({"status":report["status"],"report":str(out/"probe.json"),"solver_calls":0}))
    return 0 if report["status"]=="PASS" else 2


def matching_report(path,fp,scope):
    doc=read(path)
    if doc.get("status")!="PASS" or doc.get("full_fingerprint")!=fp or doc.get("scope")!=scope:
        raise RuntimeError("matching passing gate required: "+str(path))
    return doc


def smoke(args,bundle,cpu):
    fp,env=identity(bundle,cpu)
    matching_report(args.probe,fp,"NO_SOLVER_RESOURCE_PROBES")
    if env["environment_status"]!="PASS":raise RuntimeError("ENV_BLOCKED")
    out=legacy.new_output(args.output);snap=snapshot(bundle,out)
    entry=bundle["config"]["smoke_case"];folder=out/"smoke";folder.mkdir()
    report={"status":"RUNNING","scope":"SEPARATE_EXACT_SMOKE","full_fingerprint":fp,"environment":env,
            "probe_sha256":legacy.sha(args.probe),"formal_attempts":0,"created_at":legacy.now()}
    write(out/"smoke.json",report)
    row,_=execute(make_job(entry,folder,snap,bundle,cpu),10,out/"events.jsonl",snap/"code/t_scan_worker.py")
    endfp,endenv=identity(verify(),cpu)
    report.update(status="PASS" if row["status"]=="EXACT_COMPLETE" and row["complete_return"] and endfp==fp and endenv["environment_status"]=="PASS" else "SMOKE_FAILED",
                  solver_calls=int(row["solver_started"]),smoke=row,finished_at=legacy.now())
    write(out/"smoke.json",report)
    print(json.dumps({"status":report["status"],"report":str(out/"smoke.json"),"formal_attempts":0}))
    return 0 if report["status"]=="PASS" else 2


def initial_rows(plan):
    return [dict(r,launch_reserved=False,process_launched=False,solver_started=False,complete_return=False,
                 environment_id=None,**{k:None for k in OUTPUT_FIELDS}) for r in plan]


def run(args,bundle,cpu):
    fp,env=identity(bundle,cpu)
    matching_report(args.probe,fp,"NO_SOLVER_RESOURCE_PROBES")
    s=matching_report(args.smoke,fp,"SEPARATE_EXACT_SMOKE")
    if s.get("solver_calls")!=1 or s.get("probe_sha256")!=legacy.sha(args.probe):
        raise RuntimeError("smoke must use supplied matching probe")
    if env["environment_status"]!="PASS":raise RuntimeError("ENV_BLOCKED")
    out=args.output.resolve()
    if args.resume:
        if not out.is_dir():raise RuntimeError("resume requires existing run directory")
    else:
        out=legacy.new_output(out)
    with (out/"run.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not args.resume:
            snap=snapshot(bundle,out);(out/"attempts").mkdir();(out/"sessions").mkdir()
            rows=initial_rows(bundle["plan"])
            meta={"protocol_id":PROTO_ID,"freeze_sha256":fp["freeze_sha256"],"status":"READY",
                  "created_at":legacy.now(),"sessions":[],"planned_attempts":42,"historical_results_included":False}
            write(out/"checkpoint.json",{"status":"READY","attempts":rows});write(out/"metadata.json",meta)
        else:
            snap=out/"snapshot";verify(snap)
            meta=read(out/"metadata.json")
            if meta["protocol_id"]!=PROTO_ID or meta["freeze_sha256"]!=fp["freeze_sha256"]:
                raise RuntimeError("cannot resume under changed frozen code/inputs/protocol")
            if not args.resume_reason:raise RuntimeError("resume requires --resume-reason for the record")
            rows=read(out/"checkpoint.json")["attempts"]
            if [r["attempt_id"] for r in rows]!=[r["attempt_id"] for r in bundle["plan"]]:
                raise RuntimeError("checkpoint plan identity changed")
            previous=meta["sessions"][-1] if meta["sessions"] else None
            if previous and previous["environment_id"]!=fp["environment_id"] and not args.environment_change_reason:
                raise RuntimeError("environment changed: new gates plus --environment-change-reason required")
            for i,row in enumerate(rows):
                folder=out/"attempts"/row["attempt_id"];final=folder/"final.json"
                recovered=recover(row,read(final) if final.exists() else None)
                if recovered!=row:
                    event(out/"events.jsonl","RECOVERY",attempt_id=row["attempt_id"],old_status=row["status"],new_status=recovered["status"])
                    rows[i]=recovered
                    if recovered["status"]=="INTERRUPTED" and not final.exists():
                        recovered=launch_evidence(out,folder,recovered)
                        rows[i]=recovered
                        write(final,recovered)
        session_id=f"session-{len(meta['sessions'])+1:03d}"
        session={"session_id":session_id,"started_at":legacy.now(),"environment_id":fp["environment_id"],
                 "full_fingerprint":fp,"environment":env,"resume":args.resume,"resume_reason":args.resume_reason,
                 "environment_change_reason":args.environment_change_reason,"probe_sha256":legacy.sha(args.probe),"smoke_sha256":legacy.sha(args.smoke)}
        sessiondir=out/"sessions"/session_id;sessiondir.mkdir()
        shutil.copy2(args.probe,sessiondir/"probe.json");shutil.copy2(args.smoke,sessiondir/"smoke.json")
        write(sessiondir/"environment.json",session)
        meta["sessions"].append(session);meta["status"]="RUNNING";write(out/"metadata.json",meta)
        event(out/"events.jsonl","SESSION_START",session_id=session_id,environment_id=fp["environment_id"],resume_reason=args.resume_reason)
        trigger=None;interrupted=False;active=None
        try:
            for i,row in enumerate(rows):
                if not resumable(row):continue
                checked=verify();nextfp,nextenv=identity(checked,cpu)
                if nextfp!=fp or nextenv["environment_status"]!="PASS":
                    trigger="ENVIRONMENT_CHANGED";break
                folder=out/"attempts"/row["attempt_id"]
                if folder.exists():
                    # Any unexplained directory is a provenance ambiguity; never overwrite it.
                    trigger="UNEXPECTED_ATTEMPT_DIRECTORY:"+row["attempt_id"];break
                with durable():
                    folder.mkdir()
                    rows[i]=dict(row,status="RUNNING",launch_reserved=True,session_id=session_id,environment_id=fp["environment_id"],launch_reserved_at=legacy.now())
                    active=i
                    write(out/"checkpoint.json",{"status":"RUNNING","attempts":rows})
                    event(out/"events.jsonl","ATTEMPT_RESERVED",attempt_id=row["attempt_id"],index=i,input_sha256=row["input_sha256"],session_id=session_id)
                print(json.dumps({"event":"ATTEMPT_START","index":i+1,"total":42,"attempt_id":row["attempt_id"]}),flush=True)
                result,_=execute(make_job(row,folder,snap,bundle,cpu),300,out/"events.jsonl",snap/"code/t_scan_worker.py")
                final=dict(rows[i],**result)
                with durable():
                    write(folder/"final.json",final)
                    event(out/"events.jsonl","ATTEMPT_FINAL",attempt_id=row["attempt_id"],status=final["status"],session_id=session_id)
                    rows[i]=final;active=None
                    write(out/"checkpoint.json",{"status":"RUNNING","attempts":rows})
                print(json.dumps({"event":"ATTEMPT_FINAL","attempt_id":row["attempt_id"],"status":final["status"],"wall":final["process_wall_seconds"]}),flush=True)
                if final["status"]=="INTERRUPTED":interrupted=True;break
                if final["status"] not in ("EXACT_COMPLETE","TIMEOUT","MEMORY_LIMIT"):
                    trigger=row["attempt_id"];break
        except KeyboardInterrupt:
            interrupted=True
        except Exception as exc:
            trigger="CONTROLLER_ERROR";meta["controller_error"]=repr(exc)
        finally:
            with durable():
                if active is not None:
                    folder=out/"attempts"/rows[active]["attempt_id"]
                    path=folder/"final.json"
                    rows[active]=recover(rows[active],read(path) if path.exists() else None)
                    if not path.exists():
                        rows[active]=launch_evidence(out,folder,rows[active])
                        write(path,rows[active])
                for i,row in enumerate(rows):
                    if resumable(row) and (interrupted or trigger):
                        rows[i]=dict(row,status="NOT_RUN_INTERRUPTED" if interrupted else "NOT_RUN_SUITE_ERROR",trigger_attempt_id=trigger,
                                     missing_output_reason="Not launched after interruption or suite/environment error.")
                status="INTERRUPTED" if interrupted else ("STOPPED_ON_ERROR" if trigger else "FINISHED")
                meta.update(status=status,finished_at=legacy.now(),stop_trigger=trigger,status_counts=aggregate(rows)["status_counts"])
                write(out/"checkpoint.json",{"status":status,"attempts":rows})
                write(out/"metadata.json",meta)
                event(out/"events.jsonl","SESSION_FINAL",session_id=session_id,status=status,trigger=trigger)
                write(out/"summary.json",aggregate(rows))
        return 130 if interrupted else (1 if trigger else 0)


def main():
    def terminate(_sig,_frame):raise KeyboardInterrupt("controller termination requested")
    signal.signal(signal.SIGTERM,terminate)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("static-check","probe","smoke","run"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--cpu",type=int)
    parser.add_argument("--probe",type=Path)
    parser.add_argument("--smoke",type=Path)
    parser.add_argument("--resume",action="store_true")
    parser.add_argument("--resume-reason")
    parser.add_argument("--environment-change-reason")
    args=parser.parse_args()
    args.output=args.output.resolve()
    if args.command in ("smoke","run") and args.probe is None:parser.error("--probe required")
    if args.command=="run" and args.smoke is None:parser.error("--smoke required")
    if args.probe:args.probe=args.probe.resolve()
    if args.smoke:args.smoke=args.smoke.resolve()
    if args.resume and args.command!="run":parser.error("resume is only valid for run")
    bundle=verify();cpu=min(os.sched_getaffinity(0)) if args.cpu is None else args.cpu
    return {"static-check":static_check,"probe":resource_probe,"smoke":smoke,"run":run}[args.command](args,bundle,cpu)


if __name__=="__main__":
    raise SystemExit(main())
