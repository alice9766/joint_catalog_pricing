#!/usr/bin/env python3
"""One resource-limited task. Solver imports occur only in mode=solve."""
from __future__ import annotations

import bisect
import dataclasses
from fractions import Fraction as F
import hashlib
import importlib
import json
import math
import mmap
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time
import traceback


class InputInvalid(Exception):
    pass


class ReconstructionFail(Exception):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encode(value):
    if isinstance(value, F):
        return str(value)
    if dataclasses.is_dataclass(value):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "-Infinity" if value < 0 else "Infinity"
    return value


def write_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as f:
        json.dump(encode(data), f, ensure_ascii=False, sort_keys=True, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def configure(job):
    limit = int(job["memory_limit_bytes"])
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    if resource.getrlimit(resource.RLIMIT_AS) != (limit, limit):
        raise RuntimeError("RLIMIT_AS did not take effect")
    os.sched_setaffinity(0, {int(job["cpu"])})
    if os.sched_getaffinity(0) != {int(job["cpu"])}:
        raise RuntimeError("single-CPU affinity did not take effect")
    # If the controller vanishes, do not leave an unmonitored solver running.
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG")
        if os.getppid() != int(job["parent_pid"]):
            os.kill(os.getpid(), signal.SIGKILL)
    except AttributeError as exc:
        raise RuntimeError("Linux parent-death protection unavailable") from exc
    return {"pid": os.getpid(), "pgid": os.getpgrp(),
            "rlimit_as": list(resource.getrlimit(resource.RLIMIT_AS)),
            "affinity": sorted(os.sched_getaffinity(0)),
            "thread_environment": {k: os.environ.get(k) for k in job["thread_vars"]}}


def validate_result(instance, result, job):
    def need(condition, message):
        if not condition:
            raise ReconstructionFail(message)
    K = int(job["K"])
    positive = {b.theta for b in instance.buyers if b.theta > 0}
    thresholds = positive | {F(0)}
    ceilings = sorted({instance.safety_ceiling[b.trust] for b in instance.buyers
                       if b.theta > 0 and instance.safety_ceiling[b.trust] >= 0})
    need(len(thresholds) == job["T"], "input T changed")
    need(result.threshold_count == len(thresholds), "returned threshold count mismatch")
    need(list(result.active_ceilings) == ceilings, "returned qualification groups mismatch")
    need(len(result.menu) <= K and len(set(result.menu)) == len(result.menu), "menu capacity/uniqueness")
    need(len(result.menu) == len(result.prices), "menu/price length")
    need(all(isinstance(p, (int, F)) and p >= 0 for p in result.prices), "nonexact or negative price")
    need(all(isinstance(m, int) and 0 <= m < instance.M for m in result.menu), "invalid menu label")
    need(isinstance(result.profit_supremum, (int, F)), "nonrational finite objective")
    prices = dict(zip(result.menu, result.prices))
    assignments, demand = [], {m: F(0) for m in result.menu}
    for buyer in instance.buyers:
        if buyer.theta == 0:
            choice = -1
        else:
            options = [(F(0), F(0), F(0), -1)]
            options += [(buyer.theta * instance.qualities[m] - prices[m],
                         prices[m], instance.qualities[m], m) for m in result.menu
                        if m <= instance.safety_ceiling[buyer.trust]]
            choice = max(options)[3]
        assignments.append(choice)
        if choice != -1:
            demand[choice] += buyer.weight
    replay = sum(((prices[m] - instance.marginal_costs[m]) * demand[m]
                  - instance.fixed_costs[m] for m in result.menu), F(0))
    need(tuple(assignments) == tuple(result.assignment), "external canonical assignment mismatch")
    need(replay == result.verified_profit == result.profit_supremum, "canonical profit mismatch")
    need(result.reconstruction_gap == 0, "nonzero reconstruction gap")
    frontier, internal = result.capacity_frontier, result.exact_size_profit
    need(len(frontier) == K + 1 and len(internal) == K + 1, "capacity array length")
    need(frontier[0] == 0 and internal[0] == 0, "empty capacity state")
    best = F(0)
    for k in range(1, K + 1):
        value = internal[k]
        need(isinstance(value, (int, F)) or value == -float("inf"), "nonexact internal state")
        best = max(best, value)
        need(frontier[k] == best, "capacity not prefix maximum of internal states")
    need(frontier[-1] == result.profit_supremum, "returned capacity objective mismatch")
    need(all(isinstance(v, (int, F)) for v in frontier), "nonexact capacity value")
    need(len(result.edges) == len(result.menu), "edge count mismatch")
    need({e.child for e in result.edges} == set(result.menu), "edge children mismatch")
    P = ceilings[-1] + 1
    def birth(m):
        return len(ceilings) if m == P else bisect.bisect_left(ceilings, m)
    for e in result.edges:
        need(e.threshold in thresholds, "edge slope absent from raw threshold set")
        need(e.parent == -1 or e.parent in prices, "invalid parent")
        need(e.parent < e.child < e.boundary <= P, "edge label order")
        need(e.start_group == birth(e.child) and e.end_group == birth(e.boundary) - 1,
             "edge lifetime boundary mismatch")
        need(e.start_group <= e.end_group, "empty edge lifetime")
        pa = F(0) if e.parent == -1 else prices[e.parent]
        qa = F(0) if e.parent == -1 else instance.qualities[e.parent]
        ca = F(0) if e.parent == -1 else instance.marginal_costs[e.parent]
        need(prices[e.child] == pa + e.threshold * (instance.qualities[e.child] - qa),
             "edge price reconstruction mismatch")
        tail = sum((b.weight for b in instance.buyers if b.theta > 0 and
                    b.theta >= e.threshold and e.start_group <=
                    ceilings.index(instance.safety_ceiling[b.trust]) <= e.end_group), F(0))
        expected = (e.threshold * (instance.qualities[e.child] - qa)
                    - (instance.marginal_costs[e.child] - ca)) * tail - instance.fixed_costs[e.child]
        need(e.contribution == expected, "edge contribution mismatch")
    need(sum((e.contribution for e in result.edges), F(0)) == replay, "edge decomposition mismatch")
    need(len(result.group_hull_paths) == len(ceilings), "group path count")
    need(len(result.group_edge_thresholds) == len(ceilings), "group slope path count")
    for b in range(len(ceilings)):
        active = [e for e in result.edges if e.start_group <= b <= e.end_group]
        need(len({e.parent for e in active}) == len(active), "two active children")
        by_parent = {e.parent: e for e in active}
        path, slopes, a = [], [], -1
        while a in by_parent:
            e = by_parent[a]
            path.append(e.child)
            slopes.append(e.threshold)
            a = e.child
        need(len(path) == len(active), "active edges disconnected")
        need(tuple(path) == tuple(result.group_hull_paths[b]), "group path mismatch")
        need(tuple(slopes) == tuple(result.group_edge_thresholds[b]), "group slopes mismatch")
    return {"external_canonical_replay": "PASS", "capacity_frontier": "PASS",
            "thresholds_and_groups": "PASS", "edge_and_path_certificate": "PASS",
            "replay_profit": replay, "selected_nodes": len(result.menu),
            "selected_threshold_count": len({e.threshold for e in result.edges}),
            "total_served_weight": sum(demand.values(), F(0))}


def solve(job, progress):
    for filename, expected in job["source_hashes"].items():
        if sha(Path(job["source_dir"]) / filename) != expected:
            raise InputInvalid("source snapshot hash mismatch: " + filename)
    if sha(job["input_path"]) != job["input_sha256"]:
        raise InputInvalid("input file hash mismatch")
    raw = json.loads(Path(job["input_path"]).read_text())
    # Decode before Instance construction/validate, with no float roundtrip.
    try:
        numeric = {key: tuple(F(v) for v in raw[key]) for key in
                   ("qualities", "fixed_costs", "marginal_costs", "price_grid")}
        decoded = [(int(b["trust"]), F(b["theta"]), F(b["weight"]), b.get("name", ""))
                   for b in raw["buyers"]]
        ceilings = tuple(int(v) for v in raw["safety_ceiling"])
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        raise InputInvalid("invalid rational input: " + str(exc)) from exc
    sys.path.insert(0, str(job["source_dir"]))
    types = importlib.import_module("exact_srmd")
    module = importlib.import_module("persistent_hull_tree_dp")
    instance = types.Instance(**numeric, safety_ceiling=ceilings,
                              buyers=tuple(types.BuyerType(*b) for b in decoded), name=raw["name"])
    try:
        instance.validate()
        assert len(instance.buyers) == job["N"]
        assert instance.M == job["M"]
        assert list(ceilings) == job["ceilings"]
        assert len({b.theta for b in instance.buyers if b.theta > 0} | {F(0)}) == job["T"]
        assert sum((b.weight for b in instance.buyers), F(0)) == F(job["total_weight"])
    except (AssertionError, ValueError, TypeError) as exc:
        raise InputInvalid("input contract mismatch: " + str(exc)) from exc
    progress("SOLVER_STARTED")
    start = time.perf_counter()
    try:
        result = module.solve_persistent_hull_tree(instance, int(job["K"]), exact=True)
    except AssertionError as exc:
        raise ReconstructionFail(str(exc)) from exc
    solver_wall = time.perf_counter() - start
    progress("SOLVER_RETURNED")
    validation_start = time.perf_counter()
    validation = validate_result(instance, result, job)
    validation_wall = time.perf_counter() - validation_start
    certificate_path = Path(job["attempt_dir"]) / "certificate.json"
    write_json(certificate_path, result)
    return {"status": "EXACT_COMPLETE", "solver_wall_seconds": solver_wall,
            "external_validation_wall_seconds": validation_wall,
            "validation": validation, "certificate_path": str(certificate_path),
            "certificate_sha256": sha(certificate_path),
            "objective": result.profit_supremum,
            "capacity_frontier": result.capacity_frontier,
            "reconstruction_gap": result.reconstruction_gap,
            "state_count": result.state_count, "threshold_count": result.threshold_count,
            "optimality_gap": None, "incumbent": None, "upper_bound": None,
            "complete_return": True, "solver_called": True}


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    attempt_dir = Path(job["attempt_dir"])
    result_path = attempt_dir / "worker_result.json"
    solver_called = False
    def progress(stage, **extra):
        nonlocal solver_called
        write_json(attempt_dir / "worker_progress.json", {"stage": stage,
                   "time_ns": time.time_ns(), "pid": os.getpid(),
                   "solver_called": solver_called or stage == "SOLVER_STARTED", **extra})
        if stage == "SOLVER_STARTED":
            solver_called = True
    reserve = None
    try:
        controls = configure(job)
        reserve = bytearray(1024 * 1024)
        progress("RESOURCE_CONTROLS_READY", controls=controls)
        mode = job["mode"]
        if mode == "solve":
            result = solve(job, progress)
        elif mode == "probe":
            blocked = False
            try:
                allocation = mmap.mmap(-1, int(job["memory_limit_bytes"]) + 4096)
                allocation.close()
            except (OSError, MemoryError):
                blocked = True
            if not blocked:
                raise RuntimeError("oversized mmap succeeded despite RLIMIT_AS")
            result = {"status": "PREFLIGHT_OK", "oversized_mmap_blocked": blocked}
        elif mode == "dummy_success":
            result = {"status": "DUMMY_OK", "value": "1/3"}
        elif mode == "dummy_error":
            raise RuntimeError("intentional no-solver error branch")
        elif mode == "dummy_memory":
            allocation = bytearray(int(job["memory_limit_bytes"]) + 4096)
            raise RuntimeError("oversized bytearray unexpectedly succeeded")
        elif mode == "dummy_timeout":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            child = subprocess.Popen([sys.executable, "-c",
                "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(300)"])
            progress("DUMMY_GROUP_READY", child_pid=child.pid, pgid=os.getpgrp())
            time.sleep(300)
            raise RuntimeError("dummy timeout was not enforced")
        else:
            raise InputInvalid("unknown worker mode")
        result.update({"controls": controls, "solver_called": solver_called,
                       "solver_module_imported": "persistent_hull_tree_dp" in sys.modules})
        write_json(result_path, result)
        progress("COMPLETE", status=result["status"])
        return 0
    except BaseException as exc:
        reserve = None
        if isinstance(exc, MemoryError):
            status, code = "MEMORY_LIMIT", 4
        elif isinstance(exc, InputInvalid):
            status, code = "INPUT_INVALID", 2
        elif isinstance(exc, ReconstructionFail):
            status, code = "RECONSTRUCTION_FAIL", 5
        else:
            status, code = "SOLVER_ERROR", 3
        traceback.print_exc()
        try:
            write_json(result_path, {"status": status, "exception_type": type(exc).__name__,
                       "message": str(exc), "complete_return": False, "solver_called": solver_called,
                       "solver_module_imported": "persistent_hull_tree_dp" in sys.modules})
            progress("ERROR", status=status)
        except BaseException:
            pass
        return code


if __name__ == "__main__":
    sys.exit(main())
