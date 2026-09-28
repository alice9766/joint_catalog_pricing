#!/usr/bin/env python3
"""Exact rational continuous-price oracle for audit-sized SRMD instances.

This oracle enumerates menus and buyer assignments.  For each assignment it
enumerates the vertices of the resulting bounded price polytope and solves the
active linear system with Fraction Gaussian elimination.  It is exponential
and exists only to falsify the polynomial PHT implementation on tiny inputs.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, List, Optional, Sequence, Tuple

from exact_srmd import Instance
from persistent_hull_tree_dp import _as_fraction


Vector = Tuple[Fraction, ...]
Constraint = Tuple[Vector, Fraction]


@dataclass(frozen=True)
class ExactContinuousSolution:
    menu: Tuple[int, ...]
    prices: Tuple[Fraction, ...]
    assignment: Tuple[int, ...]
    profit_supremum: Fraction


def _dot(left: Sequence[Fraction], right: Sequence[Fraction]) -> Fraction:
    return sum((a * b for a, b in zip(left, right)), Fraction(0))


def _solve_square(
    matrix: Sequence[Sequence[Fraction]],
    rhs: Sequence[Fraction],
) -> Optional[Vector]:
    """Return the unique exact solution, or None for a singular matrix."""
    size = len(rhs)
    augmented = [
        [Fraction(value) for value in row] + [Fraction(value)]
        for row, value in zip(matrix, rhs)
    ]
    pivot_row = 0
    for column in range(size):
        pivot = next(
            (
                row
                for row in range(pivot_row, size)
                if augmented[row][column] != 0
            ),
            None,
        )
        if pivot is None:
            return None
        augmented[pivot_row], augmented[pivot] = (
            augmented[pivot],
            augmented[pivot_row],
        )
        scale = augmented[pivot_row][column]
        augmented[pivot_row] = [value / scale for value in augmented[pivot_row]]
        for row in range(size):
            if row == pivot_row:
                continue
            multiplier = augmented[row][column]
            if multiplier == 0:
                continue
            augmented[row] = [
                value - multiplier * pivot_value
                for value, pivot_value in zip(
                    augmented[row],
                    augmented[pivot_row],
                )
            ]
        pivot_row += 1
    return tuple(augmented[row][-1] for row in range(size))


def _solve_bounded_vertex_lp(
    objective: Vector,
    constraints: Sequence[Constraint],
) -> Optional[Tuple[Fraction, Vector]]:
    """Maximize objective*x over a nonempty bounded rational polytope."""
    dimension = len(objective)
    if dimension == 0:
        return Fraction(0), ()
    best_value: Optional[Fraction] = None
    best_point: Optional[Vector] = None
    for active in itertools.combinations(range(len(constraints)), dimension):
        rows = [constraints[index][0] for index in active]
        rhs = [constraints[index][1] for index in active]
        point = _solve_square(rows, rhs)
        if point is None:
            continue
        if any(_dot(row, point) > bound for row, bound in constraints):
            continue
        value = _dot(objective, point)
        if (
            best_value is None
            or value > best_value
            or (value == best_value and point < best_point)
        ):
            best_value = value
            best_point = point
    if best_value is None or best_point is None:
        return None
    return best_value, best_point


def solve_assignment_exact(
    instance: Instance,
    menu: Sequence[int],
    assignment: Sequence[int],
) -> Optional[ExactContinuousSolution]:
    menu_tuple = tuple(sorted(menu))
    assignment_tuple = tuple(assignment)
    if len(assignment_tuple) != len(instance.buyers):
        raise ValueError("assignment length differs from buyer count")
    if not menu_tuple:
        if all(choice == -1 for choice in assignment_tuple):
            return ExactContinuousSolution((), (), assignment_tuple, Fraction(0))
        return None

    qualities = tuple(_as_fraction(value) for value in instance.qualities)
    fixed_costs = tuple(_as_fraction(value) for value in instance.fixed_costs)
    marginal_costs = tuple(
        _as_fraction(value) for value in instance.marginal_costs
    )
    theta = tuple(_as_fraction(buyer.theta) for buyer in instance.buyers)
    weights = tuple(_as_fraction(buyer.weight) for buyer in instance.buyers)
    position = {product: index for index, product in enumerate(menu_tuple)}
    dimension = len(menu_tuple)
    demand = [Fraction(0) for _ in menu_tuple]
    constraints: List[Constraint] = []

    for buyer_index, (buyer, choice) in enumerate(
        zip(instance.buyers, assignment_tuple)
    ):
        accessible = tuple(
            product
            for product in menu_tuple
            if product <= instance.safety_ceiling[buyer.trust]
        )
        if choice == -1:
            for product in accessible:
                row = [Fraction(0) for _ in menu_tuple]
                row[position[product]] = Fraction(-1)
                constraints.append(
                    (
                        tuple(row),
                        -theta[buyer_index] * qualities[product],
                    )
                )
            continue
        if choice not in accessible:
            return None

        demand[position[choice]] += weights[buyer_index]
        row = [Fraction(0) for _ in menu_tuple]
        row[position[choice]] = Fraction(1)
        constraints.append(
            (tuple(row), theta[buyer_index] * qualities[choice])
        )
        for product in accessible:
            if product == choice:
                continue
            row = [Fraction(0) for _ in menu_tuple]
            row[position[choice]] = Fraction(1)
            row[position[product]] = Fraction(-1)
            constraints.append(
                (
                    tuple(row),
                    theta[buyer_index]
                    * (qualities[choice] - qualities[product]),
                )
            )

    max_value = max(
        (
            theta[index] * qualities[product]
            for index, buyer in enumerate(instance.buyers)
            for product in menu_tuple
            if product <= instance.safety_ceiling[buyer.trust]
        ),
        default=Fraction(0),
    )
    upper_bound = max_value + 1
    for index in range(dimension):
        lower = [Fraction(0) for _ in menu_tuple]
        lower[index] = Fraction(-1)
        constraints.append((tuple(lower), Fraction(0)))
        upper = [Fraction(0) for _ in menu_tuple]
        upper[index] = Fraction(1)
        constraints.append((tuple(upper), upper_bound))

    solved = _solve_bounded_vertex_lp(tuple(demand), constraints)
    if solved is None:
        return None
    revenue, prices = solved
    variable_cost = sum(
        (
            demand[index] * marginal_costs[product]
            for index, product in enumerate(menu_tuple)
        ),
        Fraction(0),
    )
    fixed_cost = sum(
        (fixed_costs[product] for product in menu_tuple),
        Fraction(0),
    )
    return ExactContinuousSolution(
        menu=menu_tuple,
        prices=prices,
        assignment=assignment_tuple,
        profit_supremum=revenue - variable_cost - fixed_cost,
    )


def solve_menu_exact(
    instance: Instance,
    menu: Sequence[int],
) -> ExactContinuousSolution:
    menu_tuple = tuple(sorted(menu))
    choice_sets = []
    for buyer in instance.buyers:
        accessible = tuple(
            product
            for product in menu_tuple
            if product <= instance.safety_ceiling[buyer.trust]
        )
        choice_sets.append((-1,) + accessible)

    best: Optional[ExactContinuousSolution] = None
    for assignment in itertools.product(*choice_sets):
        candidate = solve_assignment_exact(instance, menu_tuple, assignment)
        if candidate is None:
            continue
        if (
            best is None
            or candidate.profit_supremum > best.profit_supremum
            or (
                candidate.profit_supremum == best.profit_supremum
                and (candidate.assignment, candidate.prices)
                < (best.assignment, best.prices)
            )
        ):
            best = candidate
    if best is None:
        raise AssertionError("bounded menu LP unexpectedly has no feasible assignment")
    return best


def solve_exact_continuous(
    instance: Instance,
    max_k: int,
    allowed_products: Optional[Iterable[int]] = None,
) -> ExactContinuousSolution:
    instance.validate()
    allowed = tuple(
        sorted(range(instance.M) if allowed_products is None else allowed_products)
    )
    best = ExactContinuousSolution(
        (),
        (),
        tuple(-1 for _ in instance.buyers),
        Fraction(0),
    )
    for size in range(1, min(max_k, len(allowed)) + 1):
        for menu in itertools.combinations(allowed, size):
            candidate = solve_menu_exact(instance, menu)
            if candidate.profit_supremum > best.profit_supremum:
                best = candidate
    return best


def exact_capacity_frontier(
    instance: Instance,
    max_k: int,
) -> Tuple[Fraction, ...]:
    limit = min(max_k, instance.M)
    best_exact: List[Optional[Fraction]] = [Fraction(0)] + [None] * limit
    for size in range(1, limit + 1):
        for menu in itertools.combinations(range(instance.M), size):
            value = solve_menu_exact(instance, menu).profit_supremum
            if best_exact[size] is None or value > best_exact[size]:
                best_exact[size] = value
    frontier = [Fraction(0)]
    for size in range(1, limit + 1):
        value = best_exact[size]
        if value is None:
            value = frontier[-1]
        frontier.append(max(frontier[-1], value))
    return tuple(frontier)
