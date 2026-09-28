# Threshold scan: Figure 5

This folder reproduces the threshold scan used by the current paper's Figure 5. It contains 16 distinct deterministic inputs and all 48 original runs, with three repetitions per input. All 48 runs completed. The original solver, input generator, controller, worker, inputs, returned menus, exact certificates, environment, resource controls, and individual observations are retained.

## Commands

Run these commands from the repository root. Each output path must be a new directory. The scripts locate their inputs relative to their own file paths and can also be invoked from another working directory.

```bash
python scripts/verify_threshold.py --output outputs/threshold_verification
python scripts/solve_threshold.py --smoke --output outputs/threshold_smoke
python scripts/solve_threshold.py --case A-independent-T20 --output outputs/threshold_case
python scripts/solve_threshold.py --all --output outputs/threshold_all
```

`verify_threshold.py` calls no optimizer. It regenerates all 16 inputs byte for byte, verifies source and input hashes, checks the 48 saved worker/controller records and resource settings, and replays all 48 returned menus using exact rational arithmetic. Replay checks every customer's canonical choice, profit, edge contributions, recovered prices, group paths, and the prefix-maximum relation between exact-size values and the capacity frontier. It recomputes the median, minimum, and maximum of the three original time and RSS observations per input, then compares all observations with the current Figure 5 source data. Replay validates saved certificates and aggregation; it does not re-establish optimality through an independent optimizer or remeasure elapsed time.

`solve_threshold.py` invokes the preserved exact PHT solver under the original per-worker controls. `--smoke` solves the smallest independent input once. `--case` and `--all` use three repetitions unless `--repetitions 1` or `--repetitions 2` is supplied. Every completed run checks its exact objective and full capacity frontier against the corresponding original certificate. The command saves new environment and timing records separately; these observations never replace the published measurements. This solver entry point requires Linux. Verification requires Python 3.10 or later and only the standard library; the published runs used Python 3.12.14.

## Input generation

There are two size profiles. Contract indices start at zero; a group's ceiling is its highest eligible index.

| Profile | M | B | K | Qualities | Permission ceilings | Opening costs | Unit service costs |
|---|---:|---:|---:|---|---|---|---|
| A | 4 | 2 | 2 | 1, 2, 3, 4 | 1, 3 | 2, 5, 3, 7 | 0, 1, 1/2, 2 |
| B | 6 | 3 | 3 | 1, 2, 3, 4, 5, 6 | 1, 3, 5 | 2, 5, 3, 7, 4, 6 | 0, 1, 1/2, 2, 1, 5/2 |

For each profile, use `T = 20, 40, 80, 160` and either `independent` or `correlated` demand. Here T includes the zero threshold. Set D = T − 1. The D positive valuations are equally spaced from 1 to 9 inclusive: valuation r is 1 + 8r/(D − 1), for r = 0, …, D − 1. There is no random seed because generation is deterministic.

Each input has 960 raw records and total customer weight 120. Every permission group has equal mass 120/B and 960/B raw records. For `independent`, every valuation in a group has the same total weight. For `correlated`, write x = r/(D − 1). The unnormalized valuation weights are 2 − x in the lowest permission group, 1 + x in the highest group, and 1 in an intermediate group. Normalize separately within each group to total mass 120/B. Thus higher permission is associated with higher valuation in this workload; the current figure labels it as positive association.

Raw records split each group–valuation mass into identical copies. The number of copies is floor((960/B)/D), with one additional copy for the first (960/B) mod D valuations. Each copy receives an equal share of its group–valuation weight. All qualities, costs, valuations, and weights are represented as exact rational strings and decoded directly to `Fraction`, without a float conversion.

| T, including zero | 20 | 40 | 80 | 160 |
|---|---:|---:|---:|---:|
| Distinct positive valuations | 19 | 39 | 79 | 159 |
| Effective types in A | 38 | 78 | 158 | 318 |
| Effective types in B | 57 | 117 | 237 | 477 |

An effective type is a distinct (permission ceiling, valuation) pair. Its count is B(T − 1), despite the fixed raw-record count of 960. The `price_grid: ["0"]` field is retained for compatibility with the original input class; the PHT solver does not optimize over that field and instead constructs the exact finite threshold set.

To regenerate input files separately, use:

```bash
python experiments/threshold/protocol/build_scan_plan.py \
  --config experiments/threshold/protocol/t_scan_v1.json \
  --output-dir outputs/threshold_generated --emit-inputs
```

This command generates inputs and a plan only. Its `NOT_RUN` status describes a generated plan, not the completion status of the separately retained historical runs.

## Timing, memory, and resource controls

The published measurements were collected on Linux 6.18.44 with Python 3.12.14 on an Intel Xeon Platinum 8573C. Each worker was pinned to one CPU, numerical-library thread variables were set to one, and workers ran sequentially.

Figure 5 uses **whole worker-process elapsed time** (`process_wall_seconds`), measured immediately before process creation through the controller's observation of process exit. This includes Python startup, imports, input decoding, exact PHT optimization, its return and reconstruction work, external certificate checks, and certificate/result output. The recorded `solver_wall_seconds` field covers only the solver call and is not the plotted time. The controller polls every 0.01 seconds, so the whole-process measure includes exit-observation granularity.

Memory is the worker process's actual peak resident set size, obtained from Linux `os.wait4` / `ru_maxrss`. KiB values are multiplied by 1024 to obtain bytes and divided by 2^20 for the MiB shown in the figure. RSS is not the address-space limit. For each input, the plotted center is the median of three observations, with minimum and maximum retained for the range; no warm-up observation is removed.

| Control | Original value |
|---|---:|
| Worker process wall limit | 60 s |
| Worker virtual-address-space limit (`RLIMIT_AS`) | 4 GiB |
| Suite wall budget | 3,600 s |
| Simultaneous workers | 1 |
| CPUs per worker | 1 |
| Timeout termination grace | 2 s |

The original schedule orders T increasingly, then profile A/B, workload independent/correlated, and repetitions 1–3. A timeout or confirmed memory-limit failure closes the remaining repetitions and larger T in that profile/workload family. Input, reconstruction, or solver errors stop the suite; unstarted work remains explicitly unrun. The portable solver command preserves these policies and saves all requested slots. Its resource probe checks the address-space limit and affinity before calling the optimizer.

## Files and generated outputs

| Path | Content |
|---|---|
| `experiments/threshold/protocol/` | Original deterministic generator, configuration, and planned input/attempt records |
| `experiments/threshold/original/inputs/` | 16 original rational JSON inputs |
| `experiments/threshold/original/sources/` | Exact solver and shared input classes, unchanged |
| `experiments/threshold/original/code/` | Original controller, worker, and input-generator snapshot |
| `experiments/threshold/original/attempts/` | 48 certificates, jobs, controls, results, and logs |
| `experiments/threshold/original/results.jsonl` | All 48 published observations |
| `experiments/threshold/original/summary.json` | Recorded machine, Python version, source hashes, and completion summary |
| `experiments/threshold/expected_figure5_data.json` | Current manuscript's Figure 5 observations, extracted without changing their values |
| Verification output `regenerated_figure5_data.json` | Reconstructed `scan_cases` used to regenerate the figure |
| Verification output `case_summary.json` | Median/minimum/maximum, exact profit, and effective type counts per input |
| Verification output `replayed_certificates.json` | Exact replay status for each saved certificate |

The archived JSON records contain the original machine's absolute paths and historical identifiers. They are preserved as recorded provenance and are not used as input locations by the portable commands. The solver filename and some original comments retain the earlier working name SafeRefresh. This package maps their numerical outputs to the current manuscript and Figure 5.
