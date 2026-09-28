#!/usr/bin/env python3
"""Rerun one threshold-scan input or the full scan in a new output directory."""
from __future__ import annotations

import argparse
from fractions import Fraction as F
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "experiments/threshold"
RUN = DATA / "original"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--case", help="For example A-independent-T20")
    choice.add_argument("--all", action="store_true", help="All 16 inputs; three repeats each by default")
    choice.add_argument("--smoke", action="store_true", help="Solve A-independent-T20 once")
    parser.add_argument("--output", required=True, type=Path, help="New output directory; never overwrites recorded results")
    parser.add_argument("--repetitions", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--cpu", type=int, help="Allowed CPU index; defaults to the lowest allowed CPU")
    args = parser.parse_args()
    if args.smoke:
        args.case, args.repetitions = "A-independent-T20", 1
    if sys.platform != "linux" or not hasattr(os, "sched_setaffinity"):
        parser.error("Linux is required for the preserved resource controls and RSS measurement")
    config = json.loads((DATA / "protocol/t_scan_v1.json").read_text())
    plans = json.loads((DATA / "protocol/generated/planned_attempts.json").read_text())["planned_attempts"]
    selected = [p for p in plans if (args.all or p["case_id"] == args.case) and p["repetition"] <= args.repetitions]
    if not selected:
        parser.error("Unknown case; inspect experiments/threshold/original/inputs")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runner = load("preserved_threshold_controller", RUN / "code/t_scan_runner.py")
    cpu = min(os.sched_getaffinity(0)) if args.cpu is None else args.cpu
    environment = runner.environment(cpu, config)
    runner.atomic_json(output / "environment.json", environment)
    if environment["environment_status"] != "PASS":
        runner.atomic_json(output / "summary.json", {"status": "ENV_BLOCKED", "issues": environment["environment_issues"]})
        raise SystemExit("Resource precheck failed; see environment.json")
    source_hashes = runner.source_hashes(config, RUN / "sources")
    # The preserved probe verifies address-space limiting and CPU affinity without a solver.
    probe_dir = output / "resource_probe"
    probe_dir.mkdir()
    limits = config["limits"]
    probe_job = {"mode": "probe", "attempt_dir": str(probe_dir), "cpu": cpu,
                 "memory_limit_bytes": limits["per_worker_address_space_bytes"]}
    probe, _payload = runner.execute(probe_job, 10, limits["timeout_kill_grace_seconds"],
                                     worker_path=RUN / "code/t_scan_worker.py")
    if probe["status"] != "PREFLIGHT_OK":
        runner.atomic_json(output / "summary.json", {"status": "ENV_BLOCKED", "probe_status": probe["status"]})
        raise SystemExit("Resource probe failed")
    profiles = {p["id"]: p for p in config["profiles"]}
    audit = {p["case_id"]: p for p in json.loads((RUN / "input_audit.json").read_text()).values()}
    for folder in ("inputs", "attempts"):
        (output / folder).mkdir()
    for case in {p["case_id"] for p in selected}:
        shutil.copy2(RUN / "inputs" / (case + ".json"), output / "inputs" / (case + ".json"))
    rows, blocked_families, suite_error = [], set(), False
    suite_state = {"start": None, "limit": limits["suite_wall_seconds"]}
    for plan in selected:
        case_id = plan["case_id"]
        profile_id, workload, _T = case_id.split("-")
        profile, inp = profiles[profile_id], audit[case_id]
        family = (profile_id, workload)
        row = dict(plan)
        remaining = limits["suite_wall_seconds"] if suite_state["start"] is None else (
            limits["suite_wall_seconds"] - (time.monotonic() - suite_state["start"]))
        if suite_error:
            row.update(status="NOT_RUN_SUITE_ERROR", attempted=False)
        elif family in blocked_families:
            row.update(status="NOT_RUN_RESOURCE_STOP", attempted=False)
        elif remaining < limits["per_attempt_wall_seconds"] + limits["timeout_kill_grace_seconds"]:
            row.update(status="NOT_RUN_SUITE_BUDGET", attempted=False)
        else:
            folder = output / "attempts" / plan["attempt_id"]
            folder.mkdir()
            job = {"mode": "solve", "attempt_id": plan["attempt_id"], "attempt_dir": str(folder),
                "source_dir": str(RUN / "sources"), "source_hashes": source_hashes,
                "input_path": str(output / "inputs" / (case_id + ".json")),
                "input_sha256": plan["input_sha256"], "cpu": cpu,
                "memory_limit_bytes": limits["per_worker_address_space_bytes"],
                "M": profile["M"], "K": profile["K"], "T": inp["T"], "N": inp["N_raw"],
                "ceilings": profile["ceilings"], "total_weight": config["total_weight"]}
            try:
                result, _payload = runner.execute(job, limits["per_attempt_wall_seconds"],
                    limits["timeout_kill_grace_seconds"], worker_path=RUN / "code/t_scan_worker.py", suite_state=suite_state)
                row.update(result, attempted=True)
            except runner.SuiteBudgetExhausted:
                row.update(status="NOT_RUN_SUITE_BUDGET", attempted=False)
            if row["status"] in ("TIMEOUT", "MEMORY_LIMIT"):
                blocked_families.add(family)
            elif row["status"] not in ("EXACT_COMPLETE", "NOT_RUN_SUITE_BUDGET"):
                suite_error = True
            if row["status"] == "EXACT_COMPLETE":
                original = json.loads((RUN / "attempts" / plan["attempt_id"] / "certificate.json").read_text())
                row["recorded_objective_match"] = F(row["objective"]) == F(original["profit_supremum"])
                row["recorded_frontier_match"] = list(map(F, row["capacity_frontier"])) == list(map(F, original["capacity_frontier"]))
                if not row["recorded_objective_match"] or not row["recorded_frontier_match"]:
                    suite_error = True
        rows.append(row)
        runner.append_json(output / "results.jsonl", row)
        print(json.dumps({"attempt_id": plan["attempt_id"], "status": row["status"]}), flush=True)
    complete = [r for r in rows if r["status"] == "EXACT_COMPLETE"]
    summary = {"status": "PASS" if len(complete) == len(rows) and not suite_error else "INCOMPLETE_OR_FAILED",
        "scope": "new_solver_runs", "requested_attempts": len(rows), "completed_attempts": len(complete),
        "objective_matches": sum(r["recorded_objective_match"] for r in complete),
        "frontier_matches": sum(r["recorded_frontier_match"] for r in complete),
        "limits": limits, "source_hashes": source_hashes,
        "timing_note": "New observations use this machine and are separate from the historical published measurements."}
    runner.atomic_json(output / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
