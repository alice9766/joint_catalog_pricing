# Paired comparison of PHT, Greedy, and MILP

The comparison uses 18 fixed synthetic inputs, with each of three methods run three times. It supports the paired timing and profit comparison in Section V. The retained observations comprise all 162 attempts, including time-limited MILP solutions and differences between weak-incentive-compatible assignment objectives and canonical menu profits.

## Files and inputs

All paths below are relative to the repository root.

| Path | Contents |
|---|---|
| `experiments/comparison/inputs/` | The 18 actual solver inputs |
| `experiments/comparison/protocol/input_manifest.json` | Input hashes, generator settings, effective type counts, and source identifiers |
| `experiments/comparison/protocol/protocol_frozen.json` | Fixed selection, run order, resource limits, solver settings, tolerances, and source hashes |
| `experiments/comparison/vendor/` | Unmodified PHT, fixed-catalog repricing, Greedy support, and MILP source used in the recorded comparison |
| `experiments/comparison/code/` | Unmodified adapters, workers, process controls, and historical input-selection code |
| `experiments/comparison/results/formal_v1/` | Complete original ledger, environment, and 162 per-attempt records and certificates |
| `experiments/comparison/snapshot_manifest.json` | Hashes of the retained original files |
| `experiments/comparison/validation/` | Regenerated verification, CSV diagnostics, and the separate validation runs performed when preparing this package |

The input selection is the Cartesian product of three demand families (`balanced`, `access_skewed`, `correlated`), two configurations, and the original first three integer seeds, 2027091000–2027091002. The seed integers are reproducibility identifiers.

| Configuration | Candidate contracts M | Permission groups B | Capacity K | Distinct thresholds including zero T | Original records before aggregation |
|---|---:|---:|---:|---:|---:|
| Small | 8 | 4 | 4 | 9 | 128 |
| Larger | 12 | 6 | 6 | 11 | 512 |

The file names use T including zero, consistent with the manuscript. The historical generator's `source_key.T` counts positive valuation values, so it is one smaller. The source input already aggregates repeated customer types. Each input manifest entry reports the actual effective type count, rather than treating the pre-aggregation count as the solver dimension. The structure-suite generation code and inputs are supplied with the structure experiment; this comparison uses the fixed 18 input files directly.

Historical experiment identifiers and absolute paths remain in immutable observation files. The current entry points locate files relative to this repository and never use those recorded absolute paths. Historical preparation code is retained to identify the original selection procedure; it is not the supported entry point for current re-solving.

## Verify recorded observations

No solver is launched by this command. Python's standard library is sufficient.

```bash
python scripts/verify_comparison.py
```

The default output is the new directory `reproduced/comparison-verification`. To place regenerated summaries elsewhere, also choose a directory that does not yet exist:

```bash
python scripts/verify_comparison.py --output generated/comparison-verification
```

The script checks hashes and all 18 inputs, re-evaluates all 162 returned menus under the common canonical rule, reconstructs the 54 Greedy outer selection traces from their recorded candidate values, and independently aggregates repeated timings. It writes:

- `summary.json`, including counts and timing-ratio summaries;
- `certificate_replay.json`, including per-cell and paired results;
- `milp_by_input_and_repeat.csv`, with all 54 MILP observations, primal objectives, canonical profits, profit upper bounds, relative gaps, statuses, repetition numbers, timings, and RSS;
- `comparison_by_input.csv`, with the 18 independent inputs, all three repeated values/statuses, and eligible timing medians.

The expected retained counts are 18 numerically complete optimal MILP attempts, corresponding to six independent inputs; 36 MILP attempts reaching the internal limit; and 21 attempts whose assignment objective differs from canonical menu profit. These 21 observations remain in the tables. Replaying a Greedy trace checks its recorded outer selection, not the optimality of every candidate repricing call. Numerical replay verifies the reported menus under the stated tolerances; the theoretical guarantees and complete proofs are separate.

## Solve again

The recorded environment used Linux, Python 3.12.14, NumPy 2.3.5, and SciPy 1.17.0. Install the pinned numerical dependencies with:

```bash
python -m pip install -r experiments/comparison/requirements.txt
```

Linux is required by this runner for CPU affinity, address-space limits, and process RSS measurement. The current entry point preserves the original source hashes and numerical settings. It writes the current environment and each result to a **new** directory, leaving all published observations unchanged.

A three-contract example checks all three method interfaces:

```bash
python scripts/solve_comparison.py --smoke --method all --output generated/comparison-smoke
```

Run one actual comparison input:

```bash
python scripts/solve_comparison.py --case access_skewed-m8-b4-k4-T9-s2027091000 --method all --output generated/comparison-one-input
```

Replace `all` by `PHT`, `Greedy`, or `MILP` to run one method. The default repetition count is one. The full original comparison is explicitly requested with:

```bash
python scripts/solve_comparison.py --all --method all --repetitions 3 --output generated/comparison-full
```

This schedules 162 attempts and may take substantially longer than verification. Each worker is pinned to one allowed logical CPU, with the recorded numerical-library thread environment set to one, a 4 GiB address-space limit, and a 30-second process limit. MILP receives a 25-second internal limit. The full batch retains the 5,600-second suite limit and the original rotated method order. All attempts and limited outcomes are retained; the code does not retry or replace them. Runtime and solver-limited results may vary by hardware and software environment.

## Interpretation of the comparison

`full_function_wall_seconds` measures the complete method call through common menu replay. Imports, input decoding, and process startup are outside that timer but inside the 30-second process limit. PHT returns its capacity frontier in this call, whereas MILP solves the specified capacity once. The smaller MILP-internal timer is diagnostic only.

PHT–Greedy timing ratios use medians of all three complete repetitions per input. These comparisons do not require equal profits or completed MILP runs. PHT–MILP optimal-time ratios use only inputs with three numerically complete optimal MILP results whose canonical profits agree with PHT. A time-limited solve is not treated as an optimal completed solve. See [MILP.md](MILP.md) for the formulation, price bounds, numerical settings, and the distinction between primal objective, canonical profit, and solver upper bound.

## Package validation

The retained snapshot and complete certificate replay pass verification. Separate runs of the three-contract example and one original eight-contract input exercise all three methods through the new entry point. They are stored under `experiments/comparison/validation/` and do not enter any reported experiment statistic. The package introduces relative-path entry points and CSV exports; it changes no model constraint, solver algorithm, original tolerance, or observed result.
