#!/usr/bin/env python3
"""Re-solve selected Structure/S1 instances with the unchanged experiment solvers."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import sys
import time

from verify_structure import ROOT, DATA, read, load, generated_instance, source_id, digest, write
sys.path.insert(0,str(DATA/'vendor'))
from persistent_hull_tree_dp import solve_persistent_hull_tree
from pht_structure_baselines import solve_restricted_pht,solve_contiguous_products,solve_greedy_add_one
from fixed_menu_pricing import solve_fixed_active_menu

METHODS=('full','path','one_branch','contiguous','greedy','capacity')

def canonical_profit(instance,menu,prices):
    """Replay utilities, accepting zero-utility contracts for positive types.

    Uses the original float reconstruction tie tolerance (1e-7). This does
    not call the legacy strict-outside-option evaluator in exact_srmd.py.
    """
    prices=dict(zip(menu,prices));demand={m:0.0 for m in menu};choices=[]
    for buyer in instance.buyers:
        if buyer.theta<=1e-8: choices.append(-1);continue
        accessible=[m for m in menu if m<=instance.safety_ceiling[buyer.trust]]
        utility={m:buyer.theta*instance.qualities[m]-prices[m] for m in accessible}
        utility[-1]=0.0;best=max(utility.values())
        tied=[m for m,u in utility.items() if u>=best-1e-7]
        selected=max(tied,key=lambda m:(prices[m],instance.qualities[m],m) if m>=0 else (0.0,0.0,-1))
        choices.append(selected)
        if selected>=0:demand[selected]+=buyer.weight
    profit=sum((prices[m]-instance.marginal_costs[m])*demand[m]-instance.fixed_costs[m] for m in menu)
    return {'profit':profit,'demand':demand,'choices':choices}

def run_case(suite,index,row,config,reference,methods):
    instance=generated_instance(suite,row,config);sid=source_id(suite,row)
    output={'suite':suite,'source_row_index':index,'source_id':sid,'methods':{},'status':'PASS'}
    for method in methods:
        if method=='capacity' and suite!='s1': continue
        started=time.perf_counter()
        if method in ('full','capacity'):
            answer=solve_persistent_hull_tree(instance,instance.M if method=='capacity' else row['K'])
        elif method in ('path','one_branch'):
            answer=solve_restricted_pht(instance,row['K'],method)
        else:
            answer=(solve_contiguous_products if method=='contiguous' else solve_greedy_add_one)(instance,row['K'])
        entry={'profit':float(answer.profit_supremum),'solver_wall_seconds':time.perf_counter()-started}
        if method=='capacity':
            actual=list(map(float,answer.capacity_frontier));expected=row['capacity_frontier'];entry['capacity_frontier']=actual
            differences=[abs(a-b) for a,b in zip(actual,expected)]
            passed=len(actual)==len(expected) and max(differences,default=0)<=1e-8*max(1.0,abs(expected[-1]))
            entry['maximum_frontier_difference']=max(differences,default=0)
        else:
            expected=reference['reference_profit'] if method=='full' else reference['baseline_profit'][method]
            entry['archived_profit']=expected;entry['absolute_profit_difference']=abs(entry['profit']-expected)
            passed=entry['absolute_profit_difference']<=1e-8*max(1.0,abs(expected))
        if method in ('full','capacity','contiguous','greedy'):
            if method in ('contiguous','greedy'):
                repriced=solve_fixed_active_menu(instance,answer.menu)
                menu,prices=repriced.menu,repriced.prices
            else:menu,prices=answer.menu,answer.prices
            replay=canonical_profit(instance,menu,prices)
            entry.update(menu=list(menu),prices=list(map(float,prices)),canonical_replay_profit=replay['profit'],canonical_demand=replay['demand'])
            entry['canonical_replay_difference']=abs(entry['profit']-replay['profit'])
            passed=passed and entry['canonical_replay_difference']<=1e-6*max(1.0,abs(entry['profit']))
        entry['matches_archived_result']=bool(passed);output['methods'][method]=entry
        if not passed:output['status']='FAIL'
    return output

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',choices=('structure','s1','all'),default='structure')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--indices',help='Comma-separated zero-based source row indices, e.g. 0,1')
    g.add_argument('--all',action='store_true',help='Explicitly solve all instances in the selected suite(s)')
    g.add_argument('--smoke',action='store_true',help='Solve stored source row 0 (Structure suite by default)')
    p.add_argument('--methods',default=','.join(METHODS),help='Comma-separated methods: '+','.join(METHODS))
    p.add_argument('--output',type=Path,required=True,help='New output directory, relative to the repository root')
    a=p.parse_args();methods=a.methods.split(',')
    if not methods or any(x not in METHODS for x in methods) or len(methods)!=len(set(methods)):p.error('Invalid or repeated method')
    sources,config=load();reference={r['source_id']:r for r in read(DATA/'reference/figure6_data.json')['loss_records']}
    suites=list(sources) if a.suite=='all' else [a.suite]
    indices=None if a.all else [0] if a.smoke else [int(x) for x in a.indices.split(',')]
    if indices is not None and (not indices or len(indices)!=len(set(indices))):p.error('Indices must be unique')
    tasks=[]
    for suite in suites:
        selected=range(len(sources[suite])) if indices is None else indices
        if any(i<0 or i>=len(sources[suite]) for i in selected):p.error('Source index out of range for '+suite)
        tasks.extend((suite,i,sources[suite][i]) for i in selected)
    out=a.output if a.output.is_absolute() else ROOT/a.output;out.mkdir(parents=True,exist_ok=False)
    write(out/'run.json',{'python':platform.python_version(),'argv':sys.argv[1:],'methods':methods,'selected':[{'suite':s,'index':i,'source_id':source_id(s,r)} for s,i,r in tasks],
                         'scope':'Fresh serial solver calls; timings are diagnostic and do not replace manuscript benchmarks.','float_mode':True})
    results=[];start=time.perf_counter()
    with (out/'results.jsonl').open('x') as stream:
        for suite,index,row in tasks:
            result=run_case(suite,index,row,config,reference[source_id(suite,row)],methods)
            stream.write(json.dumps(result,allow_nan=False)+'\n');stream.flush();results.append(result)
            print(json.dumps({'suite':suite,'index':index,'status':result['status'],'completed':len(results),'planned':len(tasks)}),flush=True)
    summary={'status':'PASS' if all(r['status']=='PASS' for r in results) else 'FAIL','instances':len(results),
             'method_calls':sum(len(r['methods']) for r in results),'additional_fixed_catalog_replays':sum(sum(m in r['methods'] for m in ('contiguous','greedy')) for r in results),
             'methods':methods,'elapsed_seconds':time.perf_counter()-start,'scope':'Only selected instances re-solved; every listed comparison uses the corrected archived reference.'}
    write(out/'summary.json',summary)
    if summary['status']!='PASS':raise SystemExit(1)

if __name__=='__main__':main()
