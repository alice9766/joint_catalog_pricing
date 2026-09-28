#!/usr/bin/env python3
"""Exact candidate DP for SRMD with an arbitrary number of safety groups.

Research status: theorem-audit prototype.  The DP represents the lower-hull
history of nested safety prefixes as an ordered persistent stack tree.  Its
edge slopes are restricted to the finite empirical type set.  It reconstructs
the menu and prices and verifies the value from the buyers' original utility
maximization problem.

This file is deliberately independent of the B=2 implementation.  Agreement
with the continuous-price oracle is used only for falsification on tiny cases;
it is not a proof of the structural theorem.
"""

from __future__ import annotations

import argparse
import bisect
import functools
import json
import math
import random
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple, Union

from exact_srmd import BuyerType, Instance


NEG_INF = -float("inf")
TOL = 1e-8
Number = Union[float, Fraction]


def _as_fraction(value: object) -> Fraction:
    """Parse input numerics without importing their binary-float expansion."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    return Fraction(value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class HullTreeEdge:
    parent: int
    child: int
    boundary: int
    threshold: Number
    start_group: int
    end_group: int
    contribution: Number


@dataclass(frozen=True)
class PersistentHullResult:
    profit_supremum: Number
    # Legacy field name: k-node finite-slope PHT state values, not the original
    # SRMD optimum subject to activating exactly k products.
    exact_size_profit: Tuple[Number, ...]
    # Original SRMD optimum with at most k activated products, for each k.
    capacity_frontier: Tuple[Number, ...]
    menu: Tuple[int, ...]
    prices: Tuple[Number, ...]
    assignment: Tuple[int, ...]
    active_ceilings: Tuple[int, ...]
    group_hull_paths: Tuple[Tuple[int, ...], ...]
    group_edge_thresholds: Tuple[Tuple[Number, ...], ...]
    edges: Tuple[HullTreeEdge, ...]
    verified_profit: Number
    reconstruction_gap: Number
    threshold_count: int
    state_count: int


def solve_persistent_hull_tree(
    instance: Instance,
    K: int,
    *,
    exact: bool = False,
    target_size: Optional[int] = None,
) -> PersistentHullResult:
    """Optimize the canonical at-most-K continuous-price problem for arbitrary B.

    ``target_size`` selects an internal finite-slope PHT node-count state.
    It does not solve the original exactly-k activated-product problem: that
    problem may retain high-priced products with no demand. Likewise, the
    legacy ``exact_size_profit`` output records tree states; only
    ``capacity_frontier`` is the original SRMD cardinality frontier.

    exact=True uses fractions.Fraction for every finite numerical value and
    performs no tolerance-based comparison. ``-inf`` is used only as an
    out-of-domain sentinel for an unreachable DP state and is never combined
    arithmetically with a feasible value. Floats supplied by legacy fixtures
    are parsed through their decimal string representation; rigorous callers
    should pass Fraction, integers, or rational strings directly.
    """
    instance.validate()
    if K < 0:
        raise ValueError("K must be nonnegative")
    requested_K = min(K, instance.M)
    if target_size is not None and not 0 <= target_size <= requested_K:
        raise ValueError("target_size must lie between zero and the effective K")

    convert = _as_fraction if exact else float
    zero: Number = Fraction(0) if exact else 0.0
    qualities = tuple(convert(value) for value in instance.qualities)
    fixed_costs = tuple(convert(value) for value in instance.fixed_costs)
    marginal_costs = tuple(convert(value) for value in instance.marginal_costs)
    buyers = tuple(
        BuyerType(
            trust=buyer.trust,
            theta=convert(buyer.theta),
            weight=convert(buyer.weight),
            name=buyer.name,
        )
        for buyer in instance.buyers
    )

    def better(left: Number, right: Number) -> bool:
        return left > right if exact else left > right + TOL

    def reaches(theta: Number, threshold: Number) -> bool:
        return theta >= threshold if exact else theta + TOL >= threshold

    # Zero-value buyers always reject under the canonical convention and can be
    # removed before constructing safety groups.  This also removes safety
    # steps that have no effect on either profit or positive-type choices.
    relevant_buyers = tuple(
        b
        for b in buyers
        if (b.theta > zero if exact else b.theta > TOL)
    )
    if not relevant_buyers or K == 0:
        if target_size not in (None, 0):
            raise ValueError(
                f"no normalized PHT exists with target_size={target_size}"
            )
        assignment = tuple(-1 for _ in instance.buyers)
        exact_profit = (zero,) + tuple(
            NEG_INF for _ in range(requested_K)
        )
        capacity = tuple(zero for _ in range(requested_K + 1))
        return PersistentHullResult(
            profit_supremum=zero,
            exact_size_profit=exact_profit,
            capacity_frontier=capacity,
            menu=(),
            prices=(),
            assignment=assignment,
            active_ceilings=(),
            group_hull_paths=(),
            group_edge_thresholds=(),
            edges=(),
            verified_profit=zero,
            reconstruction_gap=zero,
            threshold_count=1,
            state_count=1,
        )

    active_ceilings = tuple(
        sorted({instance.safety_ceiling[b.trust] for b in relevant_buyers})
    )
    if active_ceilings[0] < 0:
        active_ceilings = tuple(c for c in active_ceilings if c >= 0)
    if not active_ceilings:
        if target_size not in (None, 0):
            raise ValueError(
                f"no normalized PHT exists with target_size={target_size}"
            )
        assignment = tuple(-1 for _ in instance.buyers)
        exact_profit = (zero,) + tuple(
            NEG_INF for _ in range(requested_K)
        )
        capacity = tuple(zero for _ in range(requested_K + 1))
        return PersistentHullResult(
            profit_supremum=zero,
            exact_size_profit=exact_profit,
            capacity_frontier=capacity,
            menu=(),
            prices=(),
            assignment=assignment,
            active_ceilings=(),
            group_hull_paths=(),
            group_edge_thresholds=(),
            edges=(),
            verified_profit=zero,
            reconstruction_gap=zero,
            threshold_count=1,
            state_count=1,
        )

    groups = tuple(
        tuple(
            b
            for b in relevant_buyers
            if instance.safety_ceiling[b.trust] == ceiling
        )
        for ceiling in active_ceilings
    )
    B = len(groups)
    product_count = active_ceilings[-1] + 1
    sentinel = product_count
    K = min(requested_K, product_count)
    if target_size is not None and target_size > K:
        raise ValueError(f"no normalized PHT exists with target_size={target_size}")

    thresholds = tuple(sorted({zero} | {b.theta for b in relevant_buyers}))
    threshold_count = len(thresholds)

    @functools.lru_cache(maxsize=None)
    def tail(group: int, threshold_index: int) -> Number:
        threshold = thresholds[threshold_index]
        return sum(
            (b.weight for b in groups[group] if reaches(b.theta, threshold)),
            zero,
        )

    # interval_tail[s][d][t] is built lazily below through prefix sums.
    tail_prefix = [[zero] * (B + 1) for _ in thresholds]
    for t_index in range(threshold_count):
        for b in range(B):
            tail_prefix[t_index][b + 1] = (
                tail_prefix[t_index][b] + tail(b, t_index)
            )

    def birth(product: int) -> int:
        if product == sentinel:
            return B
        return bisect.bisect_left(active_ceilings, product)

    def quality(product: int) -> Number:
        return zero if product < 0 else qualities[product]

    def marginal_cost(product: int) -> Number:
        return zero if product < 0 else marginal_costs[product]

    @functools.lru_cache(maxsize=None)
    def edge_value(
        parent: int,
        child: int,
        boundary: int,
        threshold_index: int,
    ) -> Number:
        start = birth(child)
        stop = birth(boundary)
        if stop <= start:
            return NEG_INF
        aggregate_tail = (
            tail_prefix[threshold_index][stop]
            - tail_prefix[threshold_index][start]
        )
        slope = thresholds[threshold_index]
        return (
            (
                slope * (quality(child) - quality(parent))
                - (marginal_cost(child) - marginal_cost(parent))
            )
            * aggregate_tail
            - fixed_costs[child]
        )

    subtree_choice: Dict[Tuple[int, int, int, int, int], bool] = {}
    forest_choice: Dict[Tuple[int, int, int, int], Optional[int]] = {}
    sequence_choice: Dict[
        Tuple[int, int, int, int, int, int],
        Optional[Tuple[int, int, int, int]],
    ] = {}

    @functools.lru_cache(maxsize=None)
    def subtree(
        parent: int,
        root: int,
        boundary: int,
        incoming_threshold: int,
        size: int,
    ) -> Number:
        """Best subtree rooted at root, ending just before boundary."""
        state = (parent, root, boundary, incoming_threshold, size)
        if size < 1 or not (parent < root < boundary):
            subtree_choice[state] = False
            return NEG_INF
        edge = edge_value(parent, root, boundary, incoming_threshold)
        if edge <= NEG_INF:
            subtree_choice[state] = False
            return NEG_INF
        children = forest(root, boundary, incoming_threshold, size - 1)
        if children <= NEG_INF:
            subtree_choice[state] = False
            return NEG_INF
        subtree_choice[state] = True
        return edge + children

    @functools.lru_cache(maxsize=None)
    def forest(
        parent: int,
        boundary: int,
        parent_threshold: int,
        size: int,
    ) -> Number:
        """Best ordered forest of children of parent within (parent,boundary)."""
        state = (parent, boundary, parent_threshold, size)
        if size == 0:
            forest_choice[state] = None
            return zero
        if size < 0 or boundary - parent - 1 < size:
            forest_choice[state] = None
            return NEG_INF
        best = NEG_INF
        best_first: Optional[int] = None
        for first in range(parent + 1, boundary):
            candidate = sequence(
                parent,
                first,
                boundary,
                parent_threshold,
                threshold_count - 1,
                size,
            )
            if better(candidate, best):
                best = candidate
                best_first = first
        forest_choice[state] = best_first
        return best

    @functools.lru_cache(maxsize=None)
    def sequence(
        parent: int,
        first: int,
        boundary: int,
        parent_threshold: int,
        upper_sibling_threshold: int,
        size: int,
    ) -> Number:
        """Best sibling sequence whose first child is fixed.

        Child-edge slopes are at least the parent-edge slope and weakly
        decrease across siblings.  A later sibling is required to have a
        strictly later birth group, otherwise the preceding subtree is never
        visible in any safety group.
        """
        state = (
            parent,
            first,
            boundary,
            parent_threshold,
            upper_sibling_threshold,
            size,
        )
        if (
            size < 1
            or not (parent < first < boundary)
            or parent_threshold > upper_sibling_threshold
        ):
            sequence_choice[state] = None
            return NEG_INF

        best = NEG_INF
        best_choice: Optional[Tuple[int, int, int, int]] = None
        for slope_index in range(parent_threshold, upper_sibling_threshold + 1):
            # Case 1: first is the final sibling.
            candidate = subtree(parent, first, boundary, slope_index, size)
            if better(candidate, best):
                best = candidate
                best_choice = (slope_index, -1, size, 0)

            # Case 2: y is the next sibling and therefore the boundary of the
            # first subtree.  Product order gives the preorder partition.
            for y in range(first + 1, boundary):
                if birth(y) <= birth(first):
                    continue
                for left_size in range(1, size):
                    left = subtree(
                        parent,
                        first,
                        y,
                        slope_index,
                        left_size,
                    )
                    if left <= NEG_INF:
                        continue
                    right = sequence(
                        parent,
                        y,
                        boundary,
                        parent_threshold,
                        slope_index,
                        size - left_size,
                    )
                    if right <= NEG_INF:
                        continue
                    candidate = left + right
                    if better(candidate, best):
                        best = candidate
                        best_choice = (
                            slope_index,
                            y,
                            left_size,
                            size - left_size,
                        )
        sequence_choice[state] = best_choice
        return best

    exact_size_profit: List[Number] = [zero]
    capacity_frontier: List[Number] = [zero]
    best: Number = zero
    best_size = 0
    for size in range(1, K + 1):
        candidate = forest(-1, sentinel, 0, size)
        exact_size_profit.append(candidate)
        capacity_frontier.append(max(capacity_frontier[-1], candidate))
        if better(candidate, best):
            best = candidate
            best_size = size

    if target_size is not None:
        best_size = target_size
        best = exact_size_profit[target_size]
        if best <= NEG_INF:
            raise ValueError(f"no normalized PHT exists with target_size={target_size}")

    while len(exact_size_profit) < requested_K + 1:
        exact_size_profit.append(NEG_INF)
        capacity_frontier.append(capacity_frontier[-1])

    edge_records: List[Tuple[int, int, int, int]] = []

    def reconstruct_subtree(
        parent: int,
        root: int,
        boundary: int,
        threshold_index: int,
        size: int,
    ) -> None:
        state = (parent, root, boundary, threshold_index, size)
        if not subtree_choice.get(state, False):
            raise AssertionError(f"missing subtree backpointer for {state}")
        edge_records.append((parent, root, boundary, threshold_index))
        reconstruct_forest(root, boundary, threshold_index, size - 1)

    def reconstruct_forest(
        parent: int,
        boundary: int,
        parent_threshold: int,
        size: int,
    ) -> None:
        if size == 0:
            return
        state = (parent, boundary, parent_threshold, size)
        first = forest_choice.get(state)
        if first is None:
            raise AssertionError(f"missing forest backpointer for {state}")
        reconstruct_sequence(
            parent,
            first,
            boundary,
            parent_threshold,
            threshold_count - 1,
            size,
        )

    def reconstruct_sequence(
        parent: int,
        first: int,
        boundary: int,
        parent_threshold: int,
        upper_sibling_threshold: int,
        size: int,
    ) -> None:
        state = (
            parent,
            first,
            boundary,
            parent_threshold,
            upper_sibling_threshold,
            size,
        )
        choice = sequence_choice.get(state)
        if choice is None:
            raise AssertionError(f"missing sequence backpointer for {state}")
        slope_index, next_sibling, left_size, right_size = choice
        if next_sibling < 0:
            reconstruct_subtree(
                parent,
                first,
                boundary,
                slope_index,
                left_size,
            )
            return
        reconstruct_subtree(
            parent,
            first,
            next_sibling,
            slope_index,
            left_size,
        )
        reconstruct_sequence(
            parent,
            next_sibling,
            boundary,
            parent_threshold,
            slope_index,
            right_size,
        )

    if best_size > 0:
        reconstruct_forest(-1, sentinel, 0, best_size)

    price_by_product: Dict[int, Number] = {}
    installed_edges: List[HullTreeEdge] = []
    for parent, child, boundary, threshold_index in edge_records:
        parent_price = zero if parent < 0 else price_by_product[parent]
        price_by_product[child] = parent_price + thresholds[threshold_index] * (
            quality(child) - quality(parent)
        )
        installed_edges.append(
            HullTreeEdge(
                parent=parent,
                child=child,
                boundary=boundary,
                threshold=thresholds[threshold_index],
                start_group=birth(child),
                end_group=birth(boundary) - 1,
                contribution=edge_value(
                    parent,
                    child,
                    boundary,
                    threshold_index,
                ),
            )
        )

    menu = tuple(sorted(price_by_product))
    prices = tuple(price_by_product[m] for m in menu)
    price_lookup = dict(zip(menu, prices))

    group_paths: List[Tuple[int, ...]] = []
    group_thresholds: List[Tuple[Number, ...]] = []
    for b in range(B):
        active_child: Dict[int, HullTreeEdge] = {}
        for edge in installed_edges:
            if edge.start_group <= b <= edge.end_group:
                if edge.parent in active_child:
                    raise AssertionError(
                        f"two active children of {edge.parent} in group {b}"
                    )
                active_child[edge.parent] = edge
        nodes: List[int] = []
        slopes: List[Number] = []
        parent = -1
        while parent in active_child:
            edge = active_child[parent]
            nodes.append(edge.child)
            slopes.append(edge.threshold)
            parent = edge.child
        if len(nodes) != len(active_child):
            raise AssertionError(f"active edges do not form one path in group {b}")
        group_paths.append(tuple(nodes))
        group_thresholds.append(tuple(slopes))

    assignment: List[int] = []
    demand_by_product = {m: zero for m in menu}
    for buyer in buyers:
        alternatives = [(-1, zero, zero, zero)]
        for product in menu:
            if product <= instance.safety_ceiling[buyer.trust]:
                price = price_lookup[product]
                alternatives.append(
                    (
                        product,
                        buyer.theta * qualities[product] - price,
                        price,
                        qualities[product],
                    )
                )
        max_utility = max(row[1] for row in alternatives)
        if exact:
            tied = [row for row in alternatives if row[1] == max_utility]
        else:
            tied = [
                row for row in alternatives if row[1] >= max_utility - 1e-7
            ]
        zero_type_exit = (
            buyer.theta == zero and max_utility == zero
            if exact
            else buyer.theta <= TOL and max_utility <= 1e-7
        )
        if zero_type_exit:
            choice = -1
        else:
            choice = max(tied, key=lambda row: (row[2], row[3], row[0]))[0]
        assignment.append(choice)
        if choice >= 0:
            demand_by_product[choice] += buyer.weight

    verified_profit = (
        sum(
            (
                (price_lookup[m] - marginal_costs[m])
                * demand_by_product[m]
                for m in menu
            ),
            zero,
        )
        - sum((fixed_costs[m] for m in menu), zero)
    )
    reconstruction_gap = verified_profit - best
    reconstruction_failed = (
        reconstruction_gap != zero
        if exact
        else abs(reconstruction_gap) > 1e-6
    )
    if reconstruction_failed:
        raise AssertionError(
            "backtracked menu does not reproduce the DP value: "
            f"{verified_profit} versus {best}"
        )

    state_count = (
        subtree.cache_info().currsize
        + forest.cache_info().currsize
        + sequence.cache_info().currsize
        + edge_value.cache_info().currsize
    )
    return PersistentHullResult(
        profit_supremum=best,
        exact_size_profit=tuple(exact_size_profit),
        capacity_frontier=tuple(capacity_frontier),
        menu=menu,
        prices=prices,
        assignment=tuple(assignment),
        active_ceilings=active_ceilings,
        group_hull_paths=tuple(group_paths),
        group_edge_thresholds=tuple(group_thresholds),
        edges=tuple(installed_edges),
        verified_profit=verified_profit,
        reconstruction_gap=reconstruction_gap,
        threshold_count=threshold_count,
        state_count=state_count,
    )


def random_nested_instance(
    seed: int,
    B: int = 3,
    M: int = 4,
    N: int = 5,
    fractional: bool = True,
) -> Instance:
    """Generate a tiny instance with exactly B active nested safety groups."""
    if B < 1 or B > M:
        raise ValueError("require 1 <= B <= M")
    if N < B:
        raise ValueError("N must be at least B")
    rng = random.Random(seed)
    if fractional:
        pool = {round(rng.uniform(0.25, 12.0), 2) for _ in range(8 * M)}
        if len(pool) < M:
            return random_nested_instance(seed + 10_000_000, B, M, N, fractional)
        qualities = tuple(sorted(pool)[:M])
    else:
        qualities = tuple(float(x) for x in sorted(rng.sample(range(1, 20), M)))

    if B == 1:
        ceilings = (M - 1,)
    else:
        ceilings = tuple(sorted(rng.sample(range(M - 1), B - 1)) + [M - 1])

    def draw_theta(used: set[float]) -> float:
        while True:
            value = (
                round(rng.uniform(0.25, 9.0), 2)
                if fractional
                else float(rng.randint(1, 30))
            )
            if value not in used:
                used.add(value)
                return value

    used_theta: set[float] = set()
    buyers: List[BuyerType] = []
    for b in range(B):
        buyers.append(
            BuyerType(
                b,
                draw_theta(used_theta),
                float(rng.randint(1, 4)),
                f"group-{b}",
            )
        )
    for i in range(N - B):
        b = rng.randrange(B)
        buyers.append(
            BuyerType(
                b,
                draw_theta(used_theta),
                float(rng.randint(1, 4)),
                f"type-{i}",
            )
        )
    max_value = max(b.theta for b in buyers) * qualities[-1]
    return Instance(
        qualities=qualities,
        fixed_costs=tuple(
            float(rng.choice([0.0, 0.25, 0.5, 1.0, 2.0])) for _ in range(M)
        ),
        marginal_costs=tuple(
            float(rng.choice([0.0, 0.5, 1.0, 2.0, 4.0])) for _ in range(M)
        ),
        safety_ceiling=ceilings,
        buyers=tuple(buyers),
        price_grid=tuple(float(x) for x in range(math.ceil(max_value) + 1)),
        name=f"random-b{B}-{seed}",
    )


def self_test(
    trials: int,
    B: int,
    M: int,
    N: int,
    K: int,
    *,
    exact: bool = False,
) -> Dict[str, object]:
    from continuous_srmd import solve_continuous

    mismatches = []
    checked = []
    max_reconstruction_gap = 0.0
    max_oracle_gap = 0.0
    for seed in range(trials):
        instance = random_nested_instance(seed, B=B, M=M, N=N)
        dp = solve_persistent_hull_tree(instance, K, exact=exact)
        oracle = solve_continuous(instance, K)
        oracle_gap = float(dp.profit_supremum) - oracle.profit_supremum
        max_oracle_gap = max(max_oracle_gap, abs(oracle_gap))
        max_reconstruction_gap = max(
            max_reconstruction_gap, abs(float(dp.reconstruction_gap))
        )
        row = {
            "seed": seed,
            "dp_profit": float(dp.profit_supremum),
            "oracle_profit": oracle.profit_supremum,
            "oracle_gap": oracle_gap,
            "menu": list(dp.menu),
            "states": dp.state_count,
        }
        checked.append(row)
        if abs(oracle_gap) > 1e-6:
            mismatches.append(
                {
                    **row,
                    "instance": asdict(instance),
                    "dp": asdict(dp),
                    "oracle": asdict(oracle),
                }
            )
            break
    return {
        "status": "ok" if not mismatches else "mismatch",
        "B": B,
        "M": M,
        "N": N,
        "K": K,
        "exact": exact,
        "trials_requested": trials,
        "trials_checked": len(checked),
        "max_oracle_gap": max_oracle_gap,
        "max_reconstruction_gap": max_reconstruction_gap,
        "mismatches": mismatches,
        "last_checks": checked[-5:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--groups", type=int, default=3)
    parser.add_argument("--products", type=int, default=4)
    parser.add_argument("--buyers", type=int, default=5)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument(
        "--exact",
        action="store_true",
        help="use exact Fraction arithmetic in the PHT solver",
    )
    args = parser.parse_args()
    if args.self_test:
        print(
            json.dumps(
                self_test(
                    args.trials,
                    args.groups,
                    args.products,
                    args.buyers,
                    args.k,
                    exact=args.exact,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    parser.error("choose --self-test")


if __name__ == "__main__":
    main()
