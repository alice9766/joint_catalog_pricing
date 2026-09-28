# Joint scaling: Table II

This experiment measures exact PHT optimization as the number of candidate contracts and distinct valuation thresholds increase. All paths below are relative to the repository root. Python's standard library is sufficient. Solving requires Linux; the recorded runs used Python 3.12.14.

## Reproduce the published table from recorded runs

```bash
python scripts/verify_scaling.py --output reproduced/scaling
```

The output directory must not already exist. This command regenerates all 14 formal inputs and the separate smoke input byte for byte, checks the retained source hashes, and independently replays all 42 recorded menus using exact rational arithmetic. It checks client assignments, canonical profits, recovered prices, edge profit contributions, active paths, and consistency of the saved capacity arrays. It then reconstructs `table_ii.csv` and `table_ii.tex` and compares the six displayed rows, rounded to three decimal places, with the frozen paper table. It also writes per-input and per-run CSV files and `summary.json`.

The replay checks the supplied menu and certificate. It is not a second implementation of global optimization, and checking a saved capacity array does not independently establish the optimality of its entries.

## Solve again

```bash
# One solve of the small original smoke input, with the declared resource limits.
python scripts/solve_scaling.py --smoke --output reproduced/scaling-smoke

# List all 14 formal case IDs.
python scripts/solve_scaling.py --list

# A single formal input; use all three repetitions by omitting --repetitions.
python scripts/solve_scaling.py --case MAIN-M08-independent-T20 --repetitions 1 --output reproduced/scaling-one

# All 42 formal runs, in the original declared order.
python scripts/solve_scaling.py --all --output reproduced/scaling-all
```

New runs write into a new directory and never replace the recorded results. Each worker is pinned to one allowed logical CPU and receives a 4 GiB virtual address-space limit and a 300-second wall-time limit, followed by at most one second for termination. `--cpu` selects an allowed CPU explicitly. The default selects the lowest available CPU. Thread-related environment variables are set to one. Each repetition starts a fresh Python process.

The original worker and controller enforce the limits and record actual peak RSS through Linux `wait4`. Successful end-to-end time includes process startup, imports, input loading, exact optimization, reconstruction, independent worker validation, and certificate writing. Solver-only and validation-only times are also retained. Timings obtained on a new machine need not equal the paper's timings. A timeout remains a timeout; its deadline is not substituted into the median of completed runs. The runner continues after time or memory limits and stops subsequent runs after input, solver, reconstruction, or launch errors.

## Which runs belong to Table II?

| Cohort | Configurations | Inputs | Runs | Use |
|---|---|---:|---:|---|
| Main | M in {8,12,24}, T in {20,40}, B=K=4, two workloads | 12 | 36 | Six rows of Table II |
| Supplemental | M=12, T=40, B=K=6, two workloads | 2 | 6 | Retained supplementary measurements |
| Smoke | M=4, T=5, B=K=2, independent workload | 1 | 1 per smoke command | Execution check, outside all formal denominators |

The formal run comprises **14 deterministic inputs and 42 runs**, all completed under the 300-second protocol. The paper table uses **12 inputs and 36 runs**. For each table row, the independent and correlated times are separate medians of three runs; peak RSS is the maximum across all six runs. The extra six formal runs remain in the package but are not counted in the table's denominator. They change B and K together and do not identify their separate effects.

The retained formal source is `p300_formal_v1`, dated 2026-09-25. The archived 30-second experiment is not pooled into these results. Original run identifiers retain their historical names to preserve traceability.

## Exact input construction

There is no random sampling or seed in this scan. `experiments/scaling/code/input_generator.py` contains the original generation function; the verifier invokes it with the retained protocol. Each input JSON stores all rational values as strings.

For contracts indexed by m=0,...,M-1, the quality is q_m=1+5m/(M-1). The B permission ceilings are `(b+1)*M/B-1`, with b=0,...,B-1. The main grid has B=K=4; the supplementary profile has B=K=6. Fixed costs repeat `(2,5,3,7,4,6)` and marginal costs repeat `(0,1,1/2,2,1,5/2)` as m increases. These arrays are fully listed in `protocol/protocol_frozen.json`.

T counts zero together with the distinct positive valuations. Let D=T-1. The positive valuation support is `1+8r/(D-1)` for r=0,...,D-1. Each permission group has total weight 120/B.

In the independent workload, each support point has equal mass within each group. In the correlated workload, write x=r/(D-1). Unnormalized mass is `2-x` in the lowest-permission group, `1+x` in the highest-permission group, and one in every intermediate group. Each group is normalized separately to the same total group weight. The support is identical across groups; only these masses change.

Every input contains 960 raw records, divided equally among permission groups. Within a group, the support points receive either `floor((960/B)/D)` or one additional copy, with the additional copies assigned from the start of the support. A support point's total weight is split equally among its copies. Aggregating identical permission-ceiling/valuation pairs therefore gives:

| Profile | T | Raw records | Distinct effective types |
|---|---:|---:|---:|
| Main B=4 | 20 | 960 | 76 |
| Main B=4 | 40 | 960 | 156 |
| Supplemental B=6 | 40 | 960 | 234 |
| Smoke B=2 | 5 | 960 | 8 |

The `trust` field in the historical input schema indexes a permission group; `safety_ceiling[trust]` is that group's contract ceiling. The retained `price_grid=[0]` field is an unused compatibility field in the exact PHT solver. It does not discretize the prices.

## Files and original environment

- `experiments/scaling/inputs/` contains the 14 formal input files and one smoke input.
- `protocol/` contains the frozen 300-second protocol, the ordered 42-attempt plan, and input-generation checks.
- `vendor/` contains the byte-identical optimizer and input-type modules used in the formal runs.
- `code/` contains the original generation function, worker, and process-controller dependencies. Use the documented `scripts/` entry points; the archival controller's standalone CLI expects the larger original authoring workspace.
- `recorded/attempts/` contains all 42 original certificates, final records, worker records, and launch commands. Repeated snapshots and empty logs are omitted. Historical absolute paths inside these records document the original execution; reproduction resolves all executable inputs and sources relative to this package.
- `recorded/environment.json` records Linux 6.18.44, Python 3.12.14, and AMD EPYC 9V74 80-Core Processor, with one pinned worker CPU. `recorded/metadata.json` preserves the original formal-run metadata.
- `source_manifest.json` records the original source path and SHA-256 of every selected original file. Algorithm, arithmetic, generator, worker, and controller bytes are unchanged. The new verification and solve entry points provide package-relative paths and do not change optimization logic.
- `reference/table2_joint_scale.tex` is the frozen table used for the numerical comparison.
- `validation/` records this package's successful input regeneration, 42 certificate replays, and matching six-row table reconstruction. `validation_smoke/` records one new smoke solve and exact replay; its timing is separate from the paper results.

The implementation and measurements support the declared synthetic input profiles. Raw-record counts, effective-type counts, and the separate T, B, K, M dimensions are provided so that readers can interpret the measured sizes.
