#!/usr/bin/env python3
"""Price a fixed activated catalog, including its zero-demand products.

For fixed S the activation charge is constant.  Project to S, set activation
costs to zero, solve the at-most-|S| PHT problem, and subtract the original
charge of every product in S.  A product omitted by the normalized planner is
still activated and receives a price above all accessible buyer valuations.
This gives the original fixed-catalog optimum without exact-cardinality PHT
states or exponential enumeration of relevant subsets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Iterable, Sequence, Tuple, Union

from exact_srmd import Instance
from persistent_hull_tree_dp import _as_fraction, solve_persistent_hull_tree


Number = Union[float, Fraction]


@dataclass(frozen=True)
class FixedActiveSolution:
    menu: Tuple[int, ...]
    prices: Tuple[Number, ...]
    relevant_menu: Tuple[int, ...]
    relevant_prices: Tuple[Number, ...]
    profit_supremum: Number


def project_instance(instance: Instance, products: Sequence[int]) -> Instance:
    """Keep product IDs in sorted order and preserve eligibility prefixes."""
    chosen = tuple(sorted(set(products)))
    if not chosen:
        raise ValueError("projected instance requires at least one product")
    if any(m < 0 or m >= instance.M for m in chosen):
        raise ValueError("projected product index out of range")
    return Instance(
        qualities=tuple(instance.qualities[m] for m in chosen),
        fixed_costs=tuple(instance.fixed_costs[m] for m in chosen),
        marginal_costs=tuple(instance.marginal_costs[m] for m in chosen),
        safety_ceiling=tuple(
            sum(original <= ceiling for original in chosen) - 1
            for ceiling in instance.safety_ceiling
        ),
        buyers=instance.buyers,
        price_grid=instance.price_grid,
        name=f"{instance.name}-projected-{'-'.join(map(str, chosen))}",
    )


def high_inactive_price(
    instance: Instance,
    active_prices: Iterable[Number],
    *,
    exact: bool = False,
) -> Number:
    """Return a nonnegative price strictly above every accessible valuation."""
    convert = _as_fraction if exact else float
    zero = Fraction(0) if exact else 0.0
    maximum_value = max(
        (
            convert(buyer.theta) * convert(instance.qualities[m])
            for buyer in instance.buyers
            for m in range(instance.M)
            if m <= instance.safety_ceiling[buyer.trust]
        ),
        default=zero,
    )
    bound = max([maximum_value, *(convert(p) for p in active_prices)])
    price = bound + 1
    if not exact:
        if price <= bound:
            price = math.nextafter(float(bound), math.inf)
        if not math.isfinite(price):
            raise OverflowError("inactive price is not finite; use exact=True")
    return price


def solve_fixed_active_menu(
    instance: Instance,
    menu: Sequence[int],
    *,
    exact: bool = False,
) -> FixedActiveSolution:
    """Optimize prices while charging every originally activated product.

    The default float interface and result fields match the historical S2
    helper.  ``exact=True`` additionally preserves rational values for audits.
    Optimal tied menus/prices may differ from the old subset enumeration;
    the objective and activated-catalog semantics are unchanged.
    """
    supplied = tuple(menu)
    chosen = tuple(sorted(set(supplied)))
    if len(chosen) != len(supplied):
        raise ValueError("menu must contain unique products")
    if any(not isinstance(m, int) or m < 0 or m >= instance.M for m in chosen):
        raise ValueError("menu product out of range")
    convert = _as_fraction if exact else float
    zero = Fraction(0) if exact else 0.0
    if not chosen:
        return FixedActiveSolution((), (), (), (), zero)

    projected = project_instance(instance, chosen)
    without_activation_cost = replace(
        projected,
        fixed_costs=tuple(zero for _ in chosen),
    )
    relevant = solve_persistent_hull_tree(
        without_activation_cost,
        len(chosen),
        exact=exact,
    )
    relevant_menu = tuple(chosen[m] for m in relevant.menu)
    relevant_prices = tuple(convert(p) for p in relevant.prices)
    fixed_total = sum((convert(instance.fixed_costs[m]) for m in chosen), zero)
    active_price_map = dict(zip(relevant_menu, relevant_prices))
    inactive_price = high_inactive_price(instance, relevant_prices, exact=exact)
    return FixedActiveSolution(
        menu=chosen,
        prices=tuple(active_price_map.get(m, inactive_price) for m in chosen),
        relevant_menu=relevant_menu,
        relevant_prices=relevant_prices,
        profit_supremum=convert(relevant.profit_supremum) - fixed_total,
    )
