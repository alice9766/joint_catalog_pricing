"""Pure bookkeeping rules; imports no worker, solver, or process launcher."""
from collections import Counter, defaultdict
from statistics import median

OUTPUT_FIELDS = ("solver_wall_seconds", "external_validation_wall_seconds", "objective",
                 "capacity_frontier", "reconstruction_gap", "state_count", "threshold_count",
                 "optimality_gap", "incumbent", "upper_bound", "certificate_path",
                 "certificate_sha256", "validation")
STOP_STATUSES = {"INPUT_INVALID", "RECONSTRUCTION_FAIL", "SOLVER_ERROR", "ENV_KILLED",
                 "LAUNCH_ERROR", "ENV_BLOCKED", "INTERRUPTED", "STORAGE_ERROR"}


def classify(elapsed, deadline, exit_code, payload, interrupted=False, timed_out=False):
    """Full return first observed at the deadline is conservatively censored."""
    if interrupted:
        return "INTERRUPTED", False
    if timed_out or (elapsed is not None and elapsed >= deadline):
        return "TIMEOUT", False
    if exit_code is None:
        return "LAUNCH_ERROR", False
    if exit_code < 0:
        return "ENV_KILLED", False
    if not isinstance(payload, dict):
        return "SOLVER_ERROR", False
    status = payload.get("status")
    if status == "MEMORY_LIMIT":
        confirmed = payload.get("exception_type") == "MemoryError" and exit_code == 4
        return ("MEMORY_LIMIT" if confirmed else "SOLVER_ERROR"), False
    if status == "EXACT_COMPLETE":
        complete = exit_code == 0 and payload.get("complete_return") is True
        return ("EXACT_COMPLETE" if complete else "SOLVER_ERROR"), complete
    if status in ("PREFLIGHT_OK", "DUMMY_OK"):
        return (status if exit_code == 0 else "SOLVER_ERROR"), False
    return (status if status in STOP_STATUSES else "SOLVER_ERROR"), False


def resumable(row):
    return (row.get("status", "").startswith("NOT_RUN")
            and not row.get("launch_reserved") and not row.get("process_launched"))


def recover(row, committed=None):
    """A committed final record wins; reservations without it are never rerun."""
    if committed is not None:
        if committed["attempt_id"] != row["attempt_id"]:
            raise ValueError("final record identity mismatch")
        for key in ("case_id", "input_sha256", "repetition"):
            if key in row and committed.get(key) != row[key]:
                raise ValueError("final record plan metadata mismatch: " + key)
        return dict(committed)
    if row.get("launch_reserved") or row.get("status") in ("RUNNING", "RESERVED"):
        if row.get("status") in ("RUNNING", "RESERVED"):
            return dict(row, status="INTERRUPTED", complete_return=False,
                        missing_output_reason="Controller vanished after launch reservation; partial outputs are diagnostic only.",
                        **{k: None for k in OUTPUT_FIELDS})
    return dict(row)


def recovered_launch_evidence(row, worker_progress=None, event_pid=None):
    """Unknown launch after a crash is distinct from proven no-launch."""
    row = dict(row)
    pid = event_pid or (worker_progress or {}).get("pid")
    if pid:
        row.update(process_launched=True, pid=pid, launch_evidence="WORKER_STARTED event or worker_progress")
    elif row.get("launch_reserved") and not row.get("process_launched"):
        row.update(process_launched=None, launch_evidence="UNKNOWN: durable reservation without durable process evidence")
    if worker_progress:
        row.update(worker_progress=worker_progress, solver_started=bool(worker_progress.get("solver_called")))
    return row


def stats(values):
    values = list(values)
    return {"count": len(values), "min": min(values) if values else None,
            "median": median(values) if values else None, "max": max(values) if values else None}


def aggregate(rows):
    """Only complete returns contribute times; every planned row stays in counts."""
    complete = [r for r in rows if r["status"] == "EXACT_COMPLETE" and r.get("complete_return")]
    cases, envs = defaultdict(list), defaultdict(list)
    for r in rows:
        cases[r["case_id"]].append(r)
        envs[r.get("environment_id") or "NOT_STARTED"].append(r)
    return {
        "planned_attempts": len(rows),
        "launch_reservations": sum(bool(r.get("launch_reserved")) for r in rows),
        "processes_launched": sum(bool(r.get("process_launched")) for r in rows),
        "launches_unknown_after_reservation": sum(r.get("launch_reserved",False) and r.get("process_launched") is None for r in rows),
        "solver_started": sum(bool(r.get("solver_started")) for r in rows),
        "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
        "complete_attempts": len(complete),
        "completion_thresholds": {str(t): sum(r["process_wall_seconds"] <= t for r in complete)
                                  for t in (30, 60, 120, 300)},
        "all_three_complete_inputs": sum(len(v) == 3 and all(r["status"] == "EXACT_COMPLETE" and r.get("complete_return") for r in v)
                                         for v in cases.values()),
        "successful_process_wall_seconds": stats(r["process_wall_seconds"] for r in complete),
        "rss_mib_by_status": {s: stats(r["peak_rss_bytes"] / 2**20 for r in rows
                                      if r["status"] == s and r.get("peak_rss_bytes") is not None)
                              for s in sorted({r["status"] for r in rows})},
        "per_input": [{"case_id": k, "planned": len(v), "statuses": [r["status"] for r in v],
                       "complete_count": sum(r["status"] == "EXACT_COMPLETE" and bool(r.get("complete_return")) for r in v),
                       "successful_process_wall_seconds": stats(r["process_wall_seconds"] for r in v
                                                                 if r["status"] == "EXACT_COMPLETE" and r.get("complete_return")),
                       "environment_ids": sorted({r.get("environment_id") or "NOT_STARTED" for r in v})}
                      for k, v in cases.items()],
        "per_environment": [{"environment_id": k, "planned_rows_assigned": len(v),
                             "status_counts": dict(Counter(r["status"] for r in v)),
                             "complete_attempts": sum(r["status"] == "EXACT_COMPLETE" and bool(r.get("complete_return")) for r in v),
                             "successful_process_wall_seconds": stats(r["process_wall_seconds"] for r in v
                                                                        if r["status"] == "EXACT_COMPLETE" and r.get("complete_return")),
                             "rss_mib_by_status": {s: stats(r["peak_rss_bytes"] / 2**20 for r in v
                                                            if r["status"] == s and r.get("peak_rss_bytes") is not None)
                                                    for s in sorted({r["status"] for r in v})},
                             "completion_thresholds": {str(t): sum(r["status"] == "EXACT_COMPLETE" and r.get("complete_return", False)
                                                                        and r["process_wall_seconds"] <= t for r in v)
                                                       for t in (30, 60, 120, 300)}} for k, v in envs.items()],
        "timing_policy": "TIMEOUT is right-censored at 300s, excluded from successful-duration statistics.",
        "historical_results_included": False,
        "pooling_note": "Cross-environment totals describe completion only; use per_environment durations/RSS for performance claims.",
    }
