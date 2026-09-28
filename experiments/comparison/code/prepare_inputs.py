#!/usr/bin/env python3
"""Freeze a result-blind complete slice of 18 already recovered inputs; zero solves."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parents[1]
BASE=WORKSPACE/'r14_work/public_candidate'
def digest(x):return hashlib.sha256(x).hexdigest()
def objhash(x):return digest(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
def main():
 path=ROOT/'protocol/input_manifest.json'
 if path.exists():raise RuntimeError('Input manifest already exists; refusing replacement')
 recovered=BASE/'inputs/historical_1398.jsonl'
 rows=[json.loads(s)for s in recovered.read_text().splitlines()]
 historical_file=BASE/'r6_work/reference/historical/results/PHT_STRUCTURE_VALUE_GATE_RESULTS.json'
 historical=json.loads(historical_file.read_text())['rows']
 correction_file=BASE/'r6_work/reference/historical/results/p0_correction_results/baseline_recompute.jsonl'
 # Read only correction identity/hash fields; no result-dependent selection.
 corrections={r['source_id']:{k:r[k]for k in ['source_id','source_row_index','source_row_sha256','generated_instance_sha256']}
              for r in map(json.loads,correction_file.read_text().splitlines())}
 cases=[]
 for family in ['balanced','access_skewed','correlated']:
  for M,B,K,T,N in [(8,4,4,8,128),(12,6,6,10,512)]:
   for seed in [2027091000,2027091001,2027091002]:
    key=dict(family=family,M=M,B=B,K=K,T=T,N=N,aggregate=True,seed=seed)
    matches=[r for r in rows if r['suite']=='structure' and r['source_key']==key]
    assert len(matches)==1,(key,len(matches))
    r=matches[0];corr=corrections[r['source_id']];h=historical[r['source_row_index']]
    ih=objhash(r['instance'])
    assert ih==r['generated_instance_sha256']==corr['generated_instance_sha256']
    assert corr['source_row_index']==r['source_row_index']
    assert objhash(h)==corr['source_row_sha256']
    assert {k:h[k]for k in key}==key
    assert r['source_id']=='structure:'+objhash(key)
    actual_T=len({b['theta']for b in r['instance']['buyers']}|{0.0});assert actual_T==T+1
    case_id=f'{family}-m{M}-b{B}-k{K}-T{actual_T}-s{seed}'
    payload={'schema':'saferefresh.r16-existing-input.v1','K':K,'source_key':key,'instance':r['instance']}
    ip=ROOT/'inputs'/(case_id+'.json')
    with ip.open('x') as f:json.dump(payload,f,sort_keys=True,separators=(',',':'));f.write('\n')
    cases.append({'case_id':case_id,'input_path':str(ip.relative_to(ROOT)),
     'input_sha256':digest(ip.read_bytes()),'source_id':r['source_id'],
     'source_row_index':r['source_row_index'],'source_row_sha256':corr['source_row_sha256'],
     'instance_sha256':ih,'source_key':key,'T_including_zero':actual_T,
     'effective_buyer_types':len(r['instance']['buyers']),
     'total_weight':sum(b['weight']for b in r['instance']['buyers'])})
 manifest={'selection':'three families x two existing configurations x the original first three seed integers; no outcome/solvability filter',
   'solver_calls':0,'cases':cases,'case_count':len(cases),'sources':{
    str(p.relative_to(WORKSPACE)):digest(p.read_bytes())for p in [recovered,historical_file,correction_file]}}
 path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
 # Excluded preflight only: one existing running example, not in formal slice.
 raw=json.loads((BASE/'inputs/running_example.json').read_text())
 for k in ['qualities','fixed_costs','marginal_costs','price_grid']:raw[k]=list(map(float,raw[k]))
 for b in raw['buyers']:
  b['theta']=float(b['theta']);b['weight']=float(b['weight'])
 (ROOT/'preflight/running_example_float.json').write_text(json.dumps({'K':3,'instance':raw},indent=2)+'\n')
 print(json.dumps({'status':'INPUTS_PREPARED','cases':len(cases),'solver_calls':0}))
if __name__=='__main__':main()
