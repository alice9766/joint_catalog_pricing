#!/usr/bin/env python3
"""Verify recorded application results and regenerate Table III using exact arithmetic."""
import argparse
from collections import Counter
import csv
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = ROOT / "experiments" / "application"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serial(value):
    if isinstance(value, F):
        return str(value)
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def audit(args):
    app = APPLICATION
    runs = app / 'recorded_runs'
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    checks, results = [], []

    def check(name, condition, detail=None):
        checks.append(dict(check=name, pass_=bool(condition), detail=serial(detail)))

    plan = read(app / 'evidence/plan_at_launch.json')
    manifest = read(app / 'evidence_manifest.json')
    env = read(runs / 'environment.json')
    status = read(runs / 'status.json')['runs']
    comparison = read(runs / 'comparisons.json')
    check('nine planned groups', len(plan['runs']) == plan['total_groups'] == 9)
    check('recorded plan hash', sha(app / 'evidence/plan_at_launch.json') == env['plan_sha256'])
    check('all nine status rows in frozen order', [x['run_id'] for x in status] == [x['run_id'] for x in plan['runs']])
    for entry in manifest['files']:
        path = app / entry['path']
        check('manifest identity: ' + entry['path'], path.exists() and sha(path) == entry['sha256'] and path.stat().st_size == entry['bytes'])
    for rel, expected in env['code_sha256'].items():
        source = app / rel if rel.startswith('vendor/') else app / 'evidence/original_code' / rel
        check('recorded source identity: ' + rel, sha(source) == expected)
    check('three scenarios and three methods', Counter(x['scenario_id'] for x in plan['runs']) == Counter({'APP53-negative': 3, 'APP53-independent': 3, 'APP53-positive': 3}) and len(set(x['method'] for x in plan['runs'])) == 3)

    spec = importlib.util.spec_from_file_location('application_generator', app / 'generate_inputs.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    for lam, label in generator.SCENARIOS:
        check(label + ': exact generator reproduction', (app / 'inputs' / f'APP53-{label}.json').read_bytes() == generator.encoded(generator.payload(lam, label)))
    all_inputs = {}
    for row in plan['runs']:
        run_id = row['run_id']
        path = app / row['input_path']
        raw = read(path)
        all_inputs[raw['scenario_id']] = raw
        check(run_id + ': input hash identity', sha(path) == row['input_sha256'])
        check(run_id + ': whole-method budget', row['budget_seconds'] == 300 and row['status'] == 'NOT_RUN')
        folder = runs / run_id
        job, ctrl, worker = map(read, (folder / 'job.json', folder / 'controller_result.json', folder / 'worker_result.json'))
        current = next(x for x in status if x['run_id'] == run_id)
        check(run_id + ': job unchanged', job == row)
        check(run_id + ': complete status/exit/deadline', current == ctrl and ctrl['status'] == worker['status'] == 'COMPLETED_VERIFIED' and ctrl['exit_code'] == 0 and ctrl['worker_result_usable'] is True and 0 <= ctrl['elapsed_seconds'] < 300)
        check(run_id + ': worker input and method identity', worker['input_sha256'] == row['input_sha256'] and worker['scenario_id'] == row['scenario_id'] and worker['method'] == row['method'] and worker['arithmetic'] == 'fractions.Fraction')
        check(run_id + ': empty stderr', (folder / 'stderr.log').read_text() == '')
        ins, recorded = raw['instance'], worker['independent_replay']
        q, h, c = (list(map(F, ins[k])) for k in ('qualities', 'fixed_costs', 'marginal_costs'))
        menu, prices = recorded['menu'], list(map(F, recorded['prices']))
        check(run_id + ': menu and price feasibility', len(menu) == len(prices) == len(set(menu)) <= raw['K'] and all(isinstance(m, int) and 0 <= m < len(q) for m in menu) and all(p >= 0 for p in prices))
        price_map = dict(zip(menu, prices))
        replayed = []
        demand = {g: [F(0)] * len(q) for g in range(len(ins['safety_ceiling']))}
        exits = {g: F(0) for g in demand}
        revenue = service = F(0)
        tie_events = []
        for buyer in ins['buyers']:
            theta, weight, group = F(buyer['theta']), F(buyer['weight']), buyer['trust']
            permitted = [m for m in menu if m <= ins['safety_ceiling'][group]]
            alternatives = [{'choice': -1, 'utility': F(0), 'price': F(0), 'quality': F(0)}]
            alternatives.extend({'choice': m, 'utility': theta * q[m] - price_map[m], 'price': price_map[m], 'quality': q[m]} for m in permitted)
            maximal_utility = max(a['utility'] for a in alternatives)
            tied = [a for a in alternatives if a['utility'] == maximal_utility]
            # Explicit successive filters avoid reusing the runner's tuple-max implementation.
            survivors = tied
            for key in ('price', 'quality', 'choice'):
                best_key = max(a[key] for a in survivors)
                survivors = [a for a in survivors if a[key] == best_key]
            chosen = -1 if theta == 0 else survivors[0]['choice']
            utility = F(0) if chosen == -1 else theta * q[chosen] - price_map[chosen]
            if len(tied) > 1:
                tie_events.append({'type': buyer['name'], 'tied_choices': [a['choice'] for a in tied], 'chosen': chosen, 'utility': utility})
            if chosen < 0:
                exits[group] += weight
            else:
                demand[group][chosen] += weight
                revenue += weight * price_map[chosen]
                service += weight * c[chosen]
            replayed.append({'type': buyer['name'], 'trust': group, 'theta': theta, 'weight': weight, 'choice': chosen, 'utility': utility})
        activation = sum((h[m] for m in menu), F(0))
        profit = revenue - service - activation
        groups = {str(g): {'products': serial(demand[g]), 'exit': str(exits[g])} for g in demand}
        check(run_id + ': all eight exact assignments', serial(replayed) == recorded['assignments'])
        check(run_id + ': group demand and exit accounting', groups == recorded['group_demands'] and sum((sum(demand[g]) + exits[g] for g in demand), F(0)) == 80)
        check(run_id + ': revenue/service/all-active fees/profit', all(F(recorded[key]) == value for key, value in [('revenue', revenue), ('service_cost', service), ('activation_cost', activation), ('profit', profit)]) and profit == F(worker['solver_objective']))
        trace_verified = True
        if row['method'] == 'FULL_PHT_EXACT':
            frontier = list(map(F, worker['extra']['capacity_frontier']))
            trace_verified = len(frontier) == raw['K'] + 1 and frontier[0] == 0 and frontier[-1] == profit and frontier == sorted(frontier) and F(worker['extra']['pht_internal_reconstruction_gap']) == 0 and worker['internal_pht_calls'] == 1
        else:
            trace = worker['extra']['search_trace']
            chosen, chosen_value, at = (), F(0), 0
            if row['method'] == 'GREEDY_ADD_ONE_EXACT':
                for step in range(1, raw['K'] + 1):
                    winner, winner_value = chosen, chosen_value
                    for m in range(len(q)):
                        if m in chosen:
                            continue
                        expected = tuple(sorted(chosen + (m,)))
                        t = trace[at]
                        trace_verified &= t['step'] == step and tuple(t['candidate']) == expected
                        if F(t['profit']) > winner_value:
                            winner, winner_value = expected, F(t['profit'])
                        at += 1
                    if winner == chosen:
                        break
                    chosen, chosen_value = winner, winner_value
            else:
                for expected in ((0,), (2,), (0, 2)):
                    t = trace[at]
                    trace_verified &= tuple(t['candidate']) == expected
                    if F(t['profit']) > chosen_value:
                        chosen, chosen_value = expected, F(t['profit'])
                    at += 1
            trace_verified &= at == len(trace) and list(chosen) == menu and chosen_value == profit
            trace_verified &= worker['extra']['fixed_menu_cache_entries'] == len({tuple(t['candidate']) for t in trace}) + 1 and worker['internal_pht_calls'] == len({tuple(t['candidate']) for t in trace})
        check(run_id + ': method decision trace/frontier consistency', trace_verified)
        results.append({'run_id': run_id, 'scenario_id': row['scenario_id'], 'method': row['method'], 'status': ctrl['status'], 'elapsed_seconds': ctrl['elapsed_seconds'], 'menu': menu, 'prices': prices, 'revenue': revenue, 'service_cost': service, 'activation_cost': activation, 'profit': profit, 'assignments': replayed, 'group_demands': groups, 'product_revenue': {str(m): sum(demand[g][m] for g in demand) * price_map[m] for m in menu}, 'product_service_cost': {str(m): sum(demand[g][m] for g in demand) * c[m] for m in menu}, 'product_activation_cost': {str(m): h[m] for m in menu}, 'canonical_tie_events': tie_events, 'internal_pht_calls': worker['internal_pht_calls'], 'zero_demand_activated_products': [m for m in menu if sum(demand[g][m] for g in demand) == 0]})

    for sid, raw in all_inputs.items():
        ins = raw['instance']
        lam = raw['metadata']['lambda']
        expected = [10 - lam*d for d in (-6, -2, 2, 6)] + [10 + lam*d for d in (-6, -2, 2, 6)]
        check(sid + ': frozen synthetic parameters and marginals', ins['qualities'] == [1, 2, 3] and ins['fixed_costs'] == [1, 1, 1] and ins['marginal_costs'] == [0, 1, 2] and ins['safety_ceiling'] == [1, 2] and raw['K'] == 2 and [b['weight'] for b in ins['buyers']] == expected and all(sum(b['weight'] for b in ins['buyers'] if b['trust'] == g) == 40 for g in (0, 1)) and all(sum(b['weight'] for b in ins['buyers'] if b['theta'] == theta) == 20 for theta in (1, 2, 3, 4)))
        candidates = [r for r in results if r['scenario_id'] == sid]
        full = next(r for r in candidates if r['method'] == 'FULL_PHT_EXACT')
        for r in candidates:
            gap = full['profit'] - r['profit']
            r['gap_to_full_pht'] = gap
            comp = next(x for x in comparison if x['scenario_id'] == sid and x['method'] == r['method'])
            check(r['run_id'] + ': recorded cross-method difference', gap == F(comp['absolute_gap_to_full_pht']) and gap >= 0 and comp['reference_dominance_pass'] is True and comp['status'] == r['status'])
        # Cross-check capacity-one value against all three recorded singleton candidates.
        full_extra = read(runs / full['run_id'] / 'worker_result.json')['extra']
        greedy_id = next(r['run_id'] for r in candidates if r['method'] == 'GREEDY_ADD_ONE_EXACT')
        singleton = [F(t['profit']) for t in read(runs / greedy_id / 'worker_result.json')['extra']['search_trace'] if t['step'] == 1]
        check(sid + ': capacity-one recorded candidate cross-check', F(full_extra['capacity_frontier'][1]) == max([F(0)] + singleton))

    # Check source-category fields and the fixed marginals, independently of solving.
    for sid, raw in all_inputs.items():
        cfg = raw['metadata']['source_constrained_configuration']
        hist, delayed, real = cfg['release_delay_seconds']
        check(sid + ': release categories', hist >= 28800 and 600 < delayed < 28800 and 0 <= real <= 600 and hist > delayed > real and cfg['historical_first_access_only'] and cfg['prior_access_same_observation'] == [False, False, False])

    def selected(sid, method):
        return next(r for r in results if r['scenario_id'] == 'APP53-' + sid and r['method'] == method)

    # These literals are the numeric cells of the included current Table III.
    full = selected('independent', 'FULL_PHT_EXACT')
    table_a = [
        ['Public price', full['prices'][0], full['prices'][1], '---'],
        ['Low-access subscriptions', *[F(full['group_demands']['0']['products'][m]) for m in (1, 2)], sum(F(v) for v in full['group_demands']['0']['products'])],
        ['High-access subscriptions', *[F(full['group_demands']['1']['products'][m]) for m in (1, 2)], sum(F(v) for v in full['group_demands']['1']['products'])],
        ['Revenue', full['product_revenue']['1'], full['product_revenue']['2'], full['revenue']],
        ['Service cost', full['product_service_cost']['1'], full['product_service_cost']['2'], full['service_cost']],
        ['Activation cost', full['product_activation_cost']['1'], full['product_activation_cost']['2'], full['activation_cost']],
    ]
    expected_a = [['Public price','6','9','---'], ['Low-access subscriptions','20','0','20'], ['High-access subscriptions','0','20','20'], ['Revenue','120','180','300'], ['Service cost','20','40','60'], ['Activation cost','1','1','2']]
    text_rows_a = [[str(x) for x in row] for row in table_a]
    check('Table III(a) all cells', text_rows_a == expected_a)
    check('Table III(a) profit and exits', full['profit'] == 238 and full['group_demands']['0']['exit'] == full['group_demands']['1']['exit'] == '20')
    expected_b = {'negative': (['6','6'], '234', ['3','6'], '178'), 'independent': (['6','9'], '238', ['2','8'], '198'), 'positive': (['6','9'], '254', ['2','8'], '230')}
    table_b = []
    for label in ('negative', 'independent', 'positive'):
        pht, greedy, endpoint = (selected(label, method) for method in generator.METHODS)
        check('Table III(b) ' + label, (serial(pht['prices']), str(pht['profit']), serial(endpoint['prices']), str(endpoint['profit'])) == expected_b[label] and pht['menu'] == greedy['menu'] == [1, 2] and pht['prices'] == greedy['prices'] and pht['profit'] == greedy['profit'] and endpoint['menu'] == [0, 2])
        table_b.append([label, ', '.join(map(str,pht['prices'])), str(pht['profit']), ', '.join(map(str,endpoint['prices'])), str(endpoint['profit'])])
    # Ensure checked numeric cells agree with the actual frozen Table III source.
    source_tex = (app / 'reference/table3_planning_case.tex').read_text()
    for row in expected_a:
        check('Table III source: ' + row[0], ' & '.join(row) in source_tex)
    for label, (prices, profit, ep, ep_profit) in expected_b.items():
        check('Table III source: ' + label, f" & {', '.join(prices)} & {profit} & {', '.join(ep)} & {ep_profit}" in source_tex)
    check('Table III source: aggregate profit', 'Total profit is 238.' in source_tex)
    check('Table III source: exits', 'Twenty customers in each of the low- and high-access groups exit.' in source_tex)
    with (out / 'table3a.csv').open('w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['Metric','M','H','Total']); w.writerows(text_rows_a)
    with (out / 'table3b.csv').open('w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['Scenario','PHT_Greedy_prices','PHT_Greedy_profit','Endpoint_prices','Endpoint_profit']); w.writerows(table_b)
    # Standalone tabular fragments; no manuscript edits and no compilation needed.
    def latex_table(columns, header, rows):
        slash = chr(92)
        lines = [slash + 'begin{tabular}{' + columns + '}', slash + 'toprule', ' & '.join(header) + ' ' + slash * 2, slash + 'midrule']
        lines.extend(' & '.join(str(x).capitalize() if k == 0 and str(x) in ('negative','independent','positive') else str(x) for k,x in enumerate(row)) + ' ' + slash * 2 for row in rows)
        lines += [slash + 'bottomrule', slash + 'end{tabular}']
        return chr(10).join(lines) + chr(10)
    tex = '% Generated from exact replay of the nine recorded application results.\n'
    tex += '% Requires booktabs. Part (a): independent case; profit 238; 20 exits in each group.\n'
    tex += latex_table('lrrr', ['Metric','M','H','Total'], table_a)
    tex += '\n% Part (b): PHT and Greedy choose M,H; Endpoint chooses L,H.\n'
    tex += latex_table('lcrcr', ['Scenario','PHT / Greedy prices','Profit','Endpoint prices','Profit'], table_b)
    (out / 'table3_generated.tex').write_text(tex)

    # Existing-input diagnostic: reuse independent-case prices under negative association.
    # This is exact arithmetic, not a new optimization run or a result in the main paper.
    neg = all_inputs['APP53-negative']['instance']
    diag_prices = {1: F(6), 2: F(9)}
    diag_revenue = diag_service = F(0)
    diagnostic_assignments = []
    for buyer in neg['buyers']:
        theta, weight, group = F(buyer['theta']), F(buyer['weight']), buyer['trust']
        feasible = [m for m in diag_prices if m <= neg['safety_ceiling'][group]]
        util = {m: theta * F(neg['qualities'][m]) - diag_prices[m] for m in feasible}
        best = max([F(0)] + list(util.values()))
        candidates = [m for m in feasible if util[m] == best]
        choice = max(candidates) if theta > 0 and candidates else -1
        if choice >= 0:
            diag_revenue += weight * diag_prices[choice]
            diag_service += weight * F(neg['marginal_costs'][choice])
        diagnostic_assignments.append({'type': buyer['name'], 'choice': choice, 'weight': weight})
    diag_activation = sum(F(neg['fixed_costs'][m]) for m in diag_prices)
    diag_profit = diag_revenue - diag_service - diag_activation
    adapted_profit = selected('negative','FULL_PHT_EXACT')['profit']
    loss = (adapted_profit - diag_profit) / adapted_profit
    check('fixed-menu diagnostic arithmetic', diag_profit == 222 and adapted_profit == 234 and loss == F(2,39))
    diagnostic = {'status':'PASS' if diag_profit == 222 and adapted_profit == 234 else 'FAIL', 'included_in_current_main_paper':False, 'scope':'Exact evaluation of the independent-case menu under the existing negative-association input; no new scenario or solver run.', 'evaluated_scenario':'APP53-negative', 'menu':[1,2], 'prices':['6','9'], 'profit':diag_profit, 'scenario_optimal_menu':[1,2], 'scenario_optimal_prices':['6','6'], 'scenario_optimal_profit':adapted_profit, 'relative_loss':loss, 'relative_loss_percent':float(100*loss), 'revenue':diag_revenue, 'service_cost':diag_service, 'activation_cost':diag_activation, 'assignments':diagnostic_assignments}
    (out / 'fixed_menu_diagnostic.json').write_text(json.dumps(serial(diagnostic), indent=2) + '\n')

    audit_result = {'status': 'PASS' if all(c['pass_'] for c in checks) else 'FAIL', 'scope': 'Independent exact replay of recorded menus; identity/status/trace/accounting checks only; no solver imported or executed; not an independent global optimality proof.', 'script_sha256': sha(__file__), 'total_groups': len(results), 'total_type_assignments_replayed': sum(len(r['assignments']) for r in results), 'total_internal_pht_calls_recorded': sum(r['internal_pht_calls'] for r in results), 'check_count': len(checks), 'failed_checks': [c for c in checks if not c['pass_']], 'checks': checks, 'results': results, 'observed_result_files': [{'path': str(p.relative_to(runs)), 'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(runs.rglob('*')) if p.is_file()], 'limitations': ['All methods use the same archived PHT pricing engine; no independent optimization oracle was run.', 'Recorded candidate profits are checked for decision-trace consistency, not independently optimized or replayed when candidate prices were not recorded.', 'The three designed scenarios do not estimate real customer demand, prices, operating fees or real-world profits.', 'The archived environment status BATCH_STARTED and plan NOT_RUN describe launch-time metadata; final completion is recorded in the nine controller/status rows.']}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(serial(audit_result), ensure_ascii=False, indent=2) + '\n')
    method_rows, type_rows = [], []
    for r in results:
        flat = {k: serial(r[k]) for k in ('run_id', 'scenario_id', 'method', 'status', 'elapsed_seconds', 'revenue', 'service_cost', 'activation_cost', 'profit', 'gap_to_full_pht', 'internal_pht_calls')}
        flat['menu_indices'] = ';'.join(map(str, r['menu']))
        flat['menu_labels'] = ';'.join('LMH'[m] for m in r['menu'])
        flat['prices_rational'] = ';'.join(map(str, r['prices']))
        for g in (0, 1):
            for m, label in enumerate('LMH'):
                flat[f'group{g}_demand_{label}'] = r['group_demands'][str(g)]['products'][m]
            flat[f'group{g}_exit'] = r['group_demands'][str(g)]['exit']
        method_rows.append(flat)
        for a in r['assignments']:
            type_rows.append(dict(run_id=r['run_id'], scenario_id=r['scenario_id'], method=r['method'], **serial(a), choice_label='EXIT' if a['choice'] < 0 else 'LMH'[a['choice']]))
    for name, rows in [('method_results_9.csv', method_rows), ('type_choices_72.csv', type_rows)]:
        with (out / name).open('w', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({k: audit_result[k] for k in ('status', 'total_groups', 'total_type_assignments_replayed', 'total_internal_pht_calls_recorded', 'check_count', 'failed_checks')}, ensure_ascii=False))
    if audit_result['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    audit(p.parse_args())
