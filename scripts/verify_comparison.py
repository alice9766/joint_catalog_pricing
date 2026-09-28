#!/usr/bin/env python3
"""Verify all recorded paired-comparison results without launching a solver."""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'experiments/comparison'

def load(p):
    return json.loads(p.read_text())

def csv_write(path, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ROOT/'reproduced/comparison-verification')
    args = ap.parse_args()
    output = args.output.resolve()
    # Keep both recorded observations and prior verification outputs intact.
    if output == BASE/'results/formal_v1' or (BASE/'results').resolve() in output.parents:
        ap.error('Choose an output outside the immutable results directory.')
    output.mkdir(parents=True, exist_ok=False)
    snapshot = load(BASE/'snapshot_manifest.json')
    hashes = []
    for row in snapshot['files']:
        p = BASE/row['path']
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != row['sha256']:
            hashes.append(row['path'])
    spec = importlib.util.spec_from_file_location('comparison_record_verifier', BASE/'verification/audit_completed_batch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    full = module.audit(BASE/'protocol/protocol_frozen.json', BASE/'results/formal_v1')
    (output/'certificate_replay.json').write_text(json.dumps(full, indent=2, allow_nan=False)+'\n')
    rows = [json.loads(line) for line in (BASE/'results/formal_v1/attempts.jsonl').read_text().splitlines()]
    config = load(BASE/'protocol/protocol_frozen.json')
    milp = [r for r in rows if r['algorithm'] == 'MILP_FLOAT']
    numerical = [r for r in milp if r['status']=='NUMERIC_COMPLETE']
    # SciPy status=1 is authoritative even if its human-readable vendor label differs.
    limits = [r for r in milp if r['diagnostic']['scipy_status']==1]
    mismatches = [r for r in milp if not r['checks']['objective_vs_common_replay']]
    cases = {r['case_id'] for r in rows}
    counts = {
        'independent_inputs':len(cases), 'total_attempts':len(rows),
        'milp_attempts':len(milp), 'milp_numerically_complete_attempts':len(numerical),
        'milp_numerically_complete_independent_inputs':len({r['case_id'] for r in numerical}),
        'milp_internal_limit_attempts':len(limits),
        'milp_objective_common_profit_disagreements':len(mismatches),
        'certificates_replayed':full['common_replay_count'],
        'greedy_candidate_traces_replayed':full['greedy_trace_count'],
    }
    expected = dict(independent_inputs=18,total_attempts=162,milp_attempts=54,
                    milp_numerically_complete_attempts=18,milp_numerically_complete_independent_inputs=6,
                    milp_internal_limit_attempts=36,milp_objective_common_profit_disagreements=21,
                    certificates_replayed=162,greedy_candidate_traces_replayed=54)
    failures = list(full['errors']) + ['snapshot hash differs: '+x for x in hashes]
    failures += [f'{k}: expected {v}, found {counts[k]}' for k,v in expected.items() if counts[k]!=v]
    diagnostics = []
    for r in milp:
        d=r['diagnostic']
        diagnostics.append(dict(case_id=r['case_id'],repetition=r['repetition'],status=r['status'],
            solver_status=r['solver_status'],raw_status=d['scipy_status'],has_incumbent=r['has_incumbent'],
            weak_ic_primal_objective=d['weak_ic_incumbent_objective'],canonical_profit=r['common_replay_profit'],
            profit_upper_bound=d['profit_upper_bound'],relative_mip_gap=d['raw_mip_gap'],
            objective_minus_canonical=d['weak_ic_incumbent_objective']-r['common_replay_profit'],
            objective_common_agrees=r['checks']['objective_vs_common_replay'],
            full_function_wall_seconds=r['full_function_wall_seconds'],peak_rss_bytes=r['peak_rss_bytes']))
    csv_write(output/'milp_by_input_and_repeat.csv',diagnostics)
    aggregates=[]
    for case in config['cases']:
        cid=case['case_id']; item={'case_id':cid,'M':case['source_key']['M'],
             'B':case['source_key']['B'],'K':case['source_key']['K'],
             'T_including_zero':case['T_including_zero'],'effective_customer_types':case['effective_buyer_types']}
        for method in ('PHT_FLOAT','GREEDY_FLOAT','MILP_FLOAT'):
            rr=sorted([r for r in rows if r['case_id']==cid and r['algorithm']==method],key=lambda x:x['repetition'])
            prefix=method.replace('_FLOAT','').lower()
            item[prefix+'_statuses']='|'.join(r['status'] for r in rr)
            item[prefix+'_canonical_profits']='|'.join(str(r['common_replay_profit']) for r in rr)
            complete=len(rr)==3 and all(r['status']=='NUMERIC_COMPLETE' for r in rr)
            item[prefix+'_complete_time_median']=statistics.median(r['full_function_wall_seconds'] for r in rr) if complete else None
            if method=='MILP_FLOAT':
                item['milp_primal_objectives']='|'.join(str(r['objective']) for r in rr)
                item['milp_profit_upper_bounds']='|'.join(str(r['diagnostic']['profit_upper_bound']) for r in rr)
                item['milp_relative_gaps']='|'.join(str(r['diagnostic']['raw_mip_gap']) for r in rr)
        aggregates.append(item)
    csv_write(output/'comparison_by_input.csv',aggregates)
    summary={'status':'PASS' if not failures else 'FAIL','solver_calls':0,
       'scope':'Replays every returned menu, checks all Greedy outer candidate traces, immutable hashes, and independently aggregates observed comparisons.',
       **counts,'recorded_status_counts':dict(collections.Counter(r['status'] for r in rows)),
       'primary_time_ratio_median':full['primary_ratio_median'],
       'primary_time_ratio_range':[full['primary_ratio_min'],full['primary_ratio_max']],
       'secondary_optimal_time_inputs':full['secondary_optimal_eligible_inputs'],
       'files_verified':len(snapshot['files']),'errors':failures,
       'numerical_replay_note':'Replay recomputes canonical profit under the frozen floating-point tolerance; it does not independently prove global optimality or re-solve candidate prices.'}
    (output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2))
    return 0 if not failures else 1

if __name__=='__main__':
    raise SystemExit(main())
