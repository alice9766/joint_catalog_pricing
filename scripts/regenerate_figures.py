#!/usr/bin/env python3
"""Redraw Figures 5 and 6 from independently regenerated experiment data."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threshold-data', type=Path, required=True)
    parser.add_argument('--structure-data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    threshold = json.loads(args.threshold_data.read_text())
    structure = json.loads(args.structure_data.read_text())
    reference = json.loads((ROOT/'paper_reference/section5_figure_data.json').read_text())
    data = {**reference, 'scan_cases': threshold['scan_cases'],
            'loss_records': structure['loss_records'],
            'capacity_records': structure['capacity_records']}
    # The producing verifiers check the raw observations. This final equality
    # check connects those independent reconstructions to the published plots.
    for key in ('scan_cases', 'loss_records', 'capacity_records'):
        if data[key] != reference[key]:
            raise ValueError(f'Regenerated {key} differs from the paper snapshot')
    data['sources'] = {'threshold': 'experiments/threshold',
                       'structure_and_capacity': 'experiments/structure'}
    (out/'figure_data.json').write_text(json.dumps(data, indent=2)+'\n')
    path = ROOT/'figures/draw_section5_english.py'
    spec = importlib.util.spec_from_file_location('paper_figures', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Preserve the original plotting functions. Only redirect their output.
    module.OUT = out
    font = ROOT/'figures/figure_nimbus_roman.ttf'
    module.setup_fonts(font)
    module.draw5(data)
    module.setup_fonts(font)
    module.draw6(data)
    summary = {'status': 'PASS', 'data_equal_to_paper': True,
               'plot_source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'outputs': ['figure5_threshold_scaling.pdf', 'figure6_catalog_capacity.pdf'],
               'note': 'PNG and SVG versions are also generated. PDF metadata can vary between runs.'}
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
