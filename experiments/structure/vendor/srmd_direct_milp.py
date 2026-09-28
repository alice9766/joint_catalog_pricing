#!/usr/bin/env python3
"""Direct mixed-integer baseline for static SRMD.

The formulation uses product activation, public continuous prices, buyer
assignment, and McCormick linearization of price times assignment.  It is an
independent optimization baseline: it does not use PHT states or the finite
slope theorem.  Weak utility inequalities compute the same supremum semantics
as the tiny assignment-LP oracle.  The recovered prices are additionally
replayed with the canonical PHT tie rule.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from exact_srmd import Instance


@dataclass(frozen=True)
class DirectMilpResult:
    status: str
    message: str
    objective_upper_semantics: float
    canonical_replay_profit: float
    menu: Tuple[int, ...]
    prices: Tuple[float, ...]
    assignment: Tuple[int, ...]
    canonical_assignment: Tuple[int, ...]
    mip_gap: float
    mip_dual_bound: float
    mip_node_count: int
    wall_seconds: float
    variable_count: int
    constraint_count: int


class _Variables:
    def __init__(self) -> None:
        self.lower: List[float] = []
        self.upper: List[float] = []
        self.integrality: List[int] = []

    def add(self, lower: float, upper: float, integer: bool = False) -> int:
        index = len(self.lower)
        self.lower.append(lower)
        self.upper.append(upper)
        self.integrality.append(1 if integer else 0)
        return index


class _Constraints:
    def __init__(self) -> None:
        self.rows: List[int] = []
        self.cols: List[int] = []
        self.data: List[float] = []
        self.lower: List[float] = []
        self.upper: List[float] = []

    def add(
        self,
        coefficients: Dict[int, float],
        lower: float = -math.inf,
        upper: float = math.inf,
    ) -> None:
        row = len(self.lower)
        for column, value in coefficients.items():
            if value:
                self.rows.append(row)
                self.cols.append(column)
                self.data.append(float(value))
        self.lower.append(float(lower))
        self.upper.append(float(upper))

    def linear_constraint(self, variable_count: int) -> LinearConstraint:
        matrix = coo_matrix(
            (self.data, (self.rows, self.cols)),
            shape=(len(self.lower), variable_count),
        ).tocsr()
        return LinearConstraint(matrix, np.asarray(self.lower), np.asarray(self.upper))


def _canonical_replay(
    instance: Instance,
    menu: Tuple[int, ...],
    prices: Tuple[float, ...],
    *,
    feasibility_tolerance: float,
) -> Tuple[float, Tuple[int, ...]]:
    price = dict(zip(menu, prices))
    demand = {m: 0.0 for m in menu}
    assignment: List[int] = []
    for buyer in instance.buyers:
        alternatives = [(-1, 0.0, 0.0, 0.0)]
        for product in menu:
            if product <= instance.safety_ceiling[buyer.trust]:
                alternatives.append(
                    (
                        product,
                        buyer.theta * instance.qualities[product] - price[product],
                        price[product],
                        instance.qualities[product],
                    )
                )
        best_utility = max(row[1] for row in alternatives)
        tied = [
            row
            for row in alternatives
            if row[1] >= best_utility - feasibility_tolerance
        ]
        if buyer.theta <= 1e-9 and best_utility <= feasibility_tolerance:
            choice = -1
        else:
            choice = max(tied, key=lambda row: (row[2], row[3], row[0]))[0]
        assignment.append(choice)
        if choice >= 0:
            demand[choice] += buyer.weight
    profit = sum(
        (price[m] - instance.marginal_costs[m]) * demand[m] for m in menu
    ) - sum(instance.fixed_costs[m] for m in menu)
    return float(profit), tuple(assignment)


def solve_direct_milp(
    instance: Instance,
    K: int,
    *,
    time_limit: float = 120.0,
    mip_rel_gap: float = 0.0,
    require_nondecreasing_prices: bool = False,
) -> DirectMilpResult:
    instance.validate()
    if K < 0:
        raise ValueError("K must be nonnegative")
    K = min(K, instance.M)

    maximum_value = max(
        [
            buyer.theta * instance.qualities[m]
            for buyer in instance.buyers
            for m in range(instance.M)
            if m <= instance.safety_ceiling[buyer.trust]
        ]
        + [1.0]
    )
    price_upper = float(maximum_value)

    variables = _Variables()
    z = [variables.add(0.0, 1.0, integer=True) for _ in range(instance.M)]
    p = [variables.add(0.0, price_upper) for _ in range(instance.M)]
    outside: List[int] = []
    x: Dict[Tuple[int, int], int] = {}
    revenue: Dict[Tuple[int, int], int] = {}

    for i, buyer in enumerate(instance.buyers):
        outside.append(variables.add(0.0, 1.0, integer=True))
        for m in range(instance.M):
            if m <= instance.safety_ceiling[buyer.trust]:
                x[i, m] = variables.add(0.0, 1.0, integer=True)
                revenue[i, m] = variables.add(0.0, price_upper)

    objective = np.zeros(len(variables.lower))
    for m in range(instance.M):
        objective[z[m]] = instance.fixed_costs[m]
    for i, buyer in enumerate(instance.buyers):
        for m in range(instance.M):
            if (i, m) not in x:
                continue
            objective[x[i, m]] = buyer.weight * instance.marginal_costs[m]
            objective[revenue[i, m]] = -buyer.weight

    constraints = _Constraints()
    constraints.add({z[m]: 1.0 for m in range(instance.M)}, upper=float(K))
    for m in range(instance.M):
        constraints.add({p[m]: 1.0, z[m]: -price_upper}, upper=0.0)
    if require_nondecreasing_prices:
        # Price monotonicity is required only for pairs that are both active;
        # inactive candidates have p=0 and must not constrain an active menu.
        for left in range(instance.M):
            for right in range(left + 1, instance.M):
                constraints.add(
                    {
                        p[left]: 1.0,
                        p[right]: -1.0,
                        z[left]: price_upper,
                        z[right]: price_upper,
                    },
                    upper=2.0 * price_upper,
                )

    for i, buyer in enumerate(instance.buyers):
        accessible = [m for m in range(instance.M) if (i, m) in x]
        choice_row = {outside[i]: 1.0}
        choice_row.update({x[i, m]: 1.0 for m in accessible})
        constraints.add(choice_row, lower=1.0, upper=1.0)

        for m in accessible:
            choice = x[i, m]
            paid = revenue[i, m]
            constraints.add({choice: 1.0, z[m]: -1.0}, upper=0.0)

            # McCormick envelope for paid = p_m * choice.
            constraints.add({paid: 1.0, p[m]: -1.0}, upper=0.0)
            constraints.add({paid: 1.0, choice: -price_upper}, upper=0.0)
            constraints.add(
                {p[m]: 1.0, choice: price_upper, paid: -1.0},
                upper=price_upper,
            )

            # Individual rationality when m is chosen.
            ir_big_m = max(0.0, price_upper - buyer.theta * instance.qualities[m])
            constraints.add(
                {p[m]: 1.0, choice: ir_big_m},
                upper=buyer.theta * instance.qualities[m] + ir_big_m,
            )

            # Chosen m weakly dominates every other offered accessible l.
            for other in accessible:
                if other == m:
                    continue
                delta_value = buyer.theta * (
                    instance.qualities[m] - instance.qualities[other]
                )
                disable_choice = max(0.0, price_upper - delta_value)
                # With m chosen, IR bounds p_m by theta*q_m. If the other
                # product is inactive, theta*q_other is therefore sufficient
                # to disable this comparison.
                disable_other = max(0.0, buyer.theta * instance.qualities[other])
                constraints.add(
                    {
                        p[m]: 1.0,
                        p[other]: -1.0,
                        choice: disable_choice,
                        z[other]: disable_other,
                    },
                    upper=delta_value + disable_choice + disable_other,
                )

        # If outside is selected, every offered product has nonpositive utility.
        for m in accessible:
            outside_big_m = max(0.0, buyer.theta * instance.qualities[m])
            constraints.add(
                {
                    p[m]: -1.0,
                    outside[i]: outside_big_m,
                    z[m]: outside_big_m,
                },
                upper=2.0 * outside_big_m - buyer.theta * instance.qualities[m],
            )

    started = time.perf_counter()
    result = milp(
        c=objective,
        integrality=np.asarray(variables.integrality),
        bounds=Bounds(np.asarray(variables.lower), np.asarray(variables.upper)),
        constraints=constraints.linear_constraint(len(variables.lower)),
        options={
            "time_limit": float(time_limit),
            "mip_rel_gap": float(mip_rel_gap),
            "presolve": True,
        },
    )
    wall_seconds = time.perf_counter() - started

    status_names = {
        0: "optimal",
        1: "limit",
        2: "infeasible",
        3: "unbounded",
        4: "solver_error",
    }
    status = status_names.get(int(result.status), f"status_{result.status}")
    if result.x is None:
        return DirectMilpResult(
            status=status,
            message=str(result.message),
            objective_upper_semantics=-math.inf,
            canonical_replay_profit=-math.inf,
            menu=(),
            prices=(),
            assignment=tuple(-1 for _ in instance.buyers),
            canonical_assignment=tuple(-1 for _ in instance.buyers),
            mip_gap=float(getattr(result, "mip_gap", math.inf)),
            mip_dual_bound=float(getattr(result, "mip_dual_bound", math.inf)),
            mip_node_count=int(getattr(result, "mip_node_count", 0)),
            wall_seconds=wall_seconds,
            variable_count=len(variables.lower),
            constraint_count=len(constraints.lower),
        )

    menu = tuple(m for m in range(instance.M) if result.x[z[m]] > 0.5)
    prices = tuple(max(0.0, float(result.x[p[m]])) for m in menu)
    assignment = []
    for i, _buyer in enumerate(instance.buyers):
        selected = [m for m in menu if (i, m) in x and result.x[x[i, m]] > 0.5]
        assignment.append(selected[0] if selected else -1)
    # HiGHS may return prices within its feasibility tolerance of a binding
    # indifference constraint.  Replay therefore uses a scale-aware numerical
    # tolerance and reports the solver objective separately.  This is a
    # baseline diagnostic, not an exact-rational certificate.
    replay_tolerance = 1e-6 * max(1.0, price_upper)
    replay_profit, canonical_assignment = _canonical_replay(
        instance,
        menu,
        prices,
        feasibility_tolerance=replay_tolerance,
    )

    objective_value = -float(result.fun)
    dual_min = float(getattr(result, "mip_dual_bound", result.fun))
    return DirectMilpResult(
        status=status,
        message=str(result.message),
        objective_upper_semantics=objective_value,
        canonical_replay_profit=replay_profit,
        menu=menu,
        prices=prices,
        assignment=tuple(assignment),
        canonical_assignment=canonical_assignment,
        mip_gap=float(getattr(result, "mip_gap", 0.0)),
        mip_dual_bound=-dual_min,
        mip_node_count=int(getattr(result, "mip_node_count", 0)),
        wall_seconds=wall_seconds,
        variable_count=len(variables.lower),
        constraint_count=len(constraints.lower),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_json")
    parser.add_argument("--k", type=int, required=True)
    parser.add_argument("--time-limit", type=float, default=120.0)
    parser.add_argument("--monotone-prices", action="store_true")
    args = parser.parse_args()
    with open(args.instance_json, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    from saferefresh_cli import instance_from_payload

    instance = instance_from_payload(raw, exact=False)
    print(
        json.dumps(
            asdict(
                solve_direct_milp(
                    instance,
                    args.k,
                    time_limit=args.time_limit,
                    require_nondecreasing_prices=args.monotone_prices,
                )
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
