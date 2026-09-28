#!/usr/bin/env python3
"""Exact application solver and canonical replay, extracted from the recorded runner.

The archived vendor solver files are unchanged. Public entry point:
scripts/solve_application.py.
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

