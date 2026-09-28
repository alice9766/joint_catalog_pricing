#!/usr/bin/env python3
"""Continuous-price exact oracle for tiny Safe Refresh-Menu instances.

This is a research falsification tool, not a scalable algorithm.  For each
menu it enumerates buyer assignments and solves the resulting price LP.  The
LP uses weak incentive constraints, so its value is the supremum under strict
outside-option tie-breaking (and the optimum under seller-favourable ties).
Any strict value gap can be made tie-breaking robust by an arbitrarily small
price perturbation.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass
from typing import Dict, FrozenSet, Iterable, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import linprog

from exact_srmd import Instance


TOL = 1e-8


@dataclass(frozen=True)
class ContinuousSolution:
    menu: Tuple[int, ...]
    prices: Tuple[float, ...]
    assignment: Tuple[int, ...]
    profit_supremum: float


def _solve_assignment_lp(
    instance: Instance,
    menu: Tuple[int, ...],
    assignment: Tuple[int, ...],
    require_nondecreasing_prices: bool = False,
) -> Optional[ContinuousSolution]:
    """Optimize prices for one proposed buyer assignment."""
    if not menu:
        if all(choice == -1 for choice in assignment):
            return ContinuousSolution((), (), assignment, 0.0)
        return None

    position = {product: j for j, product in enumerate(menu)}
    demand = np.zeros(len(menu))
    for buyer, choice in zip(instance.buyers, assignment):
        if choice >= 0:
            demand[position[choice]] += buyer.weight

    # linprog minimizes.  Constant marginal and fixed costs are restored after.
    objective = -demand
    A_ub = []
    b_ub = []

    for buyer, choice in zip(instance.buyers, assignment):
        accessible = [m for m in menu if m <= instance.safety_ceiling[buyer.trust]]
        if choice == -1:
            # Reject: theta*q_m - p_m <= 0.
            for m in accessible:
                row = np.zeros(len(menu))
                row[position[m]] = -1.0
                A_ub.append(row)
                b_ub.append(-buyer.theta * instance.qualities[m])
            continue

        if choice not in accessible:
            return None

        # Individual rationality: p_j <= theta*q_j.
        row = np.zeros(len(menu))
        row[position[choice]] = 1.0
        A_ub.append(row)
        b_ub.append(buyer.theta * instance.qualities[choice])

        # Incentive compatibility against every accessible offered product.
        for m in accessible:
            if m == choice:
                continue
            # theta*q_j-p_j >= theta*q_m-p_m
            # <=> p_j-p_m <= theta*(q_j-q_m).
            row = np.zeros(len(menu))
            row[position[choice]] = 1.0
            row[position[m]] = -1.0
            A_ub.append(row)
            b_ub.append(
                buyer.theta
                * (instance.qualities[choice] - instance.qualities[m])
            )

    if require_nondecreasing_prices:
        for left, right in zip(range(len(menu) - 1), range(1, len(menu))):
            row = np.zeros(len(menu))
            row[left] = 1.0
            row[right] = -1.0
            A_ub.append(row)
            b_ub.append(0.0)

    max_value = max(
        [buyer.theta * max(instance.qualities[m] for m in menu) for buyer in instance.buyers]
        + [0.0]
    )
    result = linprog(
        objective,
        A_ub=np.asarray(A_ub) if A_ub else None,
        b_ub=np.asarray(b_ub) if b_ub else None,
        bounds=[(0.0, max_value + 1.0)] * len(menu),
        method="highs",
    )
    if not result.success:
        return None

    variable_revenue = float(demand @ result.x)
    marginal_cost = sum(
        demand[j] * instance.marginal_costs[m] for j, m in enumerate(menu)
    )
    fixed_cost = sum(instance.fixed_costs[m] for m in menu)
    return ContinuousSolution(
        menu=menu,
        prices=tuple(float(x) for x in result.x),
        assignment=assignment,
        profit_supremum=variable_revenue - marginal_cost - fixed_cost,
    )


def solve_menu_continuous(
    instance: Instance,
    menu: Sequence[int],
    require_nondecreasing_prices: bool = False,
) -> ContinuousSolution:
    """Exact continuous-price supremum for a fixed tiny menu."""
    menu_tuple = tuple(sorted(menu))
    choice_sets = []
    for buyer in instance.buyers:
        accessible = tuple(
            m for m in menu_tuple if m <= instance.safety_ceiling[buyer.trust]
        )
        choice_sets.append((-1,) + accessible)

    best = ContinuousSolution(menu_tuple, tuple(0.0 for _ in menu_tuple), tuple(-1 for _ in instance.buyers), -float("inf"))
    for assignment in itertools.product(*choice_sets):
        candidate = _solve_assignment_lp(
            instance,
            menu_tuple,
            assignment,
            require_nondecreasing_prices=require_nondecreasing_prices,
        )
        if candidate is not None and candidate.profit_supremum > best.profit_supremum + TOL:
            best = candidate
    return best


def solve_continuous(
    instance: Instance,
    max_k: int,
    allowed_products: Optional[Iterable[int]] = None,
    require_nondecreasing_prices: bool = False,
) -> ContinuousSolution:
    """Optimize menu and continuous prices for a tiny instance."""
    allowed = tuple(
        sorted(range(instance.M) if allowed_products is None else allowed_products)
    )
    best = ContinuousSolution((), (), tuple(-1 for _ in instance.buyers), 0.0)
    for size in range(1, min(max_k, len(allowed)) + 1):
        for menu in itertools.combinations(allowed, size):
            candidate = solve_menu_continuous(
                instance,
                menu,
                require_nondecreasing_prices=require_nondecreasing_prices,
            )
            if candidate.profit_supremum > best.profit_supremum + TOL:
                best = candidate
    return best


def closure_value_continuous(instance: Instance, allowed: Iterable[int]) -> float:
    allowed_tuple = tuple(sorted(allowed))
    return solve_continuous(
        instance,
        len(allowed_tuple),
        allowed_products=allowed_tuple,
    ).profit_supremum


def submodularity_table(instance: Instance) -> Dict[str, object]:
    ground = set(range(instance.M))
    values: Dict[FrozenSet[int], float] = {
        frozenset(subset): closure_value_continuous(instance, subset)
        for size in range(instance.M + 1)
        for subset in itertools.combinations(range(instance.M), size)
    }
    violation = None
    for a_size in range(instance.M + 1):
        for a_tuple in itertools.combinations(range(instance.M), a_size):
            A = frozenset(a_tuple)
            for b_size in range(a_size, instance.M + 1):
                for b_tuple in itertools.combinations(range(instance.M), b_size):
                    B = frozenset(b_tuple)
                    if not A.issubset(B):
                        continue
                    for x in ground - set(B):
                        marginal_A = values[A | {x}] - values[A]
                        marginal_B = values[B | {x}] - values[B]
                        if marginal_A + TOL < marginal_B:
                            violation = {
                                "A": sorted(A),
                                "B": sorted(B),
                                "x": x,
                                "marginal_A": marginal_A,
                                "marginal_B": marginal_B,
                            }
                            return {
                                "values": {str(sorted(k)): v for k, v in values.items()},
                                "violation": violation,
                            }
    return {
        "values": {str(sorted(k)): v for k, v in values.items()},
        "violation": violation,
    }


def describe(instance: Instance, max_k: int) -> Dict[str, object]:
    return {
        "instance": asdict(instance),
        "solution": asdict(solve_continuous(instance, max_k)),
        "submodularity": submodularity_table(instance),
    }


def profile_certificate(instance: Instance, menu: Sequence[int]) -> Dict[str, object]:
    """Return all feasible assignment-LP optima for audit-sized instances."""
    menu_tuple = tuple(sorted(menu))
    choice_sets = []
    for buyer in instance.buyers:
        accessible = tuple(
            m for m in menu_tuple if m <= instance.safety_ceiling[buyer.trust]
        )
        choice_sets.append((-1,) + accessible)
    rows = []
    for assignment in itertools.product(*choice_sets):
        solution = _solve_assignment_lp(instance, menu_tuple, assignment)
        if solution is not None:
            rows.append(
                {
                    "assignment": list(solution.assignment),
                    "prices": list(solution.prices),
                    "profit_supremum": float(solution.profit_supremum),
                }
            )
    rows.sort(key=lambda row: row["profit_supremum"], reverse=True)
    return {"menu": list(menu_tuple), "feasible_profile_count": len(rows), "rows": rows}


def _self_test() -> Dict[str, object]:
    from exact_srmd import (
        captive_price_instance,
        noncontiguous_instance,
        one_group_multiple_plans_instance,
        submodularity_counterexample_instance,
    )

    captive = captive_price_instance()
    unrestricted = solve_continuous(captive, 2)
    monotone = solve_continuous(
        captive,
        2,
        require_nondecreasing_prices=True,
    )
    one_group = solve_continuous(one_group_multiple_plans_instance(), 2)
    noncontiguous = solve_continuous(noncontiguous_instance(), 2)
    submodularity = submodularity_table(submodularity_counterexample_instance())

    assert abs(float(unrestricted.profit_supremum) - 17.5) <= TOL
    assert abs(float(monotone.profit_supremum) - 11.5) <= TOL
    assert abs(float(one_group.profit_supremum) - 45.0) <= TOL
    assert noncontiguous.menu == (0, 2)
    assert abs(float(noncontiguous.profit_supremum) - 234.5) <= TOL
    assert submodularity["violation"] is not None
    return {
        "status": "ok",
        "unrestricted_price_profit_supremum": float(unrestricted.profit_supremum),
        "monotone_price_profit_supremum": float(monotone.profit_supremum),
        "one_group_two_plan_profit_supremum": float(one_group.profit_supremum),
        "noncontiguous_menu": list(noncontiguous.menu),
        "noncontiguous_profit_supremum": float(noncontiguous.profit_supremum),
        "submodularity": {
            "values": {
                key: float(value) for key, value in submodularity["values"].items()
            },
            "violation": {
                key: (float(value) if isinstance(value, (float, np.floating)) else value)
                for key, value in submodularity["violation"].items()
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--submodularity-certificate", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(_self_test(), indent=2, ensure_ascii=False))
        return
    if args.submodularity_certificate:
        from exact_srmd import submodularity_counterexample_instance

        instance = submodularity_counterexample_instance()
        certificate = {
            str(list(menu)): profile_certificate(instance, menu)
            for menu in ((1,), (1, 2), (0, 1), (0, 1, 2))
        }
        print(json.dumps(certificate, indent=2, ensure_ascii=False))
        return
    parser.error("choose --self-test or --submodularity-certificate")


if __name__ == "__main__":
    main()
