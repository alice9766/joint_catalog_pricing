# Direct MILP formulation and result interpretation

This note documents the independent MILP used in the frozen 18-input, three-method comparison. It describes the code and recorded outputs without changing the model, rerunning optimization, or changing any reported result. Paths below are relative to the public artifact root.

## Source map

| Component | Artifact path |
|---|---|
| Formulation and solver entry | `experiments/comparison/vendor/srmd_direct_milp.py` |
| Raw SciPy return capture | `experiments/comparison/code/benchmark_adapters.py` |
| Common price-menu replay and status rules | `experiments/comparison/code/benchmark_worker.py` |
| Frozen limits, inputs, tolerances, and hashes | `experiments/comparison/protocol/protocol_frozen.json` |
| Complete attempt ledger | `experiments/comparison/results/formal_v1/attempts.jsonl` |
| Environment | `experiments/comparison/results/formal_v1/environment.json` |
| Recomputed summaries | `experiments/comparison/validation/summary.json` |

The formulation source SHA-256 is `7cebcec59d83493ffcbb3d8f5e0211c1acbe715b5eee75cfc02d4a444ffbefbe`. The frozen protocol SHA-256 is `866047ea90ebee7fe2d8afdbac26e727132639e9bf45eff39f0c2be267521df1`. The protocol is the pre-run snapshot; execution status and outcomes are recorded in the results directory.

## Inputs, price bound, and variables

Let products be indexed by m=0,...,M−1. Input type i has weight w_i, valuation scale θ_i, and accessible prefix A_i. Write v_im=θ_i q_m and a_i=|A_i|. N counts explicit type records supplied to the solver, after any aggregation performed when the frozen input was created. Let E=Σ_i a_i.

The implementation sets

\[
U=\max\left(\{1\}\cup\{v_{im}:m\in A_i\}\right).
\]

U supplies finite bounds for linearization. It is derived from the instance, rather than a market-imposed price ceiling: an activated product that attracts demand has price at most an eligible buyer's value; zero-demand products can be removed under the at-most-capacity objective with nonnegative activation costs.

| Variable | Domain | Meaning |
|---|---|---|
| z_m | {0,1} | Product m is activated |
| p_m | [0,U] | One public price for product m |
| o_i | {0,1} | Type i selects the outside option |
| x_im, m∈A_i | {0,1} | Type i is assigned to product m |
| R_im, m∈A_i | [0,U] | Linearized payment p_m x_im |

In the code these arrays are named `z`, `p`, `outside`, `x`, and `revenue`. The same p_m occurs in every type's constraints. Inaccessible assignment variables are absent. The comparison calls `require_nondecreasing_prices=False`, so public prices need not be monotone in quality.

## Objective and constraints

The economic objective is

\[
\max\quad
\sum_i\sum_{m\in A_i}w_i(R_{im}-c_mx_{im})
-\sum_m h_mz_m.
\]

SciPy minimizes its negative: positive activation and delivery costs, and negative weighted revenue. Activation fees are charged for every active product, including an active product assigned no demand.

Catalog capacity, price activation, and unit choice are

\[
\sum_mz_m\le\min\{K,M\},\qquad
p_m\le Uz_m,
\]

\[
o_i+\sum_{m\in A_i}x_{im}=1,\qquad x_{im}\le z_m.
\]

The payment linearization is

\[
R_{im}\le p_m,\qquad R_{im}\le Ux_{im},\qquad
R_{im}\ge p_m-U(1-x_{im}),
\]

with R_im≥0 from its variable bound. Since x_im is binary, these rows set R_im=p_m when selected and R_im=0 otherwise.

For individual rationality, define

\[
I_{im}=\max\{0,U-v_{im}\}.
\]

The code adds

\[
p_m\le v_{im}+I_{im}(1-x_{im}).
\]

Thus a chosen product has utility at least the zero outside utility.

For m,ℓ∈A_i with m≠ℓ, define

\[
d_{im\ell}=v_{im}-v_{i\ell},\qquad
D_{im\ell}=\max\{0,U-d_{im\ell}\},\qquad
H_{i\ell}=\max\{0,v_{i\ell}\}.
\]

Weak incentive compatibility is enforced by

\[
p_m-p_\ell\le d_{im\ell}
+D_{im\ell}(1-x_{im})+H_{i\ell}(1-z_\ell).
\]

When x_im=z_ℓ=1, this is exactly v_im−p_m≥v_iℓ−p_ℓ. When ℓ is inactive, individual rationality bounds p_m by v_im; the H term disables the comparison to that inactive alternative. When m is unchosen, the D term makes the row redundant under the price bounds.

Finally, define O_im=max{0,v_im}. The outside-option rows are

\[
v_{im}-p_m\le O_{im}(2-o_i-z_m),\qquad m\in A_i.
\]

If type i exits and m is active, the offered product has nonpositive utility. Other combinations deactivate the row. All utility comparisons are weak; tie assignments are subsequently checked by canonical replay.

With the monotone-price option disabled, the formulation has

\[
2M+N+2E
\]

variables, of which M+N+E are binary, and

\[
1+M+N+6E+\sum_i a_i(a_i-1)
\]

linear rows. This count includes choice-to-activation links, three payment rows, one IR row, all ordered pairwise IC rows, and one outside row for each accessible pair. For the first balanced M=8 input, N=32 and E=160 give 368 variables and 1,801 rows, matching its existing solver diagnostics.

The formulation uses assignments and activation decisions directly. It does not call PHT, enumerate PHT states, or use the finite edge-slope theorem to restrict prices.

## Numerical settings and complete-method timing

The frozen comparison uses Python 3.12.14, NumPy 2.3.5, and SciPy 1.17.0; SciPy's `milp` calls HiGHS. The passed optimization options are `time_limit=25.0`, `mip_rel_gap=0.0`, and `presolve=True`. Other solver feasibility settings remain the defaults of that recorded environment.

Each attempt runs in a fresh process under a 30-second process wall limit and a 4-GiB virtual-address-space limit. One worker runs at a time, pinned to one permitted logical CPU, with the recorded BLAS/OpenMP thread environment set to one. These are the configured resource controls; the artifact does not independently count internal HiGHS threads.

`full_function_wall_seconds` starts immediately before the method call and ends after the common replay. For MILP it includes formulation construction, sparse-matrix construction, optimization, output recovery, and common replay. Imports and input decoding are outside this timer but inside the process wall limit. Post-replay comparisons and certificate serialization are outside the function timer. The legacy MILP-internal timer has a narrower boundary and is retained only as a diagnostic, not used in timing ratios.

All methods use the same input, capacity, public-price objective, activation costs, and delivery costs. The MILP's 25-second internal limit leaves time for recovery and output within the common 30-second process budget. PHT returns its capacity frontier as part of the same call; MILP is solved once for the specified K, rather than rerun at every capacity.

## Weak-IC objective, canonical replay, and bounds

The weak-IC assignment model permits any utility-maximizing tied assignment. The paper's canonical demand instead applies its specified tie rule. These are distinct outputs at a returned price vector, so the artifact stores both.

The common replay evaluates the recovered activated menu and prices for every explicit input type. Its tolerance is

\[
\varepsilon_u=10^{-9}\max\{1,U,\max_{m\in S}p_m\}.
\]

Alternatives within ε_u of the largest utility are treated as tied. A type with θ_i=0 exits. Other ties are ordered by price, quality, and then product index; the outside alternative has index −1 and price/quality zero. The replay recomputes revenue, subscription delivery costs, and every activated product's fixed fee.

The frozen MILP source also performs its older internal canonical replay at tolerance 10^{-6}U. The comparison worker retains that diagnostic and separately runs the common 10^{-9}-scale replay used for the other methods. It checks solver-objective versus common profit, solver-objective versus internal profit, internal versus common profit, and internal versus common assignments. Objective comparisons use

\[
|a-b|\le10^{-7}+10^{-9}\max\{|a|,|b|\}.
\]

| Recorded field | Interpretation |
|---|---|
| `weak_ic_incumbent_objective` | Negative raw SciPy minimization incumbent objective, when an incumbent exists |
| `common_replay_profit` | Profit induced by the recovered menu under the common numerical canonical rule |
| `raw_min_dual_bound` | Raw minimization bound returned by SciPy, or null if unavailable/nonfinite |
| `profit_upper_bound` | Negative finite `raw_min_dual_bound`; null if unavailable |
| `raw_mip_gap` | Reported MILP relative gap |
| `has_incumbent` | Whether raw SciPy output contains a solution vector |

The legacy field `objective_upper_semantics` is a weak-IC incumbent objective, not a global profit upper bound. The comparison reads its actual upper bound from the captured raw dual bound. This distinction applies especially to time-limited returns.

If there is no incumbent, incumbent profit, common replay, and the effective menu/prices remain null. The adapter does not reinterpret the vendor's empty return tuple as a feasible zero-profit incumbent. Missing optional SciPy fields receive sentinels only in a copied return object after optimization, allowing the unchanged vendor wrapper to serialize its result; raw missing diagnostics remain null. The formulation and solver settings are unchanged.

Canonical replay validates the returned menu under the recorded numerical rule. Global optimality is recorded separately through the solver status and gap. Exact-rational correctness belongs to the paper's theorem and its separate rational workflow.

## Status rules and the frozen outcome

| Comparison status | Meaning |
|---|---|
| `NUMERIC_COMPLETE` for MILP | Solver reports optimal with raw gap zero, an incumbent exists, and all recorded replay checks agree |
| `SOLVER_NONOPTIMAL` | An incumbent exists and replay checks agree, but the optimality condition above is unmet |
| `SOLVER_NONOPTIMAL_REPLAY_MISMATCH` | A nonoptimal incumbent exists and one or more replay checks disagree |
| `REPLAY_MISMATCH` | Optimal solver return whose replay checks disagree |
| `SOLVER_NONOPTIMAL_NO_INCUMBENT` | No returned incumbent |
| Controller timeout/resource status | The process exceeded an external limit or failed to return eligible output; controller status takes precedence |

Raw SciPy statuses are recorded independently: 0=optimal, 1=limit, 2=infeasible, 3=unbounded, and 4=solver error. A MILP internal limit is distinct from a process timeout.

Across the existing 54 MILP attempts, all returned incumbents: 18 were `NUMERIC_COMPLETE`, 15 were `SOLVER_NONOPTIMAL`, and 21 were `SOLVER_NONOPTIMAL_REPLAY_MISMATCH`. The latter 36 reached the 25-second internal limit. Six of the 18 input cells had three numerically complete optimal MILP returns. All 54 attempts, including nonoptimal and mismatch records, remain in the ledger with their available prices, bounds, gaps, and checks.

Per-input timing medians require three eligible repetitions. Secondary PHT–MILP optimal-time ratios additionally require the common objective agreement and zero-gap optimal status specified in the protocol. The primary PHT–Greedy comparison is independent of MILP completion. Internal-limit observations are not treated as completed optimal solve times.

For inspection without optimization, run `python scripts/verify_comparison.py` to recompute summaries and replay returned certificates. To solve again, use `scripts/solve_comparison.py` as described in [COMPARISON.md](COMPARISON.md). New runs are written to a new output directory and do not replace the original observations.
