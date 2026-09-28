# Joint Catalog Selection and Pricing for Data Subscriptions

This repository contains the experimental materials and [complete proofs](theory-and-proofs.pdf) accompanying the paper. It corresponds to the manuscript snapshot dated 28 September 2026. The experiment inputs, original observations, solver implementations, and figure scripts are included.

## Quick start

Use Python 3.12. Linux is required for fresh timed solves because the experiment runners use process limits, CPU affinity, and peak resident memory measurements.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/reproduce.py --output reproduced/check-1
```

The last command regenerates the synthetic inputs, checks stored menus and profits, recomputes the experiment summaries and tables, and redraws Figures 5 and 6. It does not invoke an optimization algorithm. For the 1,398 structure/S1 instances, it verifies regenerated inputs and recomputes statistics from the retained objectives and correction records; those historical records do not contain complete price certificates for every method. Full saved-menu replay covers the threshold scan, joint scan, paired comparison, and planning case. Its final `summary.json` reports `PASS` only when all five experiment checks and the figure-data comparisons succeed. Use a new output directory for each execution.

The returned files include a summary for each experiment, detailed CSV records, regenerated figure data, table TeX, and PDF/PNG/SVG figures. The current paper uses the single-column layouts included here; no chart observations or visual encodings are changed during reproduction.

## Locate a result

| Paper result | Experiment directory | Documentation |
|---|---|---|
| Figure 5: threshold scaling | `experiments/threshold/` | [Threshold scan](docs/THRESHOLD.md) |
| Table II: joint contract/threshold scaling | `experiments/scaling/` | [Joint scaling](docs/SCALING.md) |
| Figure 6(a,b): catalog-selection losses | `experiments/structure/` | [Synthetic suites](docs/STRUCTURE.md) |
| Figure 6(c): capacity frontiers | `experiments/structure/` | [Synthetic suites](docs/STRUCTURE.md) |
| Section V.B: PHT, Greedy, and MILP comparison | `experiments/comparison/` | [Paired comparison](docs/COMPARISON.md), [MILP formulation](docs/MILP.md) |
| Table III: three planning scenarios | `experiments/application/` | [Planning case](docs/APPLICATION.md) |

Figure 5 contains 16 inputs with three repetitions each. Table II uses 12 inputs and 36 repetitions from the stored 14-input, 42-run joint scan; the additional six runs are retained and identified separately. The structure and S1 suites contain 750 and 648 instances. The paired comparison contains 18 inputs, three methods, and three repetitions, giving 162 runs in total. The planning case contains three scenarios and three methods, giving nine runs.

The MILP's 18 numerically optimal runs represent six distinct inputs, each repeated three times. All 54 MILP observations are retained, including 36 internally time-limited runs. The detailed output distinguishes the solver assignment objective, the returned menu's canonical profit, and the solver upper bound.

## Run the algorithms again

The following commands perform small fresh solves using the delivered implementations. They write new observations and do not replace the paper's recorded results.

```bash
python scripts/solve_threshold.py --smoke --output reproduced/threshold-smoke
python scripts/solve_scaling.py --smoke --output reproduced/scaling-smoke
python scripts/solve_structure.py --smoke --output reproduced/structure-smoke
python scripts/solve_comparison.py --smoke --output reproduced/comparison-smoke
python scripts/solve_application.py --smoke --output reproduced/application-smoke
```

Each runner also supports selecting an experiment input or running its full set; see its `--help` and the linked experiment document for the exact options and resource limits. Full runs can be substantially more expensive than the smoke examples. Runtime and memory measurements depend on the machine and environment. The original observations retain their recorded environment and measurement boundary.

## Inputs and implementation

The experiment documents specify quality functions, valuation and permission configurations, cost profiles, seeds, and aggregation rules. Complete inputs are retained so that reproducing a result does not depend on interpreting a prose description. Identical permission/valuation pairs can be aggregated; the validation outputs report both record count and effective type count where applicable.

The scaling scans and planning case use exact rational arithmetic. The structure suites and paired comparison retain their documented floating-point and tie-tolerance conventions. Menu replay checks induced choices and profit; fresh optimization is provided through the separate solve commands above.

The implementations used for the original observations are preserved in each experiment's source directory. New entry points make paths relative to this repository and keep output directories separate. [Implementation notes](docs/IMPLEMENTATION_NOTES.md) describe the packaging changes. The original source files may retain historical internal names; the manuscript title and result map above identify this release.

The planning scenarios use public timeliness classifications together with explicitly synthetic quality, cost, and demand parameters. The application diagnostics additionally evaluate the independent-scenario menu under negative association; this diagnostic is labeled separately from Table III.

## Files and versions

`paper_reference/` contains the frozen figure data, the two table sources, reference figures, and manuscript hashes used to check the correspondence with the paper. `MANIFEST.json` records the delivered file hashes. Reproduction checks these before calculating results. The package includes no full-paper PDF; the proof document uses the notation and numbering of the main paper.

The plotting font's existing attribution and license are retained in `figures/figure_font_copyright.txt`. Exact dependency versions for this release are listed in `requirements.txt`; the recorded timing environments are preserved with the observations.
