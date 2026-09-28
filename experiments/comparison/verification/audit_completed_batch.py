#!/usr/bin/env python3
"""Read-only R16 ledger/recovery/time audit. Standard library; never solve."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import statistics

METHODS = ('PHT_FLOAT', 'GREEDY_FLOAT', 'MILP_FLOAT')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load(path):
    return json.loads(Path(path).read_text())

def audit(protocol, results):
    config = load(protocol)
    root = protocol.parent.parent
    summary = load(results / 'summary.json')
    rows = [json.loads(line) for line in (results / 'attempts.jsonl').read_text().splitlines()]
    if summary.get('ledger_rows') != 162 or len(rows) != 162:
        raise RuntimeError('Only completed, complete-ledger batches can be audited')
    errors, flags = [], []
    def need(condition, message):
        if not condition:
            errors.append(message)
    tol = config['tolerances']
    def close(a, b):
        return (isinstance(a, (int, float)) and isinstance(b, (int, float))
                and math.isfinite(a) and math.isfinite(b)
                and abs(a-b) <= tol['objective_absolute'] + tol['objective_relative'] * max(abs(a), abs(b)))
    need(sha(protocol) == sha(results / 'protocol_used.json'), 'used/frozen protocol differs')
    for kind in ('source_hashes', 'adapter_hashes'):
        for path, expected in config[kind].items():
            need(sha(root/path) == expected, 'frozen file changed: ' + path)
    expected_order = []
    for rep in range(1, config['repetitions'] + 1):
        for ci, case in enumerate(config['cases']):
            start = (ci + rep - 1) % 3
            for method in METHODS[start:] + METHODS[:start]:
                expected_order.append((case['case_id'], rep, method))
    actual_order = [(r['case_id'], r['repetition'], r['algorithm']) for r in rows]
    need(actual_order == expected_order, 'scheduled order, set or repetition count differs')
    need(len(set(actual_order)) == len(rows), 'duplicate attempt keys')
    case_map = {c['case_id']: c for c in config['cases']}
    instances = {}
    for case in config['cases']:
        file = root/case['input_path']
        need(sha(file) == case['input_sha256'], 'input hash mismatch: ' + case['case_id'])
        instances[case['case_id']] = load(file)
    cert_count, common_replay_count, greedy_trace_count = 0, 0, 0
    for row in rows:
        label = f"{row['case_id']} r{row['repetition']} {row['algorithm']}"
        case = case_map[row['case_id']]
        attempt_dir = results / f"{row['case_id']}--r{row['repetition']}--{row['algorithm']}"
        marker = attempt_dir/'solver_call_marker.json'
        need(marker.exists() == row.get('top_level_method_called',False), label + ': actual call marker differs')
        if marker.exists():
            marker_data = load(marker)
            need(marker_data['algorithm'] == row['algorithm'], label + ': marker algorithm differs')
            need(marker_data['input_sha256'] == case['input_sha256'], label + ': marker input differs')
        need(row['input_sha256'] == case['input_sha256'], label + ': row input mismatch')
        if row.get('worker_input_sha256') is not None:
            need(row['worker_input_sha256'] == case['input_sha256'], label + ': worker input mismatch')
        recorded_cp = row.get('certificate_path')
        if recorded_cp is None:
            need(row['status'] != 'NUMERIC_COMPLETE', label + ': complete but no certificate')
            continue
        # Archive paths are provenance only. Always read the certificate from
        # the supplied current batch, never an old absolute workspace path.
        cp = attempt_dir/'certificate.json'
        need(Path(recorded_cp).name == cp.name and Path(recorded_cp).parent.name == attempt_dir.name,
             label + ': recorded certificate basename/attempt differs')
        need(cp.is_file(), label + ': certificate absent in current batch')
        if not cp.is_file():
            continue
        cert_count += 1
        need(sha(cp) == row.get('certificate_sha256'), label + ': certificate hash mismatch')
        cert = load(cp)
        if row['algorithm'] == 'MILP_FLOAT':
            d = cert['raw_milp_diagnostics']
            if d['raw_min_dual_bound'] is not None:
                need(close(-d['raw_min_dual_bound'], d['profit_upper_bound']), label + ': wrong upper-bound sign')
            else:
                need(d['profit_upper_bound'] is None, label + ': missing bound converted to number')
            if row['status'] == 'NUMERIC_COMPLETE':
                need(row['solver_status'] == 'optimal' and d['raw_mip_gap'] == 0.0, label + ': complete MILP not optimal')
        if not row.get('has_incumbent'):
            need(cert.get('common_replay') is None, label + ': no incumbent but replay exported')
            need(row.get('objective') is None, label + ': no incumbent but objective exported')
            continue
        payload = instances[row['case_id']]
        inst, K = payload['instance'], payload['K']
        solution = cert['solver_result']
        menu, prices = solution['menu'], solution['prices']
        need(menu == row.get('menu') and prices == row.get('prices'), label + ': row/certificate menu or prices mismatch')
        valid = (len(menu) == len(prices) and len(menu) <= K and len(menu) == len(set(menu))
                 and all(type(m) is int and 0 <= m < len(inst['qualities']) for m in menu)
                 and all(isinstance(p, (int, float)) and math.isfinite(p) and p >= 0 for p in prices))
        need(valid, label + ': illegal recovered menu/prices')
        if not valid:
            continue
        price = dict(zip(menu, prices))
        scale = max([1.0] + [b['theta'] * inst['qualities'][m] for b in inst['buyers']
                    for m in range(inst['safety_ceiling'][b['trust']] + 1)])
        eps = tol['utility_relative'] * max([1.0, scale] + prices)
        demands = dict.fromkeys(menu, 0.0)
        assignments = []
        for buyer in inst['buyers']:
            options = [(0.0, 0.0, 0.0, -1)]
            options += [(buyer['theta'] * inst['qualities'][m] - price[m], price[m],
                         inst['qualities'][m], m) for m in menu
                        if m <= inst['safety_ceiling'][buyer['trust']]]
            best = max(o[0] for o in options)
            chosen = -1 if buyer['theta'] == 0 else max(
                (o for o in options if o[0] >= best-eps), key=lambda o: o[1:])[3]
            assignments.append(chosen)
            if chosen != -1:
                demands[chosen] += buyer['weight']
        revenue = sum(price[m] * demands[m] for m in menu)
        delivery = sum(inst['marginal_costs'][m] * demands[m] for m in menu)
        fixed = sum(inst['fixed_costs'][m] for m in menu)
        profit = revenue-delivery-fixed
        replay = cert.get('common_replay')
        need(replay is not None, label + ': incumbent without common replay')
        if replay is None:
            continue
        common_replay_count += 1
        need(assignments == replay['assignment'], label + ': canonical assignment mismatch')
        for key, value in [('profit', profit), ('revenue', revenue), ('marginal_cost', delivery), ('fixed_cost', fixed)]:
            need(close(value, replay[key]), label + ': recomputed ' + key + ' mismatch')
        need(close(profit, row.get('common_replay_profit')), label + ': row/common profit mismatch')
        need(all(close(v, replay['demand'].get(str(m))) for m, v in demands.items()), label + ': demand mismatch')
        if row['status'] == 'NUMERIC_COMPLETE':
            need(close(profit, row['objective']), label + ': complete objective mismatch')
            need(row.get('objective_replay_consistent') is True, label + ': complete without consistency')
            need(row.get('common_replay_completed') is True, label + ': complete without common replay')
        if row['algorithm'] == 'GREEDY_FLOAT':
            trace = solution['candidate_trace']
            selected, value, cursor = (), 0.0, 0
            for _ in range(min(K, len(inst['qualities']))):
                best_menu, best_value = selected, value
                for m in range(len(inst['qualities'])):
                    if m in selected:
                        continue
                    candidate = tuple(sorted(selected + (m,)))
                    if cursor >= len(trace):
                        need(False, label + ': incomplete candidate trace')
                        break
                    item = trace[cursor]
                    cursor += 1
                    need(tuple(item['menu']) == candidate, label + ': candidate order differs')
                    if item['value'] > best_value + 1e-9:
                        best_menu, best_value = candidate, item['value']
                if best_menu == selected:
                    break
                selected, value = best_menu, best_value
            need(cursor == len(trace), label + ': extra or skipped candidate trace')
            need(tuple(menu) == selected and close(value, solution['profit_supremum']), label + ': outer Greedy recovery differs')
            need(solution['fixed_menu_calls'] == solution['internal_pht_calls'] == len(trace), label + ': candidate call count differs')
            greedy_trace_count += 1
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row['case_id'], row['algorithm']].append(row)
    cells, comparisons = [], []
    by_cell = {}
    for case in config['cases']:
        cid = case['case_id']
        for method in METHODS:
            rr = groups[cid, method]
            complete = len(rr) == 3 and all(r['status'] == 'NUMERIC_COMPLETE' for r in rr)
            objectives = [r.get('common_replay_profit') for r in rr]
            stable = all(close(v, objectives[0]) for v in objectives) if all(v is not None for v in objectives) else None
            menus_stable = all(r.get('menu') == rr[0].get('menu') for r in rr)
            prices_stable = all(r.get('prices') == rr[0].get('prices') for r in rr)
            cell = {'case_id':cid, 'algorithm':method, 'statuses':[r['status'] for r in rr],
                    'all_three_complete':complete, 'common_profit_values':objectives,
                    'objectives_stable':stable, 'menus_stable':menus_stable, 'prices_stable_exact_float':prices_stable,
                    'time_median':statistics.median(r['full_function_wall_seconds'] for r in rr) if complete else None}
            if stable is False:
                flags.append(cid + ' ' + method + ': repeat objective instability' +
                    (' (budget-limited MILP variability can be expected; descriptive only)' if method == 'MILP_FLOAT' else ' (investigate deterministic primary-method outputs)'))
            if not menus_stable or not prices_stable:
                flags.append(cid + ' ' + method + ': repeat menu/price changes')
            cells.append(cell)
            by_cell[cid,method] = cell
        p, g, m = [by_cell[cid, method] for method in METHODS]
        pg = p['all_three_complete'] and g['all_three_complete']
        pm = p['all_three_complete'] and m['all_three_complete'] and p['objectives_stable'] and m['objectives_stable']
        pm = bool(pm and all(close(pv, mv) for pv, mv in zip(p['common_profit_values'], m['common_profit_values'])))
        losses = [(pv-gv)/max(1.0,abs(pv)) for pv,gv in zip(p['common_profit_values'],g['common_profit_values'])] if pg else None
        comparisons.append({'case_id':cid, 'PHT_GREEDY_eligible':pg,
             'Greedy_over_PHT_time_ratio':g['time_median']/p['time_median'] if pg else None,
             'normalized_loss_by_repeat':losses,
             'objectives_match_by_repeat':[close(pv,gv) for pv,gv in zip(p['common_profit_values'],g['common_profit_values'])] if pg else None,
             'PHT_MILP_optimal_speed_eligible':pm,
             'MILP_over_PHT_time_ratio':m['time_median']/p['time_median'] if pm else None})
    ratios=[x['Greedy_over_PHT_time_ratio'] for x in comparisons if x['PHT_GREEDY_eligible']]
    counts=dict(collections.Counter(r['status'] for r in rows))
    need(counts == summary['status_counts'], 'status summary differs from ledger')
    need(sum(r.get('top_level_method_called',False) for r in rows) == summary['top_level_method_calls_by_marker'], 'call-marker summary differs')
    return {'status':'PASS' if not errors else 'FAIL', 'solver_calls':0,
        'protocol_sha256':sha(protocol), 'ledger_sha256':sha(results/'attempts.jsonl'),
        'scope':'Completed 162-row ledger, immutable source/input hashes, all returned certificates and common numerical replay, Greedy candidate trace, independent time/quality aggregation; no optimization or mathematical proof audit',
        'ledger_rows':len(rows), 'status_counts':counts, 'certificate_count':cert_count,
        'common_replay_count':common_replay_count, 'greedy_trace_count':greedy_trace_count,
        'errors':errors, 'flags':flags, 'cells':cells, 'comparisons':comparisons,
        'flags_interpretation':'Flags are descriptive, not audit errors. Time-limited MILP incumbents may vary; objective instability in the primary deterministic methods requires explicit investigation.',
        'primary_eligible_inputs':len(ratios),
        'primary_ratio_median':statistics.median(ratios) if ratios else None,
        'primary_ratio_min':min(ratios) if ratios else None,
        'primary_ratio_max':max(ratios) if ratios else None,
        'secondary_optimal_eligible_inputs':sum(c['PHT_MILP_optimal_speed_eligible'] for c in comparisons)}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--protocol',type=Path,required=True)
    ap.add_argument('--results',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    result=audit(a.protocol.resolve(),a.results.resolve())
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('cells','comparisons')},ensure_ascii=False))

if __name__=='__main__':
    main()
