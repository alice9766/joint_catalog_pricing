#!/usr/bin/env python3
"""Solve a comparison input or explicit full batch into a new output directory."""
from __future__ import annotations
import argparse
import collections
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'experiments/comparison'
METHODS={'PHT':'PHT_FLOAT','Greedy':'GREEDY_FLOAT','MILP':'MILP_FLOAT'}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    choice=ap.add_mutually_exclusive_group(required=True)
    choice.add_argument('--smoke',action='store_true',help='Use the separate three-contract example, excluded from the 18-input batch.')
    choice.add_argument('--case',help='Case ID from protocol/input_manifest.json, without .json.')
    choice.add_argument('--all',action='store_true',help='Select all 18 fixed inputs; combine with --repetitions 3 for the full protocol.')
    ap.add_argument('--method',choices=tuple(METHODS)+('all',),default='all')
    ap.add_argument('--repetitions',type=int,choices=(1,2,3),default=1)
    ap.add_argument('--output',type=Path,required=True,help='New directory; existing directories are never overwritten.')
    args=ap.parse_args()
    output=args.output.resolve()
    if output.exists():ap.error('--output must be a new directory.')
    if (BASE/'results').resolve() in output.parents:ap.error('Choose an output outside immutable observed results.')
    if sys.platform!='linux':ap.error('The recorded process limits and resource measurement require Linux.')
    sys.path.insert(0,str(BASE/'code'))
    import benchmark_runner as runner
    config=json.loads((BASE/'protocol/protocol_frozen.json').read_text())
    if args.smoke:
        path=BASE/'preflight/running_example_float.json'
        payload=json.loads(path.read_text())
        cases=[{'case_id':'smoke-three-contracts','input_path':str(path.relative_to(BASE)),
           'input_sha256':runner.sha(path),'source_key':{'M':len(payload['instance']['qualities']),'K':payload['K']}}]
    elif args.case:
        cases=[c for c in config['cases'] if c['case_id']==args.case]
        if not cases:ap.error('Unknown --case; consult experiments/comparison/protocol/input_manifest.json.')
    else:cases=config['cases']
    methods=list(METHODS.values()) if args.method=='all' else [METHODS[args.method]]
    cpu,env=runner.setup(output,config,BASE/'protocol/protocol_frozen.json')
    command={'selected_cases':[c['case_id'] for c in cases],'methods':methods,
        'repetitions':args.repetitions,'smoke':args.smoke,'full_protocol_batch':args.all and args.method=='all' and args.repetitions==3,
        'recorded_model_and_solver_settings_unchanged':True,'observed_results_overwritten':False,
        'script_sha256':runner.sha(__file__),'interpreter':sys.executable}
    runner.atomic_json(output/'run_selection.json',command)
    if env['environment_status']!='PASS':
        report={'status':'ENV_BLOCKED','solver_calls':0,'environment_issues':env['environment_issues']}
        runner.atomic_json(output/'summary.json',report);print(json.dumps(report));return 2
    rows=[]
    started=time.monotonic()
    state={'start':started,'limit':config['limits']['suite_wall_seconds']}
    for rep in range(1,args.repetitions+1):
        for ci,case in enumerate(cases):
            offset=(ci+rep-1)%len(methods)
            for method in methods[offset:]+methods[:offset]:
                row={'case_id':case['case_id'],'case_index':ci,'repetition':rep,
                   'algorithm':method,'input_sha256':case['input_sha256'],'source_key':case['source_key']}
                if time.monotonic()-started+config['limits']['launch_reserve_seconds']>state['limit']:
                    row.update(status='NOT_RUN_SUITE_BUDGET',top_level_method_called=False)
                else:
                    folder=output/f"{case['case_id']}--r{rep}--{method}";folder.mkdir()
                    job=runner.base_job(config,case,method,folder,cpu)
                    try:row.update(runner.run_attempt(config,job,output,state))
                    except runner.SuiteBudgetExhausted:row.update(status='NOT_RUN_SUITE_BUDGET',top_level_method_called=False)
                rows.append(row);runner.append_json(output/'attempts.jsonl',row)
                print(json.dumps({k:row.get(k) for k in ('case_id','algorithm','repetition','status','objective','common_replay_profit')}),flush=True)
    report={'status':'COMPLETE','selected_attempts':len(cases)*len(methods)*args.repetitions,
       'recorded_attempts':len(rows),'solver_calls':sum(r.get('top_level_method_called',False) for r in rows),
       'status_counts':dict(collections.Counter(r['status'] for r in rows)),
       'elapsed_seconds':time.monotonic()-started,
       'all_methods_numerically_complete':all(r['status']=='NUMERIC_COMPLETE' for r in rows),
       'observed_results_overwritten':False,'smoke':args.smoke}
    runner.atomic_json(output/'summary.json',report)
    print(json.dumps(report,indent=2))
    return 0 if len(rows)==report['selected_attempts'] else 1

if __name__=='__main__':raise SystemExit(main())
