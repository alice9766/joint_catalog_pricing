#!/usr/bin/env python3
"""Prepared, not executed: nine whole-method 300-second application runs.

Invocation requires --run and a new --output directory.  No default action
solves anything.  Uses the archived exact PHT, not a new pricing algorithm.
"""
from pathlib import Path
import argparse
import hashlib
import itertools
import json
import os
import platform
import signal
import subprocess
import sys
import time
from fractions import Fraction

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jsonify(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [jsonify(x) for x in value]
    if isinstance(value, dict):
        return {str(k): jsonify(v) for k, v in value.items()}
    return value


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(jsonify(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def replay(raw, menu, prices):
    """Independent arithmetic/choice replay, not an optimality certificate."""
    ins = raw["instance"]
    q = list(map(Fraction, ins["qualities"]))
    h = list(map(Fraction, ins["fixed_costs"]))
    c = list(map(Fraction, ins["marginal_costs"]))
    menu = tuple(menu)
    prices = tuple(map(Fraction, prices))
    assert len(menu) == len(prices) == len(set(menu)) and len(menu) <= raw["K"]
    assert all(0 <= m < len(q) for m in menu) and all(p >= 0 for p in prices)
    lookup = dict(zip(menu, prices))
    group_demands = {g: {"products": [Fraction(0)]*len(q), "exit": Fraction(0)} for g in range(2)}
    revenue = service = Fraction(0)
    assignments = []
    for b in ins["buyers"]:
        theta, weight, g = Fraction(b["theta"]), Fraction(b["weight"]), b["trust"]
        candidates = [(Fraction(0), Fraction(0), Fraction(0), -1)]
        for m, p in zip(menu, prices):
            if m <= ins["safety_ceiling"][g]:
                candidates.append((theta*q[m]-p, p, q[m], m))
        choice = -1 if theta == 0 else max(candidates)[3]
        utility = Fraction(0) if choice == -1 else theta*q[choice]-lookup[choice]
        if choice == -1:
            group_demands[g]["exit"] += weight
        else:
            assert choice <= ins["safety_ceiling"][g]
            group_demands[g]["products"][choice] += weight
            revenue += weight*lookup[choice]
            service += weight*c[choice]
        assignments.append({"type": b["name"], "trust": g, "theta": theta,
                            "weight": weight, "choice": choice, "utility": utility})
    activation = sum((h[m] for m in menu), Fraction(0))
    profit = revenue-service-activation
    assert sum((sum(g["products"], Fraction(0))+g["exit"] for g in group_demands.values()), Fraction(0)) == 80
    return {"menu": menu, "prices": prices, "assignments": assignments,
            "group_demands": group_demands, "revenue": revenue, "service_cost": service,
            "activation_cost": activation, "profit": profit,
            "accounting_identity_pass": profit == revenue-service-activation,
            "capacity_and_eligibility_pass": True,
            "zero_demand_activated_products_still_charged": True}


def worker(args):
    # Deferred imports ensure preparation/static checks never execute a solver.
    sys.path.insert(0, str(ROOT / "vendor"))
    from exact_srmd import BuyerType, Instance
    from persistent_hull_tree_dp import solve_persistent_hull_tree
    from fixed_menu_pricing import solve_fixed_active_menu
    input_path = Path(args.input)
    raw = json.loads(input_path.read_text())
    v = raw["instance"]
    ins = Instance(qualities=tuple(map(Fraction, v["qualities"])),
                   fixed_costs=tuple(map(Fraction, v["fixed_costs"])),
                   marginal_costs=tuple(map(Fraction, v["marginal_costs"])),
                   safety_ceiling=tuple(v["safety_ceiling"]),
                   price_grid=tuple(map(Fraction, v["price_grid"])),
                   buyers=tuple(BuyerType(trust=b["trust"], theta=Fraction(b["theta"]),
                                          weight=Fraction(b["weight"]), name=b["name"]) for b in v["buyers"]),
                   name=v["name"])
    ins.validate()
    trace = []
    if args.method == "FULL_PHT_EXACT":
        result = solve_persistent_hull_tree(ins, raw["K"], exact=True)
        menu, prices, value = result.menu, result.prices, result.profit_supremum
        extra = {"capacity_frontier": result.capacity_frontier,
                 "pht_internal_reconstruction_gap": result.reconstruction_gap,
                 "pht_state_count": result.state_count,
                 "pht_threshold_count": result.threshold_count}
        internal_solves = 1
    else:
        cache = {}
        def price(chosen):
            key = tuple(sorted(chosen))
            if key not in cache:
                cache[key] = solve_fixed_active_menu(ins, key, exact=True)
            return cache[key]
        chosen = ()
        best = price(())
        if args.method == "GREEDY_ADD_ONE_EXACT":
            for step in range(min(raw["K"], ins.M)):
                winner = chosen
                winner_result = best
                for m in range(ins.M):
                    if m in chosen:
                        continue
                    candidate = tuple(sorted(chosen+(m,)))
                    result = price(candidate)
                    trace.append({"step": step+1, "candidate": candidate,
                                  "profit": result.profit_supremum})
                    if result.profit_supremum > winner_result.profit_supremum:
                        winner, winner_result = candidate, result
                if winner == chosen:
                    break
                chosen, best = winner, winner_result
        elif args.method == "ENDPOINTS_ONLY_EXACT":
            for size in range(1, min(raw["K"], 2)+1):
                for candidate in itertools.combinations((0, 2), size):
                    result = price(candidate)
                    trace.append({"candidate": candidate, "profit": result.profit_supremum})
                    if result.profit_supremum > best.profit_supremum:
                        chosen, best = candidate, result
        else:
            raise ValueError(args.method)
        menu, prices, value = best.menu, best.prices, best.profit_supremum
        extra = {"search_trace": trace, "fixed_menu_cache_entries": len(cache)}
        internal_solves = sum(bool(k) for k in cache) # Empty pricing uses no DP.
    account = replay(raw, menu, prices)
    assert account["profit"] == value, "canonical replay and method objective differ"
    result = {"status": "COMPLETED_VERIFIED", "scenario_id": raw["scenario_id"],
              "method": args.method, "input_sha256": digest(input_path),
              "arithmetic": "fractions.Fraction", "solver_objective": value,
              "independent_replay": account, "extra": extra,
              "internal_pht_calls": internal_solves,
              "validation_scope": "exact choices, permission, capacity, fees, accounting and recovered value; not independent optimality proof"}
    write_json(args.result, result)


def controller(args):
    if not args.run:
        raise SystemExit("Preparation only. No solver ran. Supply --run --output NEW_DIRECTORY when execution starts.")
    from validate_static import main as static_check
    static_check()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan = json.loads((ROOT / "plan.json").read_text())
    code_hashes = {str(p.relative_to(ROOT)): digest(p) for p in sorted(ROOT.glob("*.py"))+sorted((ROOT/"vendor").glob("*.py"))}
    environment = {"python": sys.version, "executable": sys.executable,
                   "platform": platform.platform(), "machine": platform.machine(),
                   "processor": platform.processor(), "cpu_count": os.cpu_count(),
                   "code_sha256": code_hashes, "plan_sha256": digest(ROOT/"plan.json"),
                   "status": "BATCH_STARTED", "numeric_type": "fractions.Fraction",
                   "execution": "sequential; wall budget includes process start, imports, all solves, replay and result write"}
    write_json(output/"environment.json", environment)
    records = []
    current_rows = [dict(r) for r in plan["runs"]]
    write_json(output/"status.json", {"runs": current_rows})
    def interrupted_signal(signum, frame):
        raise KeyboardInterrupt(f"controller received signal {signum}")
    signal.signal(signal.SIGTERM, interrupted_signal)
    for index, row in enumerate(plan["runs"]):
        out = output/row["run_id"]
        out.mkdir()
        write_json(out/"job.json", row)
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--input", str(ROOT/row["input_path"]),
                   "--method", row["method"], "--result", str(out/"worker_result.json")]
        start = time.monotonic()
        state = "RUNNING"
        process = None
        error = None
        interrupted = False
        current_rows[index] = dict(row, status="RUNNING", launch_reserved=True)
        write_json(output/"status.json", {"runs": current_rows})
        try:
            with (out/"stdout.log").open("w") as stdout, (out/"stderr.log").open("w") as stderr:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
                remaining = max(0, row["budget_seconds"]-(time.monotonic()-start))
                process.wait(timeout=remaining)
                elapsed = time.monotonic()-start
                if elapsed >= row["budget_seconds"]:
                    state = "TIMEOUT"
                elif process.returncode != 0:
                    state = "ERROR"
                elif not (out/"worker_result.json").exists():
                    state = "MISSING_RESULT"
                else:
                    state = json.loads((out/"worker_result.json").read_text())["status"]
        except subprocess.TimeoutExpired:
            state = "TIMEOUT"
        except KeyboardInterrupt as exc:
            state, interrupted, error = "INTERRUPTED", True, str(exc)
        except Exception as exc:
            state, error = "ERROR", f"{type(exc).__name__}: {exc}"
        finally:
            if process is not None and process.poll() is None:
                # Cleanup the whole worker session; no child receives its own budget.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        result = dict(row, status=state, elapsed_seconds=time.monotonic()-start,
                      exit_code=None if process is None else process.returncode,
                      worker_pid=None if process is None else process.pid,
                      error=error, worker_result_usable=state == "COMPLETED_VERIFIED",
                      partial_result_policy="any result under a non-completed controller status is diagnostic only",
                      result_path=str((out/"worker_result.json").relative_to(output)))
        write_json(out/"controller_result.json", result)
        records.append(result)
        current_rows[index] = result
        write_json(output/"status.json", {"runs": current_rows})
        if interrupted:
            break
    # Derive reference differences only after the frozen nine-group schedule.
    comparisons = []
    for scenario in dict.fromkeys(r["scenario_id"] for r in current_rows):
        matched = [r for r in current_rows if r["scenario_id"] == scenario]
        reference = next(r for r in matched if r["method"] == "FULL_PHT_EXACT")
        ref_value = None
        if reference["status"] == "COMPLETED_VERIFIED":
            ref_value = Fraction(json.loads((output/reference["result_path"]).read_text())["solver_objective"])
        for row in matched:
            comparison = {"scenario_id": scenario, "method": row["method"], "status": row["status"],
                          "absolute_gap_to_full_pht": None}
            if ref_value is not None and row["status"] == "COMPLETED_VERIFIED":
                value = Fraction(json.loads((output/row["result_path"]).read_text())["solver_objective"])
                gap = ref_value-value
                comparison["absolute_gap_to_full_pht"] = gap
                comparison["reference_dominance_pass"] = gap >= 0
                if gap < 0:
                    comparison["status"] = "CROSS_METHOD_VALIDATION_FAILURE"
            comparisons.append(comparison)
    write_json(output/"comparisons.json", comparisons)
    print(json.dumps({"groups_attempted": len(records), "groups_in_status": len(current_rows), "output": str(output)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--input", help=argparse.SUPPRESS)
    parser.add_argument("--method", help=argparse.SUPPRESS)
    parser.add_argument("--result", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args)
    else:
        if args.run and not args.output:
            parser.error("--run requires a fresh --output directory")
        controller(args)


if __name__ == "__main__":
    main()
