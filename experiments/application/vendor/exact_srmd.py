#!/usr/bin/env python3
"""Small exact oracle for the provisional SafeRefresh/SRMD model.

This module is deliberately limited to tiny, finite instances.  Its role is to
check definitions, find counterexamples, and validate future algorithms.  It is
not a scalable solver and must not be reported as an algorithmic contribution.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


TOL = 1e-9


@dataclass(frozen=True)
class BuyerType:
    trust: int
    theta: float
    weight: float = 1.0
    name: str = ""


@dataclass(frozen=True)
class Instance:
    qualities: Tuple[float, ...]
    fixed_costs: Tuple[float, ...]
    marginal_costs: Tuple[float, ...]
    safety_ceiling: Tuple[int, ...]
    buyers: Tuple[BuyerType, ...]
    price_grid: Tuple[float, ...]
    name: str = "instance"

    @property
    def M(self) -> int:
        return len(self.qualities)

    @property
    def G(self) -> int:
        return len(self.safety_ceiling)

    def validate(self) -> None:
        if self.M == 0:
            raise ValueError("At least one candidate frequency is required")
        if self.G == 0:
            raise ValueError("At least one trust tier is required")
        if len(self.fixed_costs) != self.M or len(self.marginal_costs) != self.M:
            raise ValueError("Cost vectors must match qualities")
        if self.qualities[0] <= 0:
            raise ValueError("Qualities must be strictly positive")
        if any(self.qualities[i] >= self.qualities[i + 1] for i in range(self.M - 1)):
            raise ValueError("Qualities must be strictly increasing")
        if any(c < 0 for c in self.fixed_costs + self.marginal_costs):
            raise ValueError("Costs must be nonnegative")
        if any(self.safety_ceiling[i] > self.safety_ceiling[i + 1] for i in range(self.G - 1)):
            raise ValueError("Safety ceilings must be nondecreasing in trust")
        if any(c < -1 or c >= self.M for c in self.safety_ceiling):
            raise ValueError("Safety ceilings must be -1 or a valid frequency index")
        if not self.price_grid or any(p < 0 for p in self.price_grid):
            raise ValueError("Price grid must be nonempty and nonnegative")
        for buyer in self.buyers:
            if not 0 <= buyer.trust < self.G:
                raise ValueError("Buyer trust index out of range")
            if buyer.theta < 0 or buyer.weight <= 0:
                raise ValueError("Buyer theta must be nonnegative and weight positive")


@dataclass(frozen=True)
class Evaluation:
    profit: float
    revenue: float
    variable_cost: float
    fixed_cost: float
    demand: Tuple[float, ...]
    choices: Tuple[int, ...]


@dataclass(frozen=True)
class Solution:
    menu: Tuple[int, ...]
    prices: Tuple[float, ...]
    evaluation: Evaluation


def choose_product(
    instance: Instance,
    buyer: BuyerType,
    menu: Sequence[int],
    prices_by_product: Dict[int, float],
) -> int:
    """Choose maximum utility; reject at nonpositive utility; tie to high quality."""
    ceiling = instance.safety_ceiling[buyer.trust]
    best_product = -1
    best_utility = 0.0
    for m in menu:
        if m > ceiling:
            continue
        utility = buyer.theta * instance.qualities[m] - prices_by_product[m]
        if utility > best_utility + TOL:
            best_product = m
            best_utility = utility
        elif utility > TOL and abs(utility - best_utility) <= TOL and m > best_product:
            best_product = m
    return best_product


def evaluate(instance: Instance, menu: Sequence[int], prices: Sequence[float]) -> Evaluation:
    if len(menu) != len(prices):
        raise ValueError("Menu and price vector lengths differ")
    prices_by_product = dict(zip(menu, prices))
    demand = [0.0] * instance.M
    choices: List[int] = []
    for buyer in instance.buyers:
        choice = choose_product(instance, buyer, menu, prices_by_product)
        choices.append(choice)
        if choice >= 0:
            demand[choice] += buyer.weight
    revenue = sum(prices_by_product[m] * demand[m] for m in menu)
    variable_cost = sum(instance.marginal_costs[m] * demand[m] for m in menu)
    fixed_cost = sum(instance.fixed_costs[m] for m in menu)
    return Evaluation(
        profit=revenue - variable_cost - fixed_cost,
        revenue=revenue,
        variable_cost=variable_cost,
        fixed_cost=fixed_cost,
        demand=tuple(demand),
        choices=tuple(choices),
    )


def _candidate_prices(instance: Instance, m: int) -> Tuple[float, ...]:
    maximum_value = max(
        (
            buyer.theta * instance.qualities[m]
            for buyer in instance.buyers
            if m <= instance.safety_ceiling[buyer.trust]
        ),
        default=0.0,
    )
    return tuple(
        p
        for p in instance.price_grid
        if p + TOL >= instance.marginal_costs[m] and p <= maximum_value + TOL
    )


def solve_exact(
    instance: Instance,
    K: int,
    *,
    allowed_products: Optional[Iterable[int]] = None,
    require_monotone_prices: bool = False,
) -> Solution:
    """Enumerate all menus and finite-grid prices for a tiny instance."""
    instance.validate()
    if K < 0:
        raise ValueError("K must be nonnegative")
    ground = tuple(sorted(set(range(instance.M) if allowed_products is None else allowed_products)))
    if any(m < 0 or m >= instance.M for m in ground):
        raise ValueError("Allowed product index out of range")

    empty = Evaluation(0.0, 0.0, 0.0, 0.0, tuple([0.0] * instance.M), tuple([-1] * len(instance.buyers)))
    best = Solution(tuple(), tuple(), empty)

    for size in range(1, min(K, len(ground)) + 1):
        for menu in itertools.combinations(ground, size):
            grids = [_candidate_prices(instance, m) for m in menu]
            if any(not grid for grid in grids):
                continue
            for prices in itertools.product(*grids):
                if require_monotone_prices and any(
                    prices[i] > prices[i + 1] + TOL for i in range(len(prices) - 1)
                ):
                    continue
                ev = evaluate(instance, menu, prices)
                candidate = Solution(tuple(menu), tuple(prices), ev)
                if _better(candidate, best):
                    best = candidate
    return best


def _better(left: Solution, right: Solution) -> bool:
    """Stable comparison: profit, then fewer plans, then lexicographic menu/prices."""
    if left.evaluation.profit > right.evaluation.profit + TOL:
        return True
    if abs(left.evaluation.profit - right.evaluation.profit) > TOL:
        return False
    if len(left.menu) != len(right.menu):
        return len(left.menu) < len(right.menu)
    return (left.menu, left.prices) < (right.menu, right.prices)


def profit_frontier(instance: Instance, max_k: Optional[int] = None) -> List[Solution]:
    limit = instance.M if max_k is None else min(max_k, instance.M)
    return [solve_exact(instance, k) for k in range(limit + 1)]


def safety_blocks(instance: Instance) -> List[Tuple[int, int, int]]:
    """Return inclusive trust-index intervals with the same safety ceiling."""
    blocks: List[Tuple[int, int, int]] = []
    start = 0
    for trust in range(1, instance.G + 1):
        if trust == instance.G or instance.safety_ceiling[trust] != instance.safety_ceiling[start]:
            blocks.append((start, trust - 1, instance.safety_ceiling[start]))
            start = trust
    return blocks


def reachable_blocks(instance: Instance, menu: Sequence[int]) -> List[Tuple[int, int, int]]:
    """Return intervals of trust indices with equal highest reachable menu item."""
    reachable: List[int] = []
    for ceiling in instance.safety_ceiling:
        eligible = [m for m in menu if m <= ceiling]
        reachable.append(max(eligible, default=-1))
    blocks: List[Tuple[int, int, int]] = []
    start = 0
    for trust in range(1, instance.G + 1):
        if trust == instance.G or reachable[trust] != reachable[start]:
            blocks.append((start, trust - 1, reachable[start]))
            start = trust
    return blocks


def compress_safety_equivalent_tiers(instance: Instance) -> Instance:
    """Losslessly relabel tiers that have the same safety ceiling.

    Buyer records are retained instead of numerically merging equal theta values;
    this is the empirical-distribution version of the mixture construction in G0.
    """
    blocks = safety_blocks(instance)
    trust_to_block: Dict[int, int] = {}
    for block_id, (start, end, _ceiling) in enumerate(blocks):
        for trust in range(start, end + 1):
            trust_to_block[trust] = block_id
    compressed_buyers = tuple(
        BuyerType(
            trust=trust_to_block[buyer.trust],
            theta=buyer.theta,
            weight=buyer.weight,
            name=buyer.name,
        )
        for buyer in instance.buyers
    )
    return Instance(
        qualities=instance.qualities,
        fixed_costs=instance.fixed_costs,
        marginal_costs=instance.marginal_costs,
        safety_ceiling=tuple(ceiling for _start, _end, ceiling in blocks),
        buyers=compressed_buyers,
        price_grid=instance.price_grid,
        name=f"{instance.name}-safety-compressed",
    )


def structural_bounds(instance: Instance, solution: Solution, K: int) -> Dict[str, float]:
    """Compute safe elementary bounds; these are diagnostics, not core theorems."""
    positive_types = sum(1 for buyer in instance.buyers if buyer.weight > 0)
    h_min = min(instance.fixed_costs)
    safe_value = 0.0
    for buyer in instance.buyers:
        ceiling = instance.safety_ceiling[buyer.trust]
        if ceiling >= 0:
            safe_value += buyer.weight * buyer.theta * instance.qualities[ceiling]

    viable = 0
    for m in range(instance.M):
        margin_upper = sum(
            buyer.weight
            * max(0.0, buyer.theta * instance.qualities[m] - instance.marginal_costs[m])
            for buyer in instance.buyers
            if m <= instance.safety_ceiling[buyer.trust]
        )
        if margin_upper > instance.fixed_costs[m] + TOL:
            viable += 1

    cost_bound = math.floor((safe_value + TOL) / h_min) if h_min > 0 else math.inf
    x_upper = min(K, instance.M, positive_types, viable, cost_bound)
    return {
        "B": len(safety_blocks(instance)),
        "L_star": len(solution.menu),
        "reachable_group_count": len(reachable_blocks(instance, solution.menu)),
        "safe_value_upper": safe_value,
        "positive_type_bound": positive_types,
        "viable_product_bound": viable,
        "fixed_cost_bound": cost_bound,
        "X_upper": x_upper,
    }


def _solution_json(instance: Instance, solution: Solution, K: int) -> Dict[str, object]:
    return {
        "instance": instance.name,
        "K": K,
        "menu_zero_based": solution.menu,
        "prices": solution.prices,
        "profit": solution.evaluation.profit,
        "demand": solution.evaluation.demand,
        "choices_zero_based_minus_one_is_reject": solution.evaluation.choices,
        "safety_blocks": safety_blocks(instance),
        "reachable_blocks": reachable_blocks(instance, solution.menu),
        "bounds": structural_bounds(instance, solution, K),
    }


def captive_price_instance() -> Instance:
    """Designed to test whether monotone public prices are without loss."""
    return Instance(
        qualities=(1.0, 2.0),
        fixed_costs=(0.25, 0.25),
        marginal_costs=(0.0, 0.0),
        safety_ceiling=(0, 1),
        buyers=(
            BuyerType(0, 10.0, 1.0, "low-trust-high-value"),
            BuyerType(1, 2.0, 2.0, "high-trust-lower-value"),
        ),
        price_grid=tuple(float(x) for x in range(0, 12)),
        name="captive-price-counterexample",
    )


def noncontiguous_instance() -> Instance:
    """Designed to test whether selected frequency indices must be contiguous."""
    return Instance(
        qualities=(4.0, 5.0, 7.0),
        fixed_costs=(0.25, 0.25, 0.25),
        marginal_costs=(0.0, 0.5, 0.5),
        safety_ceiling=(0, 2),
        buyers=(
            BuyerType(0, 5.0, 8.0, "restricted-high-value"),
            BuyerType(0, 1.0, 4.0, "restricted-low-value"),
            BuyerType(1, 2.0, 1.0, "fully-eligible-mid-value"),
            BuyerType(1, 6.0, 2.0, "fully-eligible-high-value"),
        ),
        price_grid=tuple(float(x) for x in range(0, 43)),
        name="noncontiguous-menu-counterexample",
    )


def one_group_multiple_plans_instance() -> Instance:
    """Counterexample candidate to L* <= number of safety-equivalence groups."""
    return Instance(
        qualities=(3.0, 4.0),
        fixed_costs=(0.5, 0.5),
        marginal_costs=(0.0, 2.0),
        safety_ceiling=(1,),
        buyers=(
            BuyerType(0, 1.0, 7.0, "low-value"),
            BuyerType(0, 2.0, 3.0, "mid-value"),
            BuyerType(0, 3.0, 4.0, "high-value"),
        ),
        price_grid=tuple(float(x) for x in range(0, 13)),
        name="one-group-multiple-plans-counterexample",
    )


def submodularity_counterexample_instance() -> Instance:
    """Finite-grid counterexample for the closure value's submodularity."""
    return Instance(
        qualities=(4.0, 6.0, 7.0),
        fixed_costs=(1.0, 1.0, 0.25),
        marginal_costs=(2.0, 1.0, 0.0),
        safety_ceiling=(0, 2),
        buyers=(
            BuyerType(1, 6.0, 4.0, "eligible-high-value"),
            BuyerType(0, 5.0, 1.0, "restricted-high-value"),
            BuyerType(1, 1.0, 3.0, "eligible-low-value"),
            BuyerType(1, 4.0, 2.0, "eligible-mid-value"),
        ),
        price_grid=tuple(float(x) for x in range(0, 43)),
        name="submodularity-counterexample",
    )


def repeated_safety_tiers_instance() -> Instance:
    """Instance used to check the empirical form of G0."""
    return Instance(
        qualities=(1.0, 2.0, 4.0),
        fixed_costs=(0.5, 0.5, 1.0),
        marginal_costs=(0.0, 0.5, 1.0),
        safety_ceiling=(-1, 0, 0, 2, 2),
        buyers=(
            BuyerType(0, 5.0, 1.0, "blocked"),
            BuyerType(1, 2.0, 2.0, "low-a"),
            BuyerType(2, 4.0, 3.0, "low-b"),
            BuyerType(3, 1.0, 4.0, "high-a"),
            BuyerType(4, 5.0, 2.0, "high-b"),
        ),
        price_grid=tuple(float(x) for x in range(0, 22)),
        name="repeated-safety-tiers",
    )


def random_instance(seed: int, M: int = 3, G: int = 2, N: int = 4) -> Instance:
    rng = random.Random(seed)
    qualities = tuple(float(x) for x in sorted(rng.sample(range(1, 8), M)))
    ceilings = sorted(rng.randrange(-1, M) for _ in range(G))
    if ceilings[-1] < 0:
        ceilings[-1] = M - 1
    buyers = tuple(
        BuyerType(
            trust=rng.randrange(G),
            theta=float(rng.randint(1, 6)),
            weight=float(rng.randint(1, 4)),
            name=f"type-{i}",
        )
        for i in range(N)
    )
    max_value = max(buyer.theta for buyer in buyers) * qualities[-1]
    return Instance(
        qualities=qualities,
        fixed_costs=tuple(float(rng.choice([0.25, 0.5, 1.0, 2.0])) for _ in range(M)),
        marginal_costs=tuple(float(rng.choice([0.0, 0.5, 1.0, 2.0])) for _ in range(M)),
        safety_ceiling=tuple(ceilings),
        buyers=buyers,
        price_grid=tuple(float(x) for x in range(0, math.ceil(max_value) + 1)),
        name=f"random-{seed}",
    )


def closure_value(instance: Instance, allowed: Iterable[int]) -> float:
    allowed_tuple = tuple(sorted(allowed))
    return solve_exact(
        instance,
        len(allowed_tuple),
        allowed_products=allowed_tuple,
    ).evaluation.profit


def find_submodularity_violation(instance: Instance) -> Optional[Dict[str, object]]:
    """Check the monotone closure value F(A)=max_{S subset A,p} profit."""
    ground = set(range(instance.M))
    values = {
        frozenset(subset): closure_value(instance, subset)
        for size in range(instance.M + 1)
        for subset in itertools.combinations(range(instance.M), size)
    }
    for a_size in range(instance.M + 1):
        for a_tuple in itertools.combinations(range(instance.M), a_size):
            A = frozenset(a_tuple)
            remaining_a = ground - set(A)
            for b_size in range(a_size, instance.M + 1):
                for b_tuple in itertools.combinations(range(instance.M), b_size):
                    B = frozenset(b_tuple)
                    if not A.issubset(B):
                        continue
                    for x in ground - set(B):
                        marginal_a = values[A | {x}] - values[A]
                        marginal_b = values[B | {x}] - values[B]
                        if marginal_a + TOL < marginal_b:
                            return {
                                "A": sorted(A),
                                "B": sorted(B),
                                "x": x,
                                "marginal_A": marginal_a,
                                "marginal_B": marginal_b,
                                "values": {
                                    "F(A)": values[A],
                                    "F(A+x)": values[A | {x}],
                                    "F(B)": values[B],
                                    "F(B+x)": values[B | {x}],
                                },
                            }
    return None


def find_frontier_concavity_violation(instance: Instance) -> Optional[Dict[str, object]]:
    frontier = profit_frontier(instance)
    values = [solution.evaluation.profit for solution in frontier]
    increments = [values[k] - values[k - 1] for k in range(1, len(values))]
    for k in range(1, len(increments)):
        if increments[k] > increments[k - 1] + TOL:
            return {"OPT": values, "increments": increments, "violation_at_k": k + 1}
    return None


def verify_grouping_lemmas(instance: Instance, solution: Solution) -> Dict[str, object]:
    """Finite-instance checks corresponding to G1 and the empirical form of G2/G3."""
    access_blocks = reachable_blocks(instance, solution.menu)
    g1_holds = len(access_blocks) <= len(solution.menu) + 1

    prices_by_product = dict(zip(solution.menu, solution.prices))
    g2_holds = True
    empirical_cell_count = 0
    cell_bound = 0
    for start, end, _ceiling in safety_blocks(instance):
        accessible = [m for m in solution.menu if m <= instance.safety_ceiling[start]]
        cell_bound += len(accessible) + 1
        choices_by_theta: Dict[float, int] = {}
        relevant_thetas = sorted(
            {buyer.theta for buyer in instance.buyers if start <= buyer.trust <= end}
        )
        for theta in relevant_thetas:
            probe = BuyerType(start, theta, 1.0, "grouping-probe")
            choices_by_theta[theta] = choose_product(instance, probe, solution.menu, prices_by_product)
        ordered_choices = [choices_by_theta[theta] for theta in relevant_thetas]
        qualities = [0.0 if choice < 0 else instance.qualities[choice] for choice in ordered_choices]
        if any(qualities[i] > qualities[i + 1] + TOL for i in range(len(qualities) - 1)):
            g2_holds = False
        empirical_cell_count += len(set(ordered_choices))

    return {
        "G1_reachable_blocks_at_most_L_plus_1": g1_holds,
        "reachable_block_count": len(access_blocks),
        "L_plus_1": len(solution.menu) + 1,
        "G2_empirical_choice_monotonicity": g2_holds,
        "G3_empirical_nonempty_cells": empirical_cell_count,
        "G3_structural_cell_bound": cell_bound,
        "G3_bound_holds": empirical_cell_count <= cell_bound,
    }


def run_self_test(random_trials: int) -> Dict[str, object]:
    results: Dict[str, object] = {"status": "ok", "designed_instances": {}, "random_search": {}}

    captive = captive_price_instance()
    unrestricted = solve_exact(captive, 2)
    monotone = solve_exact(captive, 2, require_monotone_prices=True)
    results["designed_instances"]["monotone_price_test"] = {
        "unrestricted": _solution_json(captive, unrestricted, 2),
        "monotone": _solution_json(captive, monotone, 2),
        "strict_gap": unrestricted.evaluation.profit - monotone.evaluation.profit,
    }

    noncontiguous = noncontiguous_instance()
    nc_solution = solve_exact(noncontiguous, 2)
    results["designed_instances"]["contiguous_frequency_test"] = _solution_json(
        noncontiguous, nc_solution, 2
    )

    one_group = one_group_multiple_plans_instance()
    one_group_solution = solve_exact(one_group, 2)
    results["designed_instances"]["L_star_le_B_test"] = _solution_json(
        one_group, one_group_solution, 2
    )

    submodular = submodularity_counterexample_instance()
    results["designed_instances"]["submodularity_test"] = {
        "violation": find_submodularity_violation(submodular),
        "instance": asdict(submodular),
    }

    repeated = repeated_safety_tiers_instance()
    compressed = compress_safety_equivalent_tiers(repeated)
    repeated_solution = solve_exact(repeated, 3)
    compressed_solution = solve_exact(compressed, 3)
    results["designed_instances"]["G0_safety_compression_test"] = {
        "original_G": repeated.G,
        "compressed_G": compressed.G,
        "original_solution": _solution_json(repeated, repeated_solution, 3),
        "compressed_solution": _solution_json(compressed, compressed_solution, 3),
        "profit_gap": repeated_solution.evaluation.profit - compressed_solution.evaluation.profit,
    }

    results["designed_instances"]["grouping_lemma_checks"] = {
        "captive_price": verify_grouping_lemmas(captive, unrestricted),
        "noncontiguous": verify_grouping_lemmas(noncontiguous, nc_solution),
        "one_group": verify_grouping_lemmas(one_group, one_group_solution),
    }

    assert unrestricted.evaluation.profit > monotone.evaluation.profit + TOL
    assert nc_solution.menu == (0, 2)
    assert len(one_group_solution.menu) > len(safety_blocks(one_group))
    assert results["designed_instances"]["submodularity_test"]["violation"] is not None
    assert repeated_solution.menu == compressed_solution.menu
    assert repeated_solution.prices == compressed_solution.prices
    assert abs(repeated_solution.evaluation.profit - compressed_solution.evaluation.profit) <= TOL
    for check in results["designed_instances"]["grouping_lemma_checks"].values():
        assert check["G1_reachable_blocks_at_most_L_plus_1"]
        assert check["G2_empirical_choice_monotonicity"]
        assert check["G3_bound_holds"]

    found_l_gt_b = None
    found_submodular = None
    found_frontier = None
    for seed in range(random_trials):
        instance = random_instance(seed)
        solution = solve_exact(instance, instance.M)
        bounds = structural_bounds(instance, solution, instance.M)
        if found_l_gt_b is None and bounds["L_star"] > bounds["B"]:
            found_l_gt_b = _solution_json(instance, solution, instance.M)
            found_l_gt_b["instance_data"] = asdict(instance)
        if found_submodular is None:
            violation = find_submodularity_violation(instance)
            if violation is not None:
                found_submodular = {
                    "instance": asdict(instance),
                    "violation": violation,
                }
        if found_frontier is None:
            violation = find_frontier_concavity_violation(instance)
            if violation is not None:
                found_frontier = {
                    "instance": asdict(instance),
                    "violation": violation,
                }
        if found_l_gt_b and found_submodular and found_frontier:
            break

    results["random_search"] = {
        "trials_requested": random_trials,
        "L_star_gt_B": found_l_gt_b,
        "submodularity_violation": found_submodular,
        "frontier_concavity_violation": found_frontier,
    }
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--random-trials", type=int, default=40)
    args = parser.parse_args()
    if not args.self_test:
        parser.error("This research oracle currently exposes only --self-test")
    print(json.dumps(run_self_test(args.random_trials), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
