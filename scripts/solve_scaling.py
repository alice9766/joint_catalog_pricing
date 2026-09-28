#!/usr/bin/env python3
"""Run a joint-scaling input or the complete declared scan in a fresh directory.

Linux is required for CPU affinity, RLIMIT_AS and wait4 peak-RSS accounting.
The original optimizer, worker and process controller are used unchanged.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

from verify_scaling import DATA, read, replay, require, sha, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--smoke", action="store_true", help="One solve of the original M=4, T=5 smoke input.")
    selection.add_argument("--case", help="Exact case ID, e.g. MAIN-M08-independent-T20.")
    selection.add_argument("--all", action="store_true", help="All 42 formal attempts, preserving declared order.")
    selection.add_argument("--list", action="store_true", help="List the 14 formal configurations.")
    parser.add_argument("--output", type=Path, help="New result directory, required except with --list.")
    parser.add_argument("--cpu", type=int, help="Allowed logical CPU; defaults to the lowest allowed CPU.")
    parser.add_argument("--repetitions", type=int, choices=(1, 2, 3),
                        help="For --case only; default is all three repetitions.")
    args = parser.parse_args()
    protocol = read(DATA/"protocol/protocol_frozen.json")
    plan = read(DATA/"protocol/plan.json")["planned_attempts"]
    if args.list:
        for case in sorted({r["case_id"] for r in plan}):
            print(case)
        return 0
    require(args.output is not None, "--output is required")
    require(sys.platform.startswith("linux"), "Linux is required")
    require(not sys.flags.optimize, "Do not use Python -O: worker checks must remain enabled")
    require(args.repetitions is None or args.case is not None, "--repetitions applies only to --case")
    allowed = os.sched_getaffinity(0)
    cpu = min(allowed) if args.cpu is None else args.cpu
    require(cpu in allowed, "requested CPU is outside the allowed affinity mask")
    out = args.output.resolve()
    require(not out.exists(), "output already exists; choose a new directory")
    if args.smoke:
        selected = [protocol["smoke_case"]]
    elif args.case:
        selected = [r for r in plan if r["case_id"] == args.case and r["repetition"] <= (args.repetitions or 3)]
        require(bool(selected), "unknown case ID; use --list")
    else:
        selected = plan
    # Import only the archived control layer here. It imports no optimizer in
    # the parent; the unmodified worker performs all solver imports.
    sys.path.insert(0, str(DATA/"code"))
    import run300 as controller
    source_hashes = controller.legacy.source_hashes(protocol, DATA/"vendor")
    manifest = read(DATA/"source_manifest.json")
    for entry in manifest["files"]:
        if entry["path"].startswith(("code/", "vendor/")):
            require(sha(DATA/entry["path"]) == entry["sha256"], "original source was modified")
    out.mkdir(parents=True)
    environment = controller.legacy.environment(cpu, protocol)
    write(out/"environment.json", environment)
    require(environment["environment_status"] == "PASS", "environment does not support the declared resource limits")
    write(out/"selected_plan.json", {"attempts": selected, "limits": protocol["limits"],
          "note": "New reproduction run; does not replace original recorded timing."})
    attempts = out/"attempts"
    attempts.mkdir()
    rows, checks = [], []
    stopped = False
    for entry in selected:
        if stopped:
            rows.append(dict(entry, status="NOT_RUN_SUITE_ERROR"))
            continue
        folder = attempts/entry["attempt_id"]
        folder.mkdir()
        job = {"mode": "solve", "attempt_id": entry["attempt_id"], "attempt_dir": str(folder),
               "input_path": str(DATA/entry["input_file"]), "input_sha256": entry["input_sha256"],
               "source_dir": str(DATA/"vendor"), "source_hashes": source_hashes,
               "cpu": cpu, "memory_limit_bytes": 4*1024**3, "K": entry["K"], "M": entry["M"],
               "T": entry["T"], "N": entry["N_raw"], "ceilings": entry["ceilings"],
               "total_weight": entry["total_weight"]}
        print(json.dumps({"event": "START", "attempt_id": entry["attempt_id"]}), flush=True)
        result, _ = controller.execute(job, 300, out/"events.jsonl", DATA/"code/t_scan_worker.py")
        row = dict(entry, **result)
        if result["status"] == "EXACT_COMPLETE":
            check = replay(read(DATA/entry["input_file"]), read(folder/"certificate.json"), entry["K"])
            checks.append(dict(attempt_id=entry["attempt_id"], **check))
        elif result["status"] not in ("TIMEOUT", "MEMORY_LIMIT"):
            stopped = True
        write(folder/"final.json", row)
        rows.append(row)
        write(out/"results.json", {"attempts": rows})
        print(json.dumps({"event": "FINISH", "attempt_id": entry["attempt_id"], "status": row["status"],
                          "process_wall_seconds": row["process_wall_seconds"]}), flush=True)
    counts = dict(Counter(r["status"] for r in rows))
    completed = counts.get("EXACT_COMPLETE", 0)
    summary = {"status": "PASS" if completed == len(selected) else "INCOMPLETE",
               "selected_attempts": len(selected), "completed_attempts": completed, "status_counts": counts,
               "independent_replays": len(checks), "deadline_seconds": 300,
               "address_space_limit_bytes": 4*1024**3, "worker_cpu": cpu,
               "same_archived_optimizer_and_worker": True,
               "scope": "New execution on the current machine; these timings are not substituted into Table II."}
    write(out/"results.json", {"attempts": rows})
    write(out/"certificate_replay.json", {"checks": checks})
    write(out/"summary.json", summary)
    print(json.dumps(summary))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
