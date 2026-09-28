#!/usr/bin/env python3
"""R16: separate no-solver preparation/preflight, excluded smoke, and frozen run."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import statistics
import sys
import time
from t_scan_runner import (atomic_json, append_json, environment, execute, now, sha,
                           SuiteBudgetExhausted)
from benchmark_worker import close
ROOT=Path(__file__).resolve().parents[1]
WORKER=ROOT/'code/benchmark_worker.py'
METHODS=['PHT_FLOAT','GREEDY_FLOAT','MILP_FLOAT']
DRAFT=ROOT/'protocol/protocol_draft.json'
FROZEN=ROOT/'protocol/protocol_frozen.json'

def draft():
 if FROZEN.exists():raise RuntimeError('Already frozen')
 manifest=ROOT/'protocol/input_manifest.json'
 data=json.loads(manifest.read_text())
 config={'protocol_id':'SafeRefresh-R16-time-quality-v1','status':'DRAFT_NOT_RUN',
  'created_at':now(),'case_count':18,'repetitions':3,'scheduled_top_level_attempts':162,
  'selection':data['selection'],'cases':data['cases'],
  'seed_order':'Original generator SEED_BASE=2027091000 plus offsets 0,1,2, not asynchronous result-row order',
  'seed_clarification':'Clarified before any formal solve; original seed integers unchanged',
  'aggregation':'Median of three complete replicate times per method/input, all replicates retained; never minimum time; flag objective/menu instability',
  'input_manifest_sha256':sha(manifest),
  'limits':{'per_attempt_wall_seconds':30,'milp_internal_seconds':25,
   'per_worker_address_space_bytes':4294967296,'suite_wall_seconds':5600,
   'timeout_kill_grace_seconds':2,'launch_reserve_seconds':32,
   'parallel_workers':1,'cpu_threads':1},
  'tolerances':{'utility_relative':1e-9,'objective_absolute':1e-7,'objective_relative':1e-9},
  'primary_comparison':'PHT versus complete outer Greedy, independently of MILP completion',
  'pair_eligibility':{'PHT_GREEDY':'both numerically complete and own replay consistent, same input hash; equal profits NOT required',
   'PHT_MILP_speed_ratio':'both numerically complete, MILP optimal with raw gap 0, objectives/common replay agree, same input hash',
   'replicate_summary':'require three eligible times for method median; incomplete cells keep individual observations and full denominator',
   'quality':'Greedy normalized loss=(PHT-Greedy)/max(1,abs(PHT)); MILP canonical incumbent distinct from weak-IC objective and profit upper bound'},
  'secondary_comparison':'PHT versus direct assignment/activation MILP',
  'capacity':'One at-most-K objective; no monotone price requirement; additive original fixed/delivery cost',
  'timing':{'primary':'full_function_wall_seconds includes complete entry through common replay',
   'excluded':['imports','process spawn','input decoding','post-replay validation','certificate serialization'],
   'outer_deadline':'30 seconds from process spawn, including imports and output',
   'greedy':'all candidate PHT calls including final unimproving round; stored selected prices; no extra call',
   'extra_outputs':'PHT capacity frontier is an included byproduct; MILP not rerun for every k',
   'historical_timing_pooling':False},
  'order':'repetition outer; manifest case order inner; rotate [PHT,GREEDY,MILP] left by (case_index+repetition_index) mod 3',
  'interpretation':'18 fixed synthetic existing inputs; float numerical replay; no universal speedup or real-profit inference',
  'failure_policy':'Keep every scheduled row; no retries/replacements/budget extensions; no timeout imputation',
  'expected_negative_results':'Fast matching Greedy supports practical heuristic use; no posthoc hard-instance search',
  'source_hashes':{str(p.relative_to(ROOT)):sha(p)for p in sorted((ROOT/'vendor').glob('*.py'))},
  'adapter_hashes':{str(p.relative_to(ROOT)):sha(p)for p in sorted((ROOT/'code').glob('*.py'))}}
 atomic_json(DRAFT,config)
 return {'status':'DRAFT_PREPARED','solver_calls':0,'protocol':str(DRAFT)}

def check(config):
 for group in ['source_hashes','adapter_hashes']:
  for p,d in config[group].items():
   assert sha(ROOT/p)==d,('hash mismatch',p)
 assert sha(ROOT/'protocol/input_manifest.json')==config['input_manifest_sha256']
 for c in config['cases']:
  assert sha(ROOT/c['input_path'])==c['input_sha256']
 assert len(config['cases'])==18 and config['repetitions']==3
 return {'status':'HASH_CHECK_PASS','solver_calls':0}

def freeze():
 config=json.loads(DRAFT.read_text());check(config)
 if FROZEN.exists():raise RuntimeError('Refusing to replace frozen protocol')
 config.update(status='FROZEN_NOT_RUN',frozen_at=now(),draft_sha256=sha(DRAFT))
 with FROZEN.open('x') as f:json.dump(config,f,sort_keys=True,indent=2);f.write('\n')
 return {'status':'FROZEN_NOT_RUN','protocol_sha256':sha(FROZEN),'solver_calls':0}

def base_job(config,case,method,folder,cpu):
 return {'mode':'solve','attempt_id':folder.name,'attempt_dir':str(folder),'algorithm':method,
  'cpu':cpu,'memory_limit_bytes':config['limits']['per_worker_address_space_bytes'],
  'source_dir':str(ROOT/'vendor'),
  'source_hashes':{str(ROOT/p):d for p,d in config['source_hashes'].items()},
  'input_path':str(ROOT/case['input_path']),'input_sha256':case['input_sha256'],
  'K':case['source_key']['K'],'M':case['source_key']['M'],
  'milp_internal_seconds':config['limits']['milp_internal_seconds'],'tolerances':config['tolerances']}

def run_attempt(config,job,output,suite_state=None,worker=WORKER,wall=None):
 limits=config['limits']
 row,payload=execute(job,wall or limits['per_attempt_wall_seconds'],limits['timeout_kill_grace_seconds'],
   event_path=output/'events.jsonl',worker_path=worker,
   complete_statuses=('NUMERIC_COMPLETE',),suite_state=suite_state)
 row['worker_payload']=payload
 marker=Path(job['attempt_dir'])/'solver_call_marker.json'
 row['top_level_method_called']=marker.exists()
 # Preserve returned nonoptimal incumbents/bounds/certificates. A hard timeout
 # still has precedence; any late payload is retained only as excluded diagnostic.
 if payload:
  for key in ['full_function_wall_seconds','objective','internal_replay_profit','common_replay_profit',
              'checks','solver_status','diagnostic','has_incumbent','common_replay_completed',
              'objective_replay_consistent',
              'certificate_path','certificate_sha256','menu','prices',
              'global_optimal_in_float_solver_semantics']:
   row[key]=payload.get(key)
  row['worker_input_sha256']=payload.get('input_sha256')
  row['missing_output_reason']=('Hard process deadline exceeded; any returned payload is excluded from completed comparisons.'
    if row['status']=='TIMEOUT' else None)
  row['payload_eligible_under_deadline']=row['status']!='TIMEOUT'
 atomic_json(Path(job['attempt_dir'])/'controller_result.json',row)
 return row

def setup(output,config,protocol_path):
 check(config);output.mkdir(parents=True,exist_ok=False)
 cpu=min(os.sched_getaffinity(0));env=environment(cpu,config)
 env['package_versions']={n:importlib.metadata.version(n)for n in ['numpy','scipy']}
 env.update(protocol_sha256=sha(protocol_path),adapter_hashes=config['adapter_hashes'])
 atomic_json(output/'environment.json',env)
 (output/'protocol_used.json').write_bytes(protocol_path.read_bytes())
 return cpu,env

def preflight(output,smoke=False):
 config=json.loads(DRAFT.read_text());cpu,env=setup(output,config,DRAFT)
 if env['environment_status']!='PASS':return {'status':'ENV_BLOCKED','environment':env}
 rows=[]
 if not smoke:
  import ctypes
  libc=ctypes.CDLL(None,use_errno=True)
  if libc.prctl(36,1,0,0,0)!=0:raise OSError(ctypes.get_errno(),'PR_SET_CHILD_SUBREAPER')
  for mode,expected,wall in [('probe','PREFLIGHT_OK',10),('dummy_success','DUMMY_OK',10),
                            ('dummy_error','SOLVER_ERROR',10),('dummy_memory','MEMORY_LIMIT',10),
                            ('dummy_timeout','TIMEOUT',0.5)]:
   folder=output/mode;folder.mkdir()
   job={'mode':mode,'attempt_id':mode,'attempt_dir':str(folder),'cpu':cpu,
        'memory_limit_bytes':config['limits']['per_worker_address_space_bytes']}
   row=run_attempt(config,job,output,worker=ROOT/'code/t_scan_worker.py',wall=wall)
   passed=row['status']==expected
   if mode=='dummy_timeout':
    progress=json.loads((folder/'worker_progress.json').read_text())
    child=progress['child_pid']
    waited,_=os.waitpid(child,0)
    cleanup=(waited==child and not Path(f'/proc/{child}').exists())
    passed=passed and cleanup
    row['dummy_descendant_reaped']=cleanup
   rows.append({'mode':mode,'expected':expected,'pass':passed,'result':row})
  report={'status':'PASS'if all(r['pass']for r in rows)else'FAIL','rows':rows,
          'top_level_method_attempts':0,'solver_calls':0,'formal_benchmark_status':'NOT_RUN'}
 else:
  p=ROOT/'preflight/running_example_float.json'
  case={'input_path':str(p.relative_to(ROOT)),'input_sha256':sha(p),'source_key':{'M':3,'K':3}}
  for method in METHODS:
   folder=output/method;folder.mkdir()
   row=run_attempt(config,base_job(config,case,method,folder,cpu),output)
   rows.append({'algorithm':method,**row})
  report={'status':'PASS'if all(r['status']=='NUMERIC_COMPLETE'for r in rows)else'FAIL',
   'scope':'One existing M3 running example, excluded from formal 18 cases',
   'rows':rows,'top_level_method_attempts':3,
   'greedy_internal_pht_calls':sum((r.get('diagnostic')or{}).get('greedy_internal_pht_calls',0)for r in rows),
   'formal_benchmark_status':'NOT_RUN'}
 atomic_json(output/'summary.json',report);return report

def execute_suite(output):
 config=json.loads(FROZEN.read_text());assert config['status']=='FROZEN_NOT_RUN'
 cpu,env=setup(output,config,FROZEN)
 (output/'execution_claim.json').write_text(json.dumps({'started_at':now(),'scheduled_top_level_attempts':162})+'\n')
 started=time.monotonic();state={'start':started,'limit':config['limits']['suite_wall_seconds']};rows=[]
 for rep in range(config['repetitions']):
  for ci,case in enumerate(config['cases']):
   offset=(ci+rep)%3;order=METHODS[offset:]+METHODS[:offset]
   for method in order:
    row={'case_id':case['case_id'],'case_index':ci,'repetition':rep+1,'algorithm':method,
         'input_sha256':case['input_sha256'],'source_key':case['source_key']}
    if env['environment_status']!='PASS':row.update(status='NOT_RUN_ENV_BLOCKED',top_level_method_called=False)
    elif time.monotonic()-started+config['limits']['launch_reserve_seconds']>state['limit']:
     row.update(status='NOT_RUN_SUITE_BUDGET',top_level_method_called=False)
    else:
     folder=output/f"{case['case_id']}--r{rep+1}--{method}";folder.mkdir()
     job=base_job(config,case,method,folder,cpu)
     try:row.update(run_attempt(config,job,output,state))
     except SuiteBudgetExhausted:row.update(status='NOT_RUN_SUITE_BUDGET',top_level_method_called=False)
    rows.append(row);append_json(output/'attempts.jsonl',row)
    print(json.dumps({'completed_ledger_rows':len(rows),'scheduled':162,'case':case['case_id'],
                      'repetition':rep+1,'method':method,'status':row['status']}),flush=True)
 report={'finished_at':now(),'scheduled_top_level_attempts':162,'ledger_rows':len(rows),
  'top_level_method_calls_by_marker':sum(r.get('top_level_method_called',False)for r in rows),
  'completed_greedy_internal_pht_calls':sum((r.get('diagnostic')or{}).get('greedy_internal_pht_calls',0)for r in rows),
  'greedy_internal_calls_unavailable_for_failed_attempts':sum(r['algorithm']=='GREEDY_FLOAT' and r.get('diagnostic')is None for r in rows),
  'suite_wall_seconds':time.monotonic()-started,
  'status_counts':{s:sum(r['status']==s for r in rows)for s in sorted({r['status']for r in rows})},
  'interpretation':'Raw complete ledger. Separate analysis required; no ratio for failed/replay-mismatch attempts.'}
 atomic_json(output/'summary.json',report);return report

def main():
 p=argparse.ArgumentParser(description=__doc__);a=p.add_mutually_exclusive_group(required=True)
 for flag in ['draft','check','freeze','preflight','smoke','execute']:a.add_argument('--'+flag,action='store_true')
 p.add_argument('--output',type=Path);args=p.parse_args()
 if args.draft:r=draft()
 elif args.freeze:r=freeze()
 elif args.check:r=check(json.loads((FROZEN if FROZEN.exists()else DRAFT).read_text()))
 else:
  if args.output is None:p.error('--output required')
  r=execute_suite(args.output.resolve())if args.execute else preflight(args.output.resolve(),args.smoke)
 print(json.dumps(r,sort_keys=True,allow_nan=False))
if __name__=='__main__':main()
