"""Output-only adapters: frozen search/formulation functions remain unchanged."""
from __future__ import annotations
import math
import time
from dataclasses import dataclass

@dataclass(frozen=True)
class GreedyRecovered:
    menu: tuple
    prices: tuple
    profit_supremum: float
    fixed_menu_calls: int
    internal_pht_calls: int
    candidate_trace: tuple


def greedy_with_existing_prices(instance, K):
    import pht_structure_baselines as original
    from fixed_menu_pricing import solve_fixed_active_menu
    prior = original.fixed_menu_value
    solutions, trace = {}, []
    def recording_value(inst, products):
        chosen = tuple(sorted(set(products)))
        # Exactly the original fixed_menu_value computation: no memoization,
        # additional solver call, changed iteration, or changed comparison.
        solution = solve_fixed_active_menu(inst, chosen)
        value = float(solution.profit_supremum)
        solutions[chosen] = solution
        trace.append({'menu': chosen, 'value': value})
        return value
    original.fixed_menu_value = recording_value
    try:
        result = original.solve_greedy_add_one(instance, K)
    finally:
        original.fixed_menu_value = prior
    selected = tuple(result.menu)
    prices = tuple(solutions[selected].prices) if selected else ()
    if selected and float(solutions[selected].profit_supremum) != result.profit_supremum:
        raise AssertionError('Recorded selected candidate differs from returned Greedy value')
    return GreedyRecovered(selected, prices, float(result.profit_supremum),
        len(trace), sum(bool(t['menu']) for t in trace), tuple(trace))


def finite_or_none(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def milp_with_raw_diagnostics(instance, K, time_limit):
    import srmd_direct_milp as original
    from scipy.optimize import OptimizeResult
    prior = original.milp
    captured = {}
    def recording_milp(*args, **kwargs):
        raw = prior(*args, **kwargs)
        has = raw.x is not None
        captured.update(has_incumbent=has, scipy_status=int(raw.status),
            message=str(raw.message), raw_min_objective=finite_or_none(getattr(raw, 'fun', None)),
            raw_min_dual_bound=finite_or_none(getattr(raw, 'mip_dual_bound', None)),
            raw_mip_gap=finite_or_none(getattr(raw, 'mip_gap', None)),
            raw_mip_node_count=finite_or_none(getattr(raw, 'mip_node_count', None)))
        # The frozen wrapper casts optional fields with float()/int(). Supply
        # serialization sentinels only, after optimization has returned. The
        # raw missing fields above remain null, and no-incumbent outputs are
        # never interpreted as a feasible empty menu by the worker.
        safe = OptimizeResult(raw)
        for key, fallback in [('mip_gap', math.inf), ('mip_dual_bound', math.inf),
                              ('mip_node_count', 0)]:
            if getattr(safe, key, None) is None:
                safe[key] = fallback
        return safe
    original.milp = recording_milp
    try:
        result = original.solve_direct_milp(instance, K, time_limit=time_limit,
            mip_rel_gap=0.0, require_nondecreasing_prices=False)
    finally:
        original.milp = prior
    captured['profit_upper_bound'] = (-captured['raw_min_dual_bound']
        if captured['raw_min_dual_bound'] is not None else None)
    captured['weak_ic_incumbent_objective'] = (-captured['raw_min_objective']
        if captured['has_incumbent'] and captured['raw_min_objective'] is not None else None)
    return result, captured
