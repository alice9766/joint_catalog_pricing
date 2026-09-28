#!/usr/bin/env python3
"""Verify the delivered experiment records, regenerate tables, and redraw figures.

This command does not rerun optimization. Use the solve_*.py commands for that.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SUITES = ('threshold', 'scaling', 'structure', 'comparison', 'application')


def check_files():
    manifest = json.loads((ROOT/'MANIFEST.json').read_text())
    for item in manifest['files']:
        path = ROOT/item['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f'File missing or changed: {item["path"]}')
    return len(manifest['files'])


def run(command, log):
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with log.open('w') as stream:
        completed = subprocess.run(command, cwd=ROOT, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise RuntimeError(f'Command failed ({completed.returncode}); see {log}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='A new directory; stored observations are never overwritten')
    parser.add_argument('--no-figures', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents and 'experiments' in out.relative_to(ROOT).parts:
        raise ValueError('Choose an output outside the stored experiments')
    out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    summary = {'status': 'RUNNING', 'optimizer_calls': 0, 'suites': {}}
    try:
        summary['checked_files'] = check_files()
        for suite in SUITES:
            print(f'Verifying {suite} ...', flush=True)
            run([sys.executable, '-B', str(ROOT/f'scripts/verify_{suite}.py'),
                 '--output', str(out/suite)], out/f'{suite}.log')
            result = json.loads((out/suite/'summary.json').read_text())
            if result.get('status') != 'PASS':
                raise ValueError(f'{suite} verification did not pass')
            summary['suites'][suite] = result
        if not args.no_figures:
            print('Regenerating Figures 5 and 6 ...', flush=True)
            run([sys.executable, '-B', str(ROOT/'scripts/regenerate_figures.py'),
                 '--threshold-data', str(out/'threshold/regenerated_figure5_data.json'),
                 '--structure-data', str(out/'structure/regenerated_figure6_data.json'),
                 '--output', str(out/'figures')], out/'figures.log')
            summary['figures'] = json.loads((out/'figures/summary.json').read_text())
        summary['status'] = 'PASS'
    except Exception as exc:
        summary['status'] = 'FAIL'
        summary['error'] = str(exc)
        raise
    finally:
        summary['elapsed_seconds'] = time.monotonic()-start
        (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({'status': summary['status'], 'output': str(out),
                      'elapsed_seconds': summary['elapsed_seconds']}))


if __name__ == '__main__':
    main()
