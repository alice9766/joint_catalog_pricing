#!/usr/bin/env python3
"""Preflight (no solver) or execute the frozen R5 scan once in a new directory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_PROTOCOL = ROOT / "protocol/t_scan_v1.json"
DEFAULT_SOURCE = ROOT / "reference/vendor"
WORKER = HERE / "t_scan_worker.py"
THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")
OUTPUT_FIELDS = ("solver_wall_seconds", "external_validation_wall_seconds", "objective",
                 "capacity_frontier", "reconstruction_gap", "state_count", "threshold_count",
                 "optimality_gap", "incumbent", "upper_bound", "certificate_path",
                 "certificate_sha256", "validation")


class SuiteBudgetExhausted(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(json_bytes(data))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_json(path, data):
    with Path(path).open("ab") as f:
        f.write(json_bytes(data))
        f.flush()
        os.fsync(f.fileno())


def proc_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def environment(cpu, config):
    mem = {}
    for line in (proc_text("/proc/meminfo") or "").splitlines():
        if line.startswith(("MemTotal:", "MemAvailable:")):
            key, value, _unit = line.split()
            mem[key.rstrip(":") + "_bytes"] = int(value) * 1024
    cap_text = proc_text("/sys/fs/cgroup/memory.max")
    current_text = proc_text("/sys/fs/cgroup/memory.current")
    cap = None if cap_text in (None, "max") else int(cap_text)
    current = None if current_text is None else int(current_text)
    governor = proc_text(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor")
    cpu_model = next((line.split(":", 1)[1].strip() for line in
                      (proc_text("/proc/cpuinfo") or "").splitlines()
                      if line.startswith("model name")), None)
    result = {"timestamp": now(), "system": platform.system(), "release": platform.release(),
              "machine": platform.machine(), "python": sys.version, "executable": sys.executable,
              "parent_affinity": sorted(os.sched_getaffinity(0)), "worker_cpu": cpu,
              "cpu_model": cpu_model, "cpu_governor": governor,
              "rlimit_as_parent": list(resource.getrlimit(resource.RLIMIT_AS)),
              "cgroup_memory_max_bytes": cap, "cgroup_memory_current_bytes": current,
              "meminfo": mem, "thread_environment": {k: "1" for k in THREAD_VARS},
              "peak_rss_method": "os.wait4 rusage.ru_maxrss; Linux KiB multiplied by 1024",
              "memory_limit_semantics": "RLIMIT_AS virtual address space; separate from peak RSS"}
    limit = config["limits"]["per_worker_address_space_bytes"]
    issues = []
    if result["system"] != "Linux" or not hasattr(os, "wait4"):
        issues.append("Linux os.wait4 required")
    if cpu not in result["parent_affinity"]:
        issues.append("requested CPU not in allowed affinity")
    hard = result["rlimit_as_parent"][1]
    if hard != resource.RLIM_INFINITY and hard < limit:
        issues.append("parent RLIMIT_AS hard limit below protocol")
    if mem.get("MemAvailable_bytes", 0) < limit:
        issues.append("host available memory below protocol limit")
    if cap is not None and current is not None and cap - current < limit:
        issues.append("cgroup remaining memory below protocol limit")
    result["environment_issues"] = issues
    result["environment_status"] = "PASS" if not issues else "ENV_BLOCKED"
    return result


def source_hashes(config, source_dir):
    spec = config["solver_snapshot"]
    expected = {spec["file"]: spec["sha256"], spec["type_file"]: spec["type_file_sha256"]}
    for filename, value in expected.items():
        if sha(source_dir / filename) != value:
            raise RuntimeError("frozen source hash mismatch: " + filename)
    return expected


def fingerprint(protocol, source_dir, config, cpu):
    return {"protocol_sha256": sha(protocol), "runner_sha256": sha(__file__),
            "worker_sha256": sha(WORKER), "source_hashes": source_hashes(config, source_dir),
            "python_executable": sys.executable, "python_version": sys.version,
            "cpu": cpu, "limits": config["limits"]}


def kill_group(pid, sig):
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False


def execute(job, wall_limit, grace, event_path=None, worker_path=None,
            complete_statuses=("EXACT_COMPLETE",), suite_state=None):
    """Only this function reaps each child; never call Popen.poll()/wait()."""
    directory = Path(job["attempt_dir"])
    job["parent_pid"] = os.getpid()
    job["thread_vars"] = list(THREAD_VARS)
    atomic_json(directory / "job.json", job)
    stdout = (directory / "stdout.log").open("wb")
    stderr = (directory / "stderr.log").open("wb")
    env = os.environ.copy()
    env.update({k: "1" for k in THREAD_VARS})
    env.update({"PYTHONHASHSEED": "0", "PYTHONUNBUFFERED": "1"})
    proc = None
    term_at = None
    reaped = None
    reaped_at = None
    killed_at = None
    try:
        started_at, start = now(), time.monotonic()
        if suite_state is not None:
            if suite_state["start"] is not None and suite_state["limit"] - (start - suite_state["start"]) < wall_limit + grace:
                raise SuiteBudgetExhausted("insufficient full-attempt reserve immediately before Popen")
            if suite_state["start"] is None:
                suite_state["start"] = start
        proc = subprocess.Popen([sys.executable, str(worker_path or WORKER), str(directory / "job.json")],
                                stdout=stdout, stderr=stderr, env=env, start_new_session=True)
        if event_path:
            append_json(event_path, {"event": "WORKER_STARTED", "attempt_id": job.get("attempt_id"),
                                    "pid": proc.pid, "pgid": proc.pid, "timestamp": started_at})
        while reaped is None:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                reaped = (status, usage)
                reaped_at = time.monotonic()
                # Conservative observation rule: never accept a return first
                # observed beyond the process deadline, even if a payload exists.
                if term_at is None and reaped_at - start >= wall_limit:
                    term_at = reaped_at
                    kill_group(proc.pid, signal.SIGTERM)
                break
            elapsed = time.monotonic() - start
            if term_at is None and elapsed >= wall_limit:
                term_at = time.monotonic()
                kill_group(proc.pid, signal.SIGTERM)
                if event_path:
                    append_json(event_path, {"event": "TIME_LIMIT_SIGNAL", "attempt_id": job.get("attempt_id"),
                                            "signal": "SIGTERM", "timestamp": now()})
            if term_at is not None and killed_at is None and time.monotonic() - term_at >= grace:
                killed_at = time.monotonic()
                kill_group(proc.pid, signal.SIGKILL)
            time.sleep(0.01)
        # A timeout may leave a grandchild after the worker exits on SIGTERM.
        if term_at is not None:
            remaining = grace - (time.monotonic() - term_at)
            if remaining > 0:
                time.sleep(remaining)
            kill_group(proc.pid, signal.SIGKILL)
    except BaseException:
        if proc is not None:
            kill_group(proc.pid, signal.SIGKILL)
            if reaped is None:
                _pid, status, usage = os.wait4(proc.pid, 0)
                reaped = (status, usage)
                reaped_at = time.monotonic()
            proc.returncode = os.waitstatus_to_exitcode(reaped[0])
        raise
    finally:
        stdout.close()
        stderr.close()
    end = time.monotonic()
    wait_status, usage = reaped
    exit_code = os.waitstatus_to_exitcode(wait_status)
    proc.returncode = exit_code  # Prevent Popen.__del__ from attempting a second reap.
    payload = None
    parse_error = None
    result_file = directory / "worker_result.json"
    if result_file.exists():
        try:
            payload = json.loads(result_file.read_text())
        except (OSError, ValueError) as exc:
            parse_error = str(exc)
    if term_at is not None:
        status = "TIMEOUT"
    elif exit_code < 0:
        status = "ENV_KILLED"
    elif payload is None:
        status = "SOLVER_ERROR"
    else:
        status = payload.get("status", "SOLVER_ERROR")
        if exit_code != 0 and status in tuple(complete_statuses) + ("PREFLIGHT_OK", "DUMMY_OK"):
            status = "SOLVER_ERROR"
    row = {k: None for k in OUTPUT_FIELDS}
    complete = status in complete_statuses and exit_code == 0 and payload is not None
    if complete:
        row.update({k: payload.get(k) for k in OUTPUT_FIELDS})
    row.update({"status": status, "complete_return": complete,
                "started_at": started_at, "finished_at": now(), "pid": proc.pid,
                "process_wall_seconds": reaped_at - start,
                "controller_wall_seconds": end - start,
                "timeout_signal_elapsed_seconds": term_at - start if term_at is not None else None,
                "cleanup_wall_seconds": end - reaped_at,
                "cpu_user_seconds": usage.ru_utime, "cpu_system_seconds": usage.ru_stime,
                "cpu_total_seconds": usage.ru_utime + usage.ru_stime,
                "peak_rss_linux_kib": usage.ru_maxrss, "peak_rss_bytes": usage.ru_maxrss * 1024,
                "return_code": exit_code, "termination_signal": -exit_code if exit_code < 0 else None,
                "resource_termination_source": "parent_wall_limit" if term_at is not None else
                    ("worker_MemoryError_under_RLIMIT_AS" if status == "MEMORY_LIMIT" else None),
                "worker_result_status": payload.get("status") if payload else None,
                "worker_message": payload.get("message") if payload else parse_error,
                "worker_result_path": str(result_file) if result_file.exists() else None,
                "stdout_path": str(directory / "stdout.log"), "stderr_path": str(directory / "stderr.log"),
                "missing_output_reason": None if complete else "Worker did not complete under the protocol; no exported incumbent, bound, state count, or certificate is reported."})
    atomic_json(directory / "controller_result.json", row)
    return row, payload


def new_output(path):
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def preflight(args, config, cpu):
    out = new_output(args.output)
    env = environment(cpu, config)
    atomic_json(out / "environment.json", env)
    fp = fingerprint(args.protocol, args.source_dir, config, cpu)
    report = {"status": "ENV_BLOCKED", "scope": "NO_SOLVER_RESOURCE_PREFLIGHT",
              "solver_calls": 0, "timestamp": now(), "fingerprint": fp, "environment": env,
              "checks": [], "formal_scan_status": "NOT_RUN"}
    if env["environment_status"] != "PASS":
        atomic_json(out / "preflight.json", report)
        print(json.dumps({"status": "ENV_BLOCKED", "issues": env["environment_issues"]}), flush=True)
        return 2
    # Reap the dummy worker's deliberately spawned descendant ourselves.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    spec = [("probe", "PREFLIGHT_OK", 10), ("dummy_success", "DUMMY_OK", 10),
            ("dummy_error", "SOLVER_ERROR", 10), ("dummy_memory", "MEMORY_LIMIT", 10),
            ("dummy_timeout", "TIMEOUT", 0.5)]
    for mode, expected, limit in spec:
        folder = out / mode
        folder.mkdir()
        job = {"mode": mode, "attempt_dir": str(folder), "cpu": cpu,
               "memory_limit_bytes": config["limits"]["per_worker_address_space_bytes"]}
        row, payload = execute(job, limit, config["limits"]["timeout_kill_grace_seconds"])
        passed = row["status"] == expected
        if payload is not None:
            passed = passed and payload.get("solver_module_imported") is False
        group_cleanup = None
        if mode == "dummy_timeout":
            progress = json.loads((folder / "worker_progress.json").read_text())
            child_pid = progress.get("child_pid")
            adopted_reaped = False
            try:
                if child_pid:
                    waited_pid, _status = os.waitpid(child_pid, 0)
                    adopted_reaped = waited_pid == child_pid
            except ChildProcessError:
                pass
            child_stat = proc_text(f"/proc/{child_pid}/stat") if child_pid else None
            child_state = child_stat.rsplit(")", 1)[1].split()[0] if child_stat else None
            group_cleanup = {"child_pid": child_pid, "child_state_after_kill": child_state,
                             "adopted_descendant_reaped": adopted_reaped,
                             "not_running": child_pid is not None and child_state is None}
            passed = passed and group_cleanup["not_running"] and adopted_reaped
        report["checks"].append({"mode": mode, "expected": expected, "actual": row["status"],
                                 "passed": passed, "controller": row, "worker": payload,
                                 "group_cleanup": group_cleanup})
        atomic_json(out / "preflight.json", report)
        print(json.dumps({"preflight": mode, "status": row["status"], "passed": passed}), flush=True)
    report["status"] = "PASS" if all(x["passed"] for x in report["checks"]) else "ENV_BLOCKED"
    atomic_json(out / "preflight.json", report)
    print(json.dumps({"preflight_status": report["status"], "solver_calls": 0,
                      "report": str(out / "preflight.json")}), flush=True)
    return 0 if report["status"] == "PASS" else 2


def load_generator(protocol):
    path = protocol.with_name("build_scan_plan.py")
    spec = importlib.util.spec_from_file_location("frozen_input_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args, config, cpu):
    preflight_report = json.loads(args.preflight.read_text())
    fp = fingerprint(args.protocol, args.source_dir, config, cpu)
    if preflight_report.get("status") != "PASS" or preflight_report.get("fingerprint") != fp:
        raise RuntimeError("matching passing no-solver preflight required; files/environment identity changed")
    env = environment(cpu, config)
    if env["environment_status"] != "PASS":
        raise RuntimeError("ENV_BLOCKED: " + "; ".join(env["environment_issues"]))
    out = new_output(args.output)
    for name in ("sources", "inputs", "attempts", "code"):
        (out / name).mkdir()
    shutil.copy2(args.protocol, out / "protocol.json")
    shutil.copy2(args.preflight, out / "preflight.json")
    for path in (Path(__file__).resolve(), WORKER):
        shutil.copy2(path, out / "code" / path.name)
    for filename in fp["source_hashes"]:
        shutil.copy2(args.source_dir / filename, out / "sources" / filename)
    generator = load_generator(args.protocol)
    generator_path = args.protocol.with_name("build_scan_plan.py")
    shutil.copy2(generator_path, out / "code" / generator_path.name)
    saved_audit = json.loads(args.protocol.with_name("generated").joinpath("input_plan_audit.json").read_text())
    saved_inputs = {r["case_id"]: r["input_sha256"] for r in saved_audit["checks"]}
    saved_plan = json.loads(args.protocol.with_name("generated").joinpath("planned_attempts.json").read_text())
    expected_attempts = saved_plan["planned_attempts"]
    plan, inputs = [], {}
    for T in config["T_values"]:
        for profile in config["profiles"]:
            for workload in config["workloads"]:
                instance, stats = generator.generate(profile, workload, T, config)
                data = generator.canonical_bytes(instance)
                if hashlib.sha256(data).hexdigest() != saved_inputs[stats["case_id"]]:
                    raise RuntimeError("generated input differs from frozen R5 audit")
                path = out / "inputs" / (stats["case_id"] + ".json")
                path.write_bytes(data)
                inputs[stats["case_id"]] = {"path": str(path), "stats": stats, "profile": profile}
                for rep in range(1, config["repetitions"] + 1):
                    plan.append({"attempt_id": f"{stats['case_id']}-r{rep}", "case_id": stats["case_id"],
                                 "repetition": rep, "input_sha256": stats["input_sha256"],
                                 "profile_id": profile["id"], "workload": workload,
                                 "T": T, "status": "NOT_RUN"})
    for actual, expected in zip(plan, expected_attempts, strict=True):
        if any(actual[k] != expected[k] for k in ("attempt_id", "case_id", "repetition", "input_sha256")):
            raise RuntimeError("attempt order/input differs from frozen R5 plan")
    if len(plan) != 48 or len(inputs) != 16:
        raise RuntimeError("unexpected frozen plan dimensions")
    atomic_json(out / "input_audit.json", {k: v["stats"] for k, v in inputs.items()})
    metadata = {"status": "READY", "created_at": now(), "protocol_id": config["protocol_id"],
                "environment": env, "fingerprint": fp, "generator_sha256": sha(generator_path),
                "planned_attempts": len(plan), "input_configurations": len(inputs),
                "formal_solver_attempts_started": 0, "no_solver_preflight": str(args.preflight),
                "resume_policy": "No overwrite or implicit resume; interrupted rows retain RUNNING plus durable events."}
    atomic_json(out / "metadata.json", metadata)
    atomic_json(out / "checkpoint.json", {"status": "READY", "attempts": plan})
    events, results = out / "events.jsonl", out / "results.jsonl"
    limits = config["limits"]
    closed = {}
    global_error = None
    budget_stopped = False
    suite_start = None
    suite_state = {"start": None, "limit": limits["suite_wall_seconds"]}
    outcomes = []
    for index, entry in enumerate(plan):
        family = entry["profile_id"] + "/" + entry["workload"]
        reason = trigger = None
        if global_error:
            reason, trigger = "NOT_RUN_SUITE_ERROR", global_error
        elif family in closed:
            reason, trigger = "NOT_RUN_RESOURCE_STOP", closed[family]
        elif budget_stopped or (suite_start is not None and
              limits["suite_wall_seconds"] - (time.monotonic() - suite_start) <
              limits["per_attempt_wall_seconds"] + limits["timeout_kill_grace_seconds"]):
            reason = "NOT_RUN_SUITE_BUDGET"
            budget_stopped = True
        if reason:
            row = {**entry, **{k: None for k in OUTPUT_FIELDS}, "status": reason, "attempted": False,
                   "complete_return": False, "trigger_attempt_id": trigger,
                   "process_wall_seconds": None, "cpu_total_seconds": None, "peak_rss_bytes": None,
                   "missing_output_reason": "Not attempted under the frozen stop policy."}
        else:
            folder = out / "attempts" / entry["attempt_id"]
            folder.mkdir()
            info = inputs[entry["case_id"]]
            profile = info["profile"]
            job = {"mode": "solve", "attempt_id": entry["attempt_id"], "attempt_dir": str(folder),
                   "input_path": info["path"], "input_sha256": entry["input_sha256"],
                   "source_dir": str(out / "sources"), "source_hashes": fp["source_hashes"],
                   "cpu": cpu, "memory_limit_bytes": limits["per_worker_address_space_bytes"],
                   "K": profile["K"], "M": profile["M"], "T": entry["T"],
                   "N": config["raw_record_count"], "ceilings": profile["ceilings"],
                   "total_weight": config["total_weight"]}
            entry["status"] = "RUNNING"
            atomic_json(out / "checkpoint.json", {"status": "RUNNING", "attempts": plan})
            append_json(events, {"event": "ATTEMPT_START", "attempt_id": entry["attempt_id"],
                                 "timestamp": now(), "index": index, "input_sha256": entry["input_sha256"]})
            print(json.dumps({"event": "ATTEMPT_START", "index": index + 1, "total": len(plan),
                              "attempt_id": entry["attempt_id"]}), flush=True)
            try:
                measurement, payload = execute(job, limits["per_attempt_wall_seconds"],
                                               limits["timeout_kill_grace_seconds"], events,
                                               suite_state=suite_state)
                suite_start = suite_state["start"]
                metadata["formal_solver_attempts_started"] += 1
                row = {**entry, **measurement, "attempted": True, "trigger_attempt_id": None,
                       "suite_elapsed_seconds": time.monotonic() - suite_start}
            except SuiteBudgetExhausted:
                budget_stopped = True
                row = {**entry, **{k: None for k in OUTPUT_FIELDS}, "status": "NOT_RUN_SUITE_BUDGET",
                       "attempted": False, "complete_return": False, "trigger_attempt_id": None,
                       "process_wall_seconds": None, "cpu_total_seconds": None, "peak_rss_bytes": None,
                       "missing_output_reason": "Less than 62 seconds remained immediately before worker spawn."}
            except Exception as exc:
                row = {**entry, **{k: None for k in OUTPUT_FIELDS}, "status": "SOLVER_ERROR",
                       "attempted": True, "complete_return": False, "trigger_attempt_id": None,
                       "process_wall_seconds": None, "cpu_total_seconds": None, "peak_rss_bytes": None,
                       "missing_output_reason": "Controller/worker launch failed: " + repr(exc)}
            if row["status"] in ("TIMEOUT", "MEMORY_LIMIT"):
                closed[family] = entry["attempt_id"]
            elif row["status"] not in ("EXACT_COMPLETE", "NOT_RUN_SUITE_BUDGET"):
                global_error = entry["attempt_id"]
        entry["status"] = row["status"]
        entry["trigger_attempt_id"] = row.get("trigger_attempt_id")
        outcomes.append(row)
        append_json(results, row)
        append_json(events, {"event": "ATTEMPT_FINAL", "attempt_id": entry["attempt_id"],
                             "timestamp": now(), "status": row["status"],
                             "trigger_attempt_id": row.get("trigger_attempt_id")})
        atomic_json(out / "checkpoint.json", {"status": "RUNNING", "attempts": plan,
                                              "suite_error_trigger": global_error})
        print(json.dumps({"event": "ATTEMPT_FINAL", "attempt_id": entry["attempt_id"],
                          "status": row["status"], "process_wall_seconds": row.get("process_wall_seconds"),
                          "peak_rss_bytes": row.get("peak_rss_bytes")}), flush=True)
    counts = {s: sum(r["status"] == s for r in outcomes) for s in sorted({r["status"] for r in outcomes})}
    metadata.update({"status": "STOPPED_ON_ERROR" if global_error else "FINISHED",
                     "finished_at": now(), "status_counts": counts,
                     "suite_elapsed_seconds": None if suite_start is None else time.monotonic() - suite_start,
                     "closed_families": closed, "suite_error_trigger": global_error})
    atomic_json(out / "metadata.json", metadata)
    atomic_json(out / "checkpoint.json", {"status": metadata["status"], "attempts": plan})
    atomic_json(out / "summary.json", metadata)
    print(json.dumps({"event": "SUITE_FINAL", "status": metadata["status"], "counts": counts,
                      "output": str(out)}), flush=True)
    return 1 if global_error else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "run"))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--cpu", type=int)
    args = parser.parse_args()
    args.protocol, args.source_dir = args.protocol.resolve(), args.source_dir.resolve()
    config = json.loads(args.protocol.read_text())
    if config["protocol_id"] != "SafeRefresh-R5-T-v1":
        parser.error("only the frozen R5-v1 protocol is supported")
    cpu = min(os.sched_getaffinity(0)) if args.cpu is None else args.cpu
    if args.command == "preflight":
        return preflight(args, config, cpu)
    if args.preflight is None:
        parser.error("run requires --preflight /absolute/path/preflight.json")
    args.preflight = args.preflight.resolve()
    return run(args, config, cpu)


if __name__ == "__main__":
    sys.exit(main())
