# Application planning case: Table III

This case compares three access–valuation association scenarios using the same eight customer types, three candidate contracts and two-contract capacity. The inputs and the nine recorded results are preserved byte for byte. The release categories are motivated by public licensing documents; the quality coefficients, costs and demand are synthetic.

## Run

From the repository root, using Python 3.12 or later and the standard library:

```bash
# Replay all recorded menus and regenerate both parts of Table III.
python scripts/verify_application.py --output work/application-verify

# Execute the solver for one small original scenario.
python scripts/solve_application.py --smoke --output work/application-smoke

# Select any one scenario and method, or run all nine combinations.
python scripts/solve_application.py --scenario negative --method endpoints --output work/application-endpoints
python scripts/solve_application.py --scenario all --method all --output work/application-all

# Regenerate the original three JSON inputs exactly.
python experiments/application/generate_inputs.py --output work/application-inputs
```

Every command requires a new output directory. Solves run sequentially with a 300-second whole-method timeout per combination; `--timeout` changes this limit. Fresh solve outputs include environment details, individual worker results and a `summary.json`. Fresh timings are separate from the archived observations.

`--method` accepts `pht`, `greedy`, `endpoints` or `all`. `--scenario` accepts `negative`, `independent`, `positive` or `all`. `--smoke` selects `independent` and `pht`.

## Inputs and methods

The contract labels L, M and H correspond to indices 0, 1 and 2. Quality is `(1,2,3)`, activation cost `(1,1,1)`, service cost `(0,1,2)` and capacity `K=2`. Low-access customers can buy L or M; high-access customers can buy all three. Valuations are `theta=(1,2,3,4)`. For `d=(-6,-2,2,6)` and scenario parameter `lambda` equal to -1, 0 or 1, high-access type weights are `10+lambda*d` and low-access type weights are `10-lambda*d`. Each scenario therefore has 40 customers in each access group and 20 at each valuation. Monetary amounts are model units in one synthetic planning period.

PHT jointly chooses the catalog and prices. Greedy adds one contract at a time with exact fixed-catalog repricing, accepting only strict improvement. The endpoint method considers catalogs drawn from L and H and reprices each exactly. All methods use the archived rational PHT pricing engine. The legacy `price_grid=[0]` input field is required by the shared schema and is not used to discretize prices.

The archived source uses `trust` for access-group index, `safety_ceiling` for the group's highest eligible contract, and `profit_supremum` for the returned optimum field. These names are retained so the source and original inputs keep their identities. The paper uses access terminology and proves attainment under its choice rule.

## Generated results

`verify_application.py` imports no solver. It uses `fractions.Fraction`, independently applies the canonical choice rule to all 72 type–menu combinations and checks permissions, capacity, subscription counts, revenue, service costs and activation costs. It also checks input regeneration, recorded source hashes, successful completion, decision-trace consistency and the archived Table III cells.

The output directory contains:

| File | Contents |
|---|---|
| `summary.json` | Status, individual checks, exact replayed values and evidence identities |
| `method_results_9.csv` | Nine method–scenario results and group demand |
| `type_choices_72.csv` | Every replayed customer-type choice |
| `table3a.csv`, `table3b.csv` | Both parts of Table III |
| `table3_generated.tex` | Two tabular fragments requiring `booktabs` |
| `fixed_menu_diagnostic.json` | Existing-input comparison described below |

All replay checks are accounting and consistency checks. Global optimality is provided by the algorithm and its theory; replay is not an independent optimization oracle. The fresh solve command also compares its objective with the archived objective.

## Fixed-menu diagnostic

The verifier evaluates the independent-scenario menu `(M,H)` with prices `(6,9)` under the existing negative-association demand. Its profit is 222, compared with 234 for that scenario's recorded optimum `(6,6)`. The relative loss is exactly `2/39`, or approximately 5.1282%. This is a diagnostic calculated from existing inputs, not an additional experimental scenario. It is not included in the current main paper.

## Evidence and source mapping

`experiments/application/recorded_runs/` contains all nine original jobs, worker outputs, controller outcomes and the recorded environment. `evidence/original_code/` preserves the original runner, generator and static checker referenced by the environment hashes. These are provenance snapshots; use the commands above to execute the packaged experiment. The archived launch plan has pre-execution `NOT_RUN` labels and the environment has `BATCH_STARTED`; the final `status.json` and per-run controller outcomes document all nine completions. `evidence_manifest.json` protects the retained payloads, including the current Table III TeX snapshot.

Public-source metadata, dates, locators and historical PDF hashes are in `experiments/application/sources/source_manifest.json`. The source files were checked on 25 September 2026; this package retains references and concise descriptions rather than copies of the official PDFs.

| Source | Role in the case |
|---|---|
| [CME Information License Agreement, June 2026](https://www.cmegroup.com/market-data/files/information-license-agreement-june-2026.pdf), Section 1, pp. 1–2 | Release-category bounds: real-time within 600 seconds, delayed above 600 and below 28,800 seconds, historical first access at least 28,800 seconds with the specified prior-access condition |
| [CME Fee List, June 2026](https://www.cmegroup.com/market-data/files/june-2026-market-data-fee-list.pdf), display-device notes 11–12 | Motivation for nested coverage in the stated display-use setting |
| [Databento Plans and live data](https://databento.com/docs/portal/live-data), licensing sections | Motivation for access eligibility being established before menu planning |

The designed delays `(86400,900,0)` seconds meet the recorded category conditions. Zero denotes no intentional release delay. The two customer groups and their eligibility are designed scenarios, not observed customer entitlements. No public fee is used as a model cost, and no demand or cardinal quality coefficient is estimated from these sources.
