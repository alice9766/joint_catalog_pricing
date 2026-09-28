#!/usr/bin/env python3
"""Regenerate all 1,398 inputs and recompute Figure 6 statistics without solving."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import itertools
import json
from pathlib import Path
import platform
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'experiments/structure'
FIELDS = {'structure': ('family','M','B','K','T','N','aggregate','seed'),
          's1': ('quality_shape','demand_shape','access_profile','cost_profile','seed_offset')}
METHODS = ('path','one_branch','contiguous','greedy')
TOL = 1e-8

def read(path): return json.loads(Path(path).read_text())
def lines(path): return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'),ensure_ascii=False,allow_nan=False)
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value): Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def close(a,b,scale=1.0):
    if abs(float(a)-float(b)) > 1e-10*max(1.0,abs(float(scale))):
        raise ValueError(f'Numeric mismatch: {a}, {b}')
def source_id(suite,row): return suite+':'+digest({f:row[f] for f in FIELDS[suite]})
def clean(x):
    if x < -TOL: raise ValueError(f'Baseline exceeds reference: {x}')
    return 0.0 if abs(x)<=TOL else x

def stats(values):
    a=np.asarray(values,dtype=float)
    if not len(a) or not np.isfinite(a).all(): raise ValueError('Empty or nonfinite statistics')
    return dict(n=len(a),minimum=float(a.min()),mean=float(a.mean()),median=float(np.quantile(a,.5,method='linear')),
                p95=float(np.quantile(a,.95,method='linear')),maximum=float(a.max()),
                zero_loss_count=int((np.abs(a)<=TOL).sum()),strict_loss_count=int((a>TOL).sum()))

def load():
    return ({'structure':read(DATA/'evidence/structure_results.json')['rows'],
             's1':lines(DATA/'evidence/s1_results.jsonl')},read(DATA/'config.json'))

def generated_instance(suite,row,config):
    sys.path.insert(0,str(DATA/'vendor'))
    if suite=='structure':
        from saferefresh_algorithm_structure_gate import make_benchmark_instance
        return make_benchmark_instance(**{f:row[f] for f in FIELDS[suite] if f!='K'})
    from run_saferefresh_s1 import build_instance_from_payload
    return build_instance_from_payload(row,config)[0]

def verify(output):
    provenance=read(DATA/'provenance.json')
    for f in provenance['files']:
        p=ROOT/f['package_path']
        if p.stat().st_size!=f['bytes'] or sha(p)!=f['sha256']:
            raise ValueError('Original source changed: '+f['package_path'])
    sources,config=load()
    supplied=lines(DATA/'instances.jsonl')
    correction_rows=lines(DATA/'evidence/corrected_baselines.jsonl')
    instances={x['source_id']:x for x in supplied}
    corrections={x['source_id']:x for x in correction_rows}
    if len(instances)!=1398 or len(corrections)!=1398: raise ValueError('Missing or duplicate inputs')
    sys.path.insert(0,str(DATA/'vendor'))
    from saferefresh_algorithm_structure_gate import structure_payloads
    from run_saferefresh_s1 import main_payloads
    schedules={'structure':structure_payloads(),'s1':main_payloads(config)}
    loss_records=[];capacity_records=[];merged=[];input_checks=[]
    for suite,rows in sources.items():
        expected={source_id(suite,x) for x in schedules[suite]}
        actual={source_id(suite,x) for x in rows}
        if len(rows)!=len(expected) or expected!=actual: raise ValueError('Incomplete schedule: '+suite)
        for index,row in enumerate(rows):
            sid=source_id(suite,row);inp=instances[sid];cor=corrections[sid]
            if inp['source_row_index']!=index or cor['source_row_index']!=index: raise ValueError('Source index mismatch')
            if row['status']!='complete' or cor['status']!='complete': raise ValueError('Incomplete result')
            if cor['source_row_sha256']!=digest(row): raise ValueError('Source record hash mismatch')
            if cor['source_key']!={f:row[f] for f in FIELDS[suite]}: raise ValueError('Stable key mismatch')
            inst=generated_instance(suite,row,config);inst.validate();payload=asdict(inst);h=digest(payload)
            if not h==inp['generated_instance_sha256']==cor['generated_instance_sha256']==digest(inp['instance']):
                raise ValueError('Regenerated input mismatch: '+sid)
            if len(inst.buyers)!=row['effective_buyer_records']: raise ValueError('Effective type count mismatch')
            input_checks.append({'source_id':sid,'suite':suite,'source_row_index':index,'sha256':h,'effective_types':len(inst.buyers)})
            optimum=float(row['profit']);denom=max(1.0,abs(optimum));close(row['values']['full'],optimum,denom)
            close(cor['archived_full_pht_profit'],optimum,denom);close(cor['relative_denominator'],denom,denom)
            values={};losses={}
            for method in METHODS:
                old_key='greedy_add_one' if suite=='s1' and method=='greedy' else method
                old=float(row['values'][old_key]);close(row['losses'][old_key],(optimum-old)/denom)
                if method in ('contiguous','greedy'):
                    item=cor['baselines'][method];value=float(item['corrected_profit'])
                    close(item['old_profit'],old,denom);close(item['corrected_normalized_loss'],(optimum-value)/denom)
                    if item['above_archived_full_optimum']: raise ValueError('Baseline inconsistency')
                    menu=item['corrected_menu']
                    if len(menu)>row['K'] or len(menu)!=len(set(menu)): raise ValueError('Invalid catalog size')
                    if method=='contiguous' and menu and menu!=list(range(menu[0],menu[-1]+1)):
                        raise ValueError('Noncontiguous catalog')
                else: value=old
                values[method]=value;losses[method]=clean((optimum-value)/denom)
            entry={'source_id':sid,'suite':suite,'reference_profit':optimum,'baseline_profit':values,'normalized_loss':losses}
            loss_records.append(entry);merged.append((row,entry))
            if suite=='s1':
                frontier=list(map(float,row['capacity_frontier']));terminal=frontier[-1];cden=max(1.0,abs(terminal))
                if len(frontier)!=inst.M+1: raise ValueError('Wrong frontier length')
                close(frontier[0],0);close(frontier[row['K']],optimum,cden)
                if any(frontier[k]<frontier[k-1]-TOL*cden for k in range(1,len(frontier))): raise ValueError('Nonmonotone frontier')
                for k in range(1,len(frontier)):close(row['capacity_marginal_values'][k-1],frontier[k]-frontier[k-1],cden)
                kstar=next(k for k,v in enumerate(frontier) if terminal-v<=TOL*cden)
                if row['k_star']!=kstar: raise ValueError('Wrong minimal capacity')
                capacity_records.append(dict(source_id=sid,capacity_frontier=frontier,normalized_gap=[clean((terminal-v)/cden) for v in frontier],k_star=kstar))
    all_ids={r['source_id'] for r in loss_records}
    if all_ids!=set(instances) or all_ids!=set(corrections): raise ValueError('Coverage mismatch')
    corrsummary=read(DATA/'evidence/correction_summary.json')
    if digest(sorted(all_ids))!=corrsummary['selected_source_ids_sha256'] or digest(sorted(all_ids))!=corrsummary['completed_source_ids_sha256']:
        raise ValueError('Stable ID coverage digest mismatch')
    figure_data=dict(loss_records=loss_records,capacity_records=capacity_records)
    reference=read(DATA/'reference/figure6_data.json')
    if figure_data!=reference: raise ValueError('Current Figure 6 data mismatch')
    summary={'status':'PASS','scope':'All inputs regenerated and archived results recomputed; no optimization calls.',
             'input_count':len(input_checks),'source_files_byte_identical':len(provenance['files']),
             'solver_calls':0,'current_figure6_all_values_equal':True,
             'normalization':{'baseline':'(V_K - baseline_profit) / max(1, abs(V_K))','capacity':'(V_M - V_k) / max(1, abs(V_M))',
                              'zero_tolerance':TOL,'quantile':'NumPy linear interpolation, including zero losses'},
             'suites':{},'s1_cost_groups':{},'capacity':{},
             'environment':{'python':platform.python_version(),'numpy':np.__version__}}
    for suite in sources:
        subset=[r for r in loss_records if r['suite']==suite]
        ntypes=[x['effective_types'] for x in input_checks if x['suite']==suite]
        summary['suites'][suite]={'n':len(subset),'baselines':{m:stats([x['normalized_loss'][m] for x in subset]) for m in METHODS},'effective_type_count_range':[min(ntypes),max(ntypes)]}
    for cost in config['main']['cost_profiles']:
        subset=[r for row,r in merged if r['suite']=='s1' and row['cost_profile']==cost]
        summary['s1_cost_groups'][cost]=stats([r['normalized_loss']['greedy'] for r in subset])
    summary['capacity']={'n':len(capacity_records),'k_star_distribution':dict(sorted(Counter(x['k_star'] for x in capacity_records).items())),
                         'gap_by_k':{str(k):stats([r['normalized_gap'][k] for r in capacity_records]) for k in range(11)}}
    write(output/'summary.json',summary);write(output/'regenerated_figure6_data.json',figure_data)
    write(output/'regenerated_input_checks.json',input_checks)
    (output/'regenerated_instances.jsonl').write_text(''.join(canonical({**instances[x['source_id']],'instance':asdict(generated_instance(x['suite'],sources[x['suite']][x['source_row_index']],config))})+'\n' for x in input_checks))
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path('reproduced/structure_verify'))
    a=p.parse_args();out=a.output if a.output.is_absolute() else ROOT/a.output
    out.mkdir(parents=True,exist_ok=False);summary=verify(out)
    print(json.dumps({'status':summary['status'],'inputs':summary['input_count'],'solver_calls':0,'figure6_equal':True,'output':str(out)}))

if __name__=='__main__': main()
