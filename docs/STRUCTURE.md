# Catalog structure and capacity experiments

These files reproduce the 750-instance Structure suite and the 648-instance S1 suite used in Figure 6 and the corresponding Section V results. All instances are synthetic. `experiments/structure/instances.jsonl` contains the complete generated quality, cost, permission and weighted customer vectors, with a stable ID and source row index for every instance.

Run the following commands from the repository root after installing its requirements:

```bash
python scripts/verify_structure.py --output reproduced/structure_verify
python scripts/solve_structure.py --smoke --output reproduced/structure_smoke
python scripts/solve_structure.py --suite s1 --indices 0 --output reproduced/s1_smoke
```

The verification command regenerates all 1,398 inputs from their original rules and seeds, checks their hashes, joins the corrected baseline results to the original PHT results, and recomputes the loss distributions and capacity frontiers. It checks every Figure 6 observation against the data used in the current paper. It does not run an optimizer. The output contains `summary.json`, `regenerated_figure6_data.json`, `regenerated_instances.jsonl` and per-input checks. The figure script uses the same loss and frontier observations.

The solve command makes fresh calls to the original solvers. `--smoke` selects Structure source row 0; `--suite s1 --indices 0` also checks an S1 capacity frontier. Every resulting objective is compared with its stored reference. PHT, Greedy and Contiguous menus are independently replayed under the paper's customer choice rule. Path and One branch return objective values only and are compared with their stored objective values. For other selections or the complete suites:

```bash
python scripts/solve_structure.py --suite structure --indices 0,1 --output reproduced/structure_two
python scripts/solve_structure.py --suite s1 --indices 0,1 --output reproduced/s1_two
python scripts/solve_structure.py --suite all --all --output reproduced/all_structure
```

The last command runs all 1,398 instances and all applicable methods, including the S1 frontiers. It can take substantially longer than verification. Each completed instance is written immediately to `results.jsonl`. All commands require a new output directory and preserve supplied inputs and results. Indices are zero-based positions within each suite's original result file, not seed offsets. An optional `--methods full,greedy` limits the solve methods; the default is `full,path,one_branch,contiguous,greedy,capacity`, with `capacity` applied only to S1.

## Structure suite

Each of three families uses both configurations below. The 750 instances are the complete specified schedules; no instance is selected by its outcome.

| Candidates M | Permission groups B | Catalog limit K | Positive valuation levels | Raw records | Seeds per family |
|---:|---:|---:|---:|---:|---:|
| 8 | 4 | 4 | 8 | 128 | 200 |
| 12 | 6 | 6 | 10 | 512 | 50 |

The base seed is `2027091000 + offset`, with offsets 0–199 or 0–49 respectively. The Python `random.Random` seed additionally includes 0 for `balanced`, 10,000,000 for `access_skewed`, or 20,000,000 for `correlated`.

Qualities are cumulative independent integer increments drawn uniformly from {1,2,3,4}. The positive valuation pool is 0.5, 1.5, ..., T−0.5. Here T is the legacy generator parameter counting positive values; the paper's threshold count includes zero as well.

Balanced ceilings are `floor((g+1)M/B)−1`, for group g=0,...,B−1. The `access_skewed` family instead uses ceilings 0,1,...,B−2,M−1. One initial record per group uses valuation index `floor(gT/B)` and weight 1. The remaining N−B records are drawn as follows:

- `balanced`: uniform group and uniform valuation index.
- `access_skewed`: group probabilities proportional to B−g and a uniform valuation index.
- `correlated`: uniform group; valuation index is the clipped value of `round(g(T−1)/(B−1)) + U`, with integer U drawn uniformly from −2,...,2. Python's `round` convention is used.

Each remaining record has an independent integer weight drawn uniformly from {1,2,3}. Equal group/valuation pairs are aggregated by adding their weights. After these draws, costs are generated. In the correlated family, opening cost for product m is `randint(0,3)+floor(m/5)` and unit service cost is `m/3+randint(0,2)/2`. In the other families, opening costs are uniform choices from {0,1,2,4,6} and service costs from {0,0.5,1,2,3}. The original `make_benchmark_instance` implementation is included without modifications, fixing the precise random draw order.

## S1 factorial suite

The full design is 3 quality shapes × 4 valuation distributions × 3 access profiles × 3 cost profiles × 6 seeds = 648 instances. Every instance has M=10, B=5, K=4, 12 positive valuation levels and 240 raw customer records. `config.json` contains the machine-readable factor levels.

For product m=0,...,9, the three quality vectors are `sqrt((m+1)/M)` (diminishing), `(m+1)/M` (linear), and `((m+1)/M)^2` (accelerating). They are normalized to a maximum of 1 and rounded to 12 decimal places.

Let `p_j=(j+0.5)/12` and let Φ⁻¹ be the standard normal quantile. The positive valuation pools are:

| Distribution | Pool construction |
|---|---|
| Uniform | `0.25 + 3.75*p_j` |
| Heavy tail | `clip(exp(−0.25+0.85*Φ⁻¹(p_j)), 0.15, 6)` |
| Bimodal | Six low values `max(0.15,0.65+0.14*Φ⁻¹((j+0.5)/6))` and six high values `max(0.8,2.65+0.32*Φ⁻¹((j+0.5)/6))`, j=0,...,5 |
| Near tie | `1.5 + (j−5.5)*0.003` |

Pools are sorted and rounded to nine decimal places. Access ceilings and relative group sampling weights are:

| Access profile | Ceilings | Relative group weights |
|---|---|---|
| Balanced | 1,3,5,7,9 | 1,1,1,1,1 |
| Restrictive | 0,1,2,3,9 | 5,4,3,2,1 |
| Permissive | 5,6,7,8,9 | 1,2,3,4,5 |

Before perturbation, opening and service costs for product m are:

| Cost profile | Opening cost | Unit service cost |
|---|---|---|
| Activation heavy | `4+1.2*(m+1)^1.25` | `0.02+0.008*m` |
| Balanced | `1.5+0.5*(m+1)` | `0.05+0.035*m` |
| Service heavy | `0.2+0.1*m` | `0.10+0.075*m` |

Each cost is independently multiplied by a draw from Uniform[0.95,1.05] and rounded to nine decimal places, drawing opening then service cost for each product. Customer draws follow the cost draws. One record per group uses valuation index `floor(12*g/5)`. The remaining 235 records draw a group with the listed weights and choose a valuation uniformly from the 12-value pool. Each raw record has unit weight; identical group/valuation pairs are aggregated.

The random seed is `2026092100 + seed_offset + quality_offset + demand_offset + access_offset + cost_offset`. Seed offsets are 0,...,5. Factor offsets are:

| Factor | Values and offsets |
|---|---|
| Quality | diminishing: 0; linear: 1,000,000; accelerating: 2,000,000 |
| Demand | uniform: 0; heavy_tail: 10,000; bimodal: 20,000; near_tie: 30,000 |
| Access | balanced: 0; restrictive: 100; permissive: 200 |
| Cost | activation_heavy: 0; balanced: 10; service_heavy: 20 |

`run_saferefresh_s1.py` provides the original generation functions. `verify_structure.py` passes the packaged configuration explicitly, so there is no dependency on the original working directory. The retained original configuration also records separate historical qualification and sensitivity designs; only its `main` design generates the S1 results included here.

## Methods, statistics and source files

PHT optimizes the common catalog and prices. Path prohibits branching. One branch allows at most one branching node, including the outside-option root, and each node has at most two children. Contiguous searches all candidate intervals up to K. Greedy repeatedly adds the product giving the largest positive improvement after repricing the entire selected catalog, and stops when no addition improves profit. Both catalog-search baselines use `fixed_menu_pricing.py`, which charges the opening cost of every activated product, including zero-demand products.

The archived records do not contain complete price certificates for every method on all 1,398 instances. The full-data verification checks input generation, record joins and numerical summaries; it is not an independent replay of all archived menus. Menu replay applies to fresh solver outputs selected by the solve command.

The recorded Structure and S1 results use the original floating-point mode. The PHT implementation also supports rational arithmetic, but it is not substituted during these reproductions. The float PHT reconstruction and the independent wrapper replay use a utility tie tolerance of 1e−7, accept zero-utility subscriptions for positive valuation types, and select the highest-price/highest-quality tied contract. The historical `exact_srmd.evaluate` routine uses a different outside-option convention; the reproduction wrappers do not use that routine to evaluate canonical menu profit.

Baseline loss is `(V_K−baseline_profit)/max(1,abs(V_K))`. Capacity gap is `(V_M−V_k)/max(1,abs(V_M))`. Values within 1e−8 of zero are classified as zero; all other observations are retained. Quantiles use NumPy's linear interpolation over all instances, including zero losses. Figure 6 shows losses in percent; JSON summary values are fractions. The capacity values are the at-most-k frontier, not the legacy `exact_size_profit` field.

`evidence/structure_results.json` and `evidence/s1_results.jsonl` preserve the original PHT and structural-baseline results. `evidence/corrected_baselines.jsonl` supplies the corrected Greedy and Contiguous results, joined by stable source IDs and original record hashes. These corrected values are the published references. No obsolete Greedy/Contiguous value is used in the summaries. `reference/figure6_data.json` contains the current plotted observations. `provenance.json` records byte-for-byte hashes of the original inputs, results and code. Original vendor modules are unchanged; the documented `scripts/` commands supply repository-relative paths and explicit configuration.

The bundled `validation/current` output verifies all 1,398 inputs and all Figure 6 values. `validation/smoke` independently re-solves source row 0 in each suite, including all four baselines and the S1 capacity frontier. These checks reproduce stored objectives and do not replace the manuscript's timing experiments or imply that all 1,398 optimizations were rerun during packaging.
