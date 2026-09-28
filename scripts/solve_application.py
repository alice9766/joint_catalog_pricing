#!/usr/bin/env python3
"""Solve application scenarios afresh; every invocation requires a new output directory."""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'experiments' / 'application'
METHODS = {'pht': 'FULL_PHT_EXACT', 'greedy': 'GREEDY_ADD_ONE_EXACT', 'endpoints': 'ENDPOINTS_ONLY_EXACT'}
SCENARIOS = ('negative', 'independent', 'positive')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true', help='solve the independent scenario once with PHT')
    parser.add_argument('--scenario', choices=(*SCENARIOS, 'all'), default='all')
    parser.add_argument('--method', choices=(*METHODS, 'all'), default='all')
    parser.add_argument('--output', type=Path, required=True, help='new directory; existing directories are refused')
    parser.add_argument('--timeout', type=float, default=300, help='wall-clock seconds per whole scenario-method worker (default: 300)')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke:
        args.scenario, args.method = 'independent', 'pht'
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.output.resolve().is_relative_to((APP / 'recorded_runs').resolve()):
        parser.error('the archived recorded_runs directory is read-only evidence')
    if args.worker:
        if (args.output / 'worker_result.json').exists():
            parser.error('refusing to overwrite an existing worker result')
        if args.scenario == 'all' or args.method == 'all':
            parser.error('internal worker requires one scenario and one method')
        spec = importlib.util.spec_from_file_location('application_engine', APP / 'engine.py')
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        engine.worker(argparse.Namespace(input=APP / 'inputs' / f'APP53-{args.scenario}.json', method=METHODS[args.method], result=args.output / 'worker_result.json'))
        return
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = [APP / 'engine.py', Path(__file__).resolve(), *sorted((APP / 'vendor').glob('*.py'))]
    write(out / 'environment.json', {'python':sys.version, 'platform':platform.platform(), 'machine':platform.machine(), 'processor':platform.processor(), 'cpu_count':os.cpu_count(), 'started_utc':datetime.now(timezone.utc).isoformat(), 'arithmetic':'fractions.Fraction', 'execution':'sequential workers; whole-method timeout includes process launch, imports, solving, replay and output', 'timeout_seconds':args.timeout, 'code_sha256':{p.relative_to(ROOT).as_posix():sha(p) for p in sources}})
    records = []
    scenarios = SCENARIOS if args.scenario == 'all' else (args.scenario,)
    methods = tuple(METHODS) if args.method == 'all' else (args.method,)
    for scenario in scenarios:
        for method in methods:
            run_id = f'APP53-{scenario}--{METHODS[method]}'
            run_dir = out / run_id
            run_dir.mkdir()
            cmd = [sys.executable, str(Path(__file__).resolve()), '--worker', '--scenario', scenario, '--method', method, '--output', str(run_dir)]
            started = time.monotonic()
            exit_code = None
            with (run_dir / 'stdout.log').open('w') as stdout, (run_dir / 'stderr.log').open('w') as stderr:
                try:
                    process = subprocess.run(cmd, stdout=stdout, stderr=stderr, timeout=args.timeout, check=False)
                    exit_code = process.returncode
                    status = 'COMPLETED' if exit_code == 0 else 'ERROR'
                except subprocess.TimeoutExpired:
                    status = 'TIMEOUT'
            record = {'run_id':run_id, 'scenario':scenario, 'method':METHODS[method], 'status':status, 'exit_code':exit_code, 'elapsed_seconds':time.monotonic()-started, 'input_sha256':sha(APP / 'inputs' / f'APP53-{scenario}.json')}
            if status == 'COMPLETED':
                result = read(run_dir / 'worker_result.json')
                reference = read(APP / 'recorded_runs' / run_id / 'worker_result.json')
                equal_profit = Fraction(result['solver_objective']) == Fraction(reference['solver_objective'])
                record.update(status='PASS' if result['status'] == 'COMPLETED_VERIFIED' and equal_profit else 'FAIL', profit=result['solver_objective'], recorded_profit=reference['solver_objective'], recorded_profit_matches=equal_profit, menu=result['independent_replay']['menu'], prices=result['independent_replay']['prices'])
            write(run_dir / 'run_summary.json', record)
            records.append(record)
            print(json.dumps(record), flush=True)
    summary = {'status':'PASS' if all(r['status'] == 'PASS' for r in records) else 'FAIL', 'scope':'Fresh exact solves with canonical replay, compared with recorded objective values; timings are from this new environment.', 'runs':records}
    write(out / 'summary.json', summary)
    if summary['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
