#!/usr/bin/env python3
"""Exact restricted-PHT baselines for SafeRefresh experiments.

`path` allows no branching node. `one_branch` allows at most one node
(including the outside-option root) with exactly two children; every node has
out-degree at most two.  The routines reuse the proved edge objective but have
their own restricted dynamic program and do not call the full PHT solver.
"""

from __future__ import annotations

import bisect
import functools
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from exact_srmd import BuyerType, Instance
from fixed_menu_pricing import (
    project_instance as _project_instance,
    solve_fixed_active_menu,
)


NEG_INF = -float("inf")
TOL = 1e-9


@dataclass(frozen=True)
class RestrictedPhtResult:
    mode: str
    profit_supremum: float
    exact_size_profit: Tuple[float, ...]
    capacity_frontier: Tuple[float, ...]
    state_count: int
    threshold_count: int


@dataclass(frozen=True)
class MenuRestrictionResult:
    mode: str
    profit_supremum: float
    menu: Tuple[int, ...]


def fixed_menu_value(instance: Instance, products: Sequence[int]) -> float:
    """Price a fixed activated menu, retaining costs of zero-demand products."""
    chosen = tuple(sorted(set(products)))
    return float(solve_fixed_active_menu(instance, chosen).profit_supremum)


def solve_contiguous_products(instance: Instance, K: int) -> MenuRestrictionResult:
    """Best menu that is one consecutive interval of candidate products."""
    if K < 0:
        raise ValueError("K must be nonnegative")
    best_value = 0.0
    best_menu: Tuple[int, ...] = ()
    for start in range(instance.M):
        for stop in range(start + 1, min(instance.M, start + K) + 1):
            menu = tuple(range(start, stop))
            value = fixed_menu_value(instance, menu)
            if value > best_value + TOL:
                best_value = value
                best_menu = menu
    return MenuRestrictionResult("contiguous", best_value, best_menu)


def solve_greedy_add_one(instance: Instance, K: int) -> MenuRestrictionResult:
    """Add the product giving the best re-priced fixed-menu improvement."""
    if K < 0:
        raise ValueError("K must be nonnegative")
    selected: Tuple[int, ...] = ()
    value = 0.0
    for _ in range(min(K, instance.M)):
        best_value = value
        best_menu = selected
        for product in range(instance.M):
            if product in selected:
                continue
            candidate = tuple(sorted(selected + (product,)))
            candidate_value = fixed_menu_value(instance, candidate)
            if candidate_value > best_value + TOL:
                best_value = candidate_value
                best_menu = candidate
        if best_menu == selected:
            break
        selected = best_menu
        value = best_value
    return MenuRestrictionResult("greedy_add_one", value, selected)


def solve_restricted_pht(instance: Instance, K: int, mode: str) -> RestrictedPhtResult:
    if mode not in {"path", "one_branch"}:
        raise ValueError("mode must be 'path' or 'one_branch'")
    instance.validate()
    if K < 0:
        raise ValueError("K must be nonnegative")
    requested_k = min(K, instance.M)
    relevant = tuple(b for b in instance.buyers if b.theta > TOL)
    if not relevant or requested_k == 0:
        exact = (0.0,) + tuple(NEG_INF for _ in range(requested_k))
        return RestrictedPhtResult(mode, 0.0, exact, tuple(0.0 for _ in exact), 1, 1)

    active_ceilings = tuple(
        c
        for c in sorted({instance.safety_ceiling[b.trust] for b in relevant})
        if c >= 0
    )
    if not active_ceilings:
        exact = (0.0,) + tuple(NEG_INF for _ in range(requested_k))
        return RestrictedPhtResult(mode, 0.0, exact, tuple(0.0 for _ in exact), 1, 1)

    groups: Tuple[Tuple[BuyerType, ...], ...] = tuple(
        tuple(
            buyer
            for buyer in relevant
            if instance.safety_ceiling[buyer.trust] == ceiling
        )
        for ceiling in active_ceilings
    )
    group_count = len(groups)
    product_count = active_ceilings[-1] + 1
    sentinel = product_count
    effective_k = min(requested_k, product_count)
    thresholds = tuple(sorted({0.0} | {float(b.theta) for b in relevant}))

    tail_prefix: List[List[float]] = [
        [0.0] * (group_count + 1) for _ in thresholds
    ]
    for t_index, threshold in enumerate(thresholds):
        for group, buyers in enumerate(groups):
            mass = sum(b.weight for b in buyers if b.theta + TOL >= threshold)
            tail_prefix[t_index][group + 1] = tail_prefix[t_index][group] + mass

    def birth(product: int) -> int:
        if product == sentinel:
            return group_count
        return bisect.bisect_left(active_ceilings, product)

    def quality(product: int) -> float:
        return 0.0 if product < 0 else float(instance.qualities[product])

    def marginal(product: int) -> float:
        return 0.0 if product < 0 else float(instance.marginal_costs[product])

    @functools.lru_cache(maxsize=None)
    def edge_value(parent: int, child: int, boundary: int, threshold_index: int) -> float:
        start = birth(child)
        stop = birth(boundary)
        if stop <= start:
            return NEG_INF
        mass = tail_prefix[threshold_index][stop] - tail_prefix[threshold_index][start]
        slope = thresholds[threshold_index]
        return (
            (slope * (quality(child) - quality(parent)) - (marginal(child) - marginal(parent)))
            * mass
            - instance.fixed_costs[child]
        )

    maximum_degree = 1 if mode == "path" else 2
    maximum_branches = 0 if mode == "path" else 1

    @functools.lru_cache(maxsize=None)
    def subtree(
        parent: int,
        root: int,
        boundary: int,
        incoming_threshold: int,
        size: int,
        branches: int,
    ) -> float:
        if size < 1 or branches < 0 or not (parent < root < boundary):
            return NEG_INF
        edge = edge_value(parent, root, boundary, incoming_threshold)
        if edge == NEG_INF:
            return NEG_INF
        best = NEG_INF
        for degree in range(0, maximum_degree + 1):
            branch_here = 1 if degree >= 2 else 0
            child_branches = branches - branch_here
            if child_branches < 0:
                continue
            children = forest_exact(
                root,
                boundary,
                incoming_threshold,
                size - 1,
                child_branches,
                degree,
            )
            if children != NEG_INF:
                best = max(best, edge + children)
        return best

    @functools.lru_cache(maxsize=None)
    def forest_exact(
        parent: int,
        boundary: int,
        parent_threshold: int,
        size: int,
        branches: int,
        degree: int,
    ) -> float:
        if degree == 0:
            return 0.0 if size == 0 and branches == 0 else NEG_INF
        if size < degree or branches < 0 or boundary - parent - 1 < size:
            return NEG_INF
        best = NEG_INF
        for first in range(parent + 1, boundary):
            best = max(
                best,
                sequence(
                    parent,
                    first,
                    boundary,
                    parent_threshold,
                    len(thresholds) - 1,
                    size,
                    branches,
                    degree,
                ),
            )
        return best

    @functools.lru_cache(maxsize=None)
    def sequence(
        parent: int,
        first: int,
        boundary: int,
        parent_threshold: int,
        upper_sibling_threshold: int,
        size: int,
        branches: int,
        degree: int,
    ) -> float:
        if (
            degree < 1
            or size < degree
            or branches < 0
            or not (parent < first < boundary)
            or parent_threshold > upper_sibling_threshold
        ):
            return NEG_INF
        best = NEG_INF
        for slope_index in range(parent_threshold, upper_sibling_threshold + 1):
            if degree == 1:
                best = max(
                    best,
                    subtree(
                        parent,
                        first,
                        boundary,
                        slope_index,
                        size,
                        branches,
                    ),
                )
                continue

            for next_sibling in range(first + 1, boundary):
                if birth(next_sibling) <= birth(first):
                    continue
                maximum_left = size - (degree - 1)
                for left_size in range(1, maximum_left + 1):
                    for left_branches in range(branches + 1):
                        left = subtree(
                            parent,
                            first,
                            next_sibling,
                            slope_index,
                            left_size,
                            left_branches,
                        )
                        if left == NEG_INF:
                            continue
                        right = sequence(
                            parent,
                            next_sibling,
                            boundary,
                            parent_threshold,
                            slope_index,
                            size - left_size,
                            branches - left_branches,
                            degree - 1,
                        )
                        if right != NEG_INF:
                            best = max(best, left + right)
        return best

    exact_values: List[float] = [0.0]
    frontier: List[float] = [0.0]
    for size in range(1, effective_k + 1):
        best = NEG_INF
        for total_branches in range(maximum_branches + 1):
            for root_degree in range(maximum_degree + 1):
                root_branch = 1 if root_degree >= 2 else 0
                remaining = total_branches - root_branch
                if remaining < 0:
                    continue
                best = max(
                    best,
                    forest_exact(
                        -1,
                        sentinel,
                        0,
                        size,
                        remaining,
                        root_degree,
                    ),
                )
        exact_values.append(best)
        frontier.append(max(frontier[-1], best))

    while len(exact_values) < requested_k + 1:
        exact_values.append(NEG_INF)
        frontier.append(frontier[-1])

    state_count = (
        edge_value.cache_info().currsize
        + subtree.cache_info().currsize
        + forest_exact.cache_info().currsize
        + sequence.cache_info().currsize
    )
    return RestrictedPhtResult(
        mode=mode,
        profit_supremum=max(frontier),
        exact_size_profit=tuple(exact_values),
        capacity_frontier=tuple(frontier),
        state_count=state_count,
        threshold_count=len(thresholds),
    )
