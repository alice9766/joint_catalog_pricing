#!/usr/bin/env python3
"""One R16 float attempt. Numerical replay does not prove rational optimality."""
from __future__ import annotations
import dataclasses
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
from t_scan_worker import InputInvalid, configure, write_json, sha
from benchmark_adapters import greedy_with_existing_prices, milp_with_raw_diagnostics

def close(a, b, tolerances):
    return (math.isfinite(a) and math.isfinite(b) and
            abs(a - b) <= tolerances["objective_absolute"] +
            tolerances["objective_relative"] * max(abs(a), abs(b)))

def common_replay(instance, menu, prices, K, tolerances):
    """Same canonical tie convention and original cost objective for both methods.

    The tolerance is numerical only: this is not an exact-rational certificate.
    Empty demand does not remove an offered product's fixed cost.
    """
    if len(menu) != len(prices) or len(menu) > K or len(set(menu)) != len(menu):
        raise ValueError("invalid menu cardinality")
    if any(type(m) is not int or not 0 <= m < instance.M for m in menu):
        raise ValueError("invalid product index")
    if any(not math.isfinite(p) or p < 0 for p in prices):
        raise ValueError("nonfinite or negative price")
    valuation = max([1.0] + [b.theta * instance.qualities[m]
                            for b in instance.buyers
                            for m in range(instance.safety_ceiling[b.trust] + 1)])
    utility_tolerance = tolerances["utility_relative"] * max(valuation, *prices, 1.0)
    lookup, demand = dict(zip(menu, prices)), dict.fromkeys(menu, 0.0)
    assignments = []
    for buyer in instance.buyers:
        options = [(-1, 0.0, 0.0, 0.0)]
        options += [(m, buyer.theta * instance.qualities[m] - lookup[m],
                     lookup[m], instance.qualities[m]) for m in menu
                    if m <= instance.safety_ceiling[buyer.trust]]
        best = max(row[1] for row in options)
        tied = [row for row in options if row[1] >= best - utility_tolerance]
        choice = -1 if buyer.theta == 0 else max(tied, key=lambda row: (row[2], row[3], row[0]))[0]
        assignments.append(choice)
        if choice != -1:
            demand[choice] += buyer.weight
    revenue = sum(lookup[m] * demand[m] for m in menu)
    delivery = sum(instance.marginal_costs[m] * demand[m] for m in menu)
    fixed = sum(instance.fixed_costs[m] for m in menu)
    return {"profit": revenue - delivery - fixed, "revenue": revenue,
            "marginal_cost": delivery, "fixed_cost": fixed,
            "assignment": assignments, "demand": demand,
            "utility_tolerance": utility_tolerance, "valuation_scale": valuation}

def solve(job, progress):
    for path, digest in job['source_hashes'].items():
        if sha(path) != digest:
            raise InputInvalid('frozen source changed: ' + path)
    if sha(job['input_path']) != job['input_sha256']:
        raise InputInvalid('input file hash mismatch')
    raw = json.loads(Path(job['input_path']).read_text())
    sys.path.insert(0, job['source_dir'])
    types = importlib.import_module('exact_srmd')
    instance = types.Instance(
        **{key: tuple(raw['instance'][key]) for key in
           ('qualities', 'fixed_costs', 'marginal_costs', 'safety_ceiling', 'price_grid')},
        buyers=tuple(types.BuyerType(**b) for b in raw['instance']['buyers']),
        name=raw['instance']['name'])
    instance.validate()
    K = int(raw['K'])
    if K != job['K'] or instance.M != job['M']:
        raise InputInvalid('instance capacity contract changed')
    algorithm = job['algorithm']
    if algorithm == 'PHT_FLOAT':
        module = importlib.import_module('persistent_hull_tree_dp')
        entry = lambda: module.solve_persistent_hull_tree(instance, K, exact=False, target_size=None)
    elif algorithm == 'GREEDY_FLOAT':
        importlib.import_module('pht_structure_baselines')
        entry = lambda: greedy_with_existing_prices(instance, K)
    elif algorithm == 'MILP_FLOAT':
        importlib.import_module('srmd_direct_milp')
        entry = lambda: milp_with_raw_diagnostics(instance, K, job['milp_internal_seconds'])
    else:
        raise InputInvalid('unknown method')
    progress('SOLVER_STARTED')
    started = time.perf_counter()
    returned = entry()
    result, raw_milp = returned if algorithm == 'MILP_FLOAT' else (returned, None)
    has_incumbent = raw_milp['has_incumbent'] if raw_milp else True
    replay = (common_replay(instance, result.menu, result.prices, K, job['tolerances'])
              if has_incumbent else None)
    full_wall = time.perf_counter() - started
    progress('SOLVER_RETURNED')
    internal_assignment, internal_replay = None, None
    if algorithm == 'PHT_FLOAT':
        objective = float(result.profit_supremum)
        internal_replay = float(result.verified_profit)
        internal_assignment = result.assignment
        solver_status, algorithm_finished, global_optimal = 'complete_float_dp', True, True
        diagnostic = {'state_count': result.state_count, 'threshold_count': result.threshold_count,
            'reconstruction_gap': float(result.reconstruction_gap),
            'capacity_frontier': result.capacity_frontier, 'top_level_pht_calls': 1,
            'greedy_internal_pht_calls': 0, 'internal_tie_tolerance': 1e-7,
            'internal_dp_tolerance': 1e-8}
    elif algorithm == 'GREEDY_FLOAT':
        objective = result.profit_supremum
        solver_status, algorithm_finished, global_optimal = 'complete_greedy', True, False
        diagnostic = {'fixed_menu_calls': result.fixed_menu_calls,
            'greedy_internal_pht_calls': result.internal_pht_calls,
            'candidate_trace': result.candidate_trace,
            'final_extra_repricing_calls': 0, 'outer_improvement_tolerance': 1e-9,
            'internal_replay_not_exposed_by_fixed_menu_helper': True}
    else:
        objective = raw_milp['weak_ic_incumbent_objective']
        internal_replay = float(result.canonical_replay_profit) if has_incumbent else None
        internal_assignment = result.canonical_assignment if has_incumbent else None
        global_optimal = result.status == 'optimal' and raw_milp['raw_mip_gap'] == 0.0
        algorithm_finished = global_optimal
        solver_status = result.status
        diagnostic = {**raw_milp, 'mip_node_count': raw_milp['raw_mip_node_count'],
            'variable_count': result.variable_count, 'constraint_count': result.constraint_count,
            'legacy_solver_internal_wall_seconds_do_not_use_for_ratio': result.wall_seconds,
            'greedy_internal_pht_calls': 0,
            'internal_replay_utility_tolerance': 1e-6 * replay['valuation_scale'] if replay else None}
    checks = {'has_incumbent': has_incumbent,
        'objective_vs_common_replay': (close(objective, replay['profit'], job['tolerances'])
                                     if objective is not None and replay else False)}
    if internal_replay is not None:
        checks.update(objective_vs_internal_replay=close(objective, internal_replay, job['tolerances']),
            internal_vs_common_replay=close(internal_replay, replay['profit'], job['tolerances']),
            internal_vs_common_assignment=list(internal_assignment) == replay['assignment'])
    valid_replay = all(checks.values())
    if not has_incumbent:
        status = 'SOLVER_NONOPTIMAL_NO_INCUMBENT'
    elif not valid_replay:
        status = 'REPLAY_MISMATCH' if algorithm_finished else 'SOLVER_NONOPTIMAL_REPLAY_MISMATCH'
    elif not algorithm_finished:
        status = 'SOLVER_NONOPTIMAL'
    else:
        status = 'NUMERIC_COMPLETE'
    certificate = Path(job['attempt_dir']) / 'certificate.json'
    write_json(certificate, {'solver_result': dataclasses.asdict(result),
        'raw_milp_diagnostics': raw_milp, 'has_incumbent': has_incumbent,
        'common_replay': replay, 'numeric_only': True})
    return {'status': status, 'complete_return': status == 'NUMERIC_COMPLETE',
        'solver_called': True, 'algorithm': algorithm, 'mode': 'float_three_method',
        'solver_status': solver_status, 'global_optimal_in_float_solver_semantics': global_optimal,
        'has_incumbent': has_incumbent, 'common_replay_completed': replay is not None,
        'objective_replay_consistent': valid_replay,
        'full_function_wall_seconds': full_wall, 'objective': objective,
        'internal_replay_profit': internal_replay,
        'common_replay_profit': replay['profit'] if replay else None,
        'checks': checks, 'input_sha256': job['input_sha256'],
        'menu': result.menu if has_incumbent else None,
        'prices': result.prices if has_incumbent else None,
        'diagnostic': diagnostic, 'certificate_path': str(certificate),
        'certificate_sha256': sha(certificate)}


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    directory = Path(job['attempt_dir'])
    reserve, solver_called = None, False
    def progress(stage, **extra):
        nonlocal solver_called
        if stage == 'SOLVER_STARTED':
            write_json(directory / 'solver_call_marker.json', {
                'algorithm': job['algorithm'], 'input_sha256': job['input_sha256'],
                'pid': os.getpid(), 'time_ns': time.time_ns(), 'top_level_method_called': True})
            solver_called = True
        write_json(directory / 'worker_progress.json', {'stage': stage, 'pid': os.getpid(), **extra})
    try:
        controls = configure(job)
        reserve = bytearray(1024 * 1024)
        progress('RESOURCE_CONTROLS_READY', controls=controls)
        if job['mode'] == 'probe':
            result = {'status': 'PREFLIGHT_OK', 'solver_called': False}
        elif job['mode'] == 'solve':
            result = solve(job, progress)
        else:
            raise InputInvalid('unsupported worker mode')
        result['controls'] = controls
        write_json(directory / 'worker_result.json', result)
        progress('COMPLETE', status=result['status'])
        return 0
    except BaseException as exc:
        reserve = None
        status = 'MEMORY_LIMIT' if isinstance(exc, MemoryError) else (
            'INPUT_INVALID' if isinstance(exc, InputInvalid) else 'SOLVER_ERROR')
        traceback.print_exc()
        try:
            write_json(directory / 'worker_result.json', {'status': status,
                'complete_return': False, 'solver_called': solver_called,
                'exception_type': type(exc).__name__, 'message': str(exc)})
        except BaseException:
            pass
        return 2

if __name__ == '__main__':
    sys.exit(main())
