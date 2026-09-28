#!/usr/bin/env python3
"""Reproduce frozen application inputs and NOT_RUN plan; never calls a solver."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parent
THETA = [1, 2, 3, 4]
DELTA = [-6, -2, 2, 6]
SCENARIOS = [(-1, "negative"), (0, "independent"), (1, "positive")]
METHODS = ["FULL_PHT_EXACT", "GREEDY_ADD_ONE_EXACT", "ENDPOINTS_ONLY_EXACT"]
SOURCE_URL = "https://www.cmegroup.com/market-data/files/information-license-agreement-june-2026.pdf"


def payload(lam, label):
    buyers = []
    for trust, group in [(0, "low"), (1, "high")]:
        for theta, d in zip(THETA, DELTA):
            weight = 10 + lam * d if trust else 10 - lam * d
            buyers.append(dict(trust=trust, theta=theta, weight=weight,
                               name=f"{group}-theta{theta}"))
    return {
        "schema_version": "application-input-v1",
        "scenario_id": f"APP53-{label}",
        "K": 2,
        "instance": {
            "name": f"SafeRefresh application association {label}; separate from R26 running example",
            "qualities": [1, 2, 3], "fixed_costs": [1, 1, 1],
            "marginal_costs": [0, 1, 2], "safety_ceiling": [1, 2],
            "price_grid": [0], "buyers": buyers,
        },
        "metadata": {
            "frozen_spec": "5.3 section 6.7",
            "lambda": lam, "association": label,
            "main_accounting_case": lam == 0,
            "units": "synthetic model profit units per common planning period; not USD",
            "business_setting": "one CME exchange information set, designed display-subscription planning abstraction; no bundling across exchanges, non-display use or redistribution-right inference",
            "common_period": "one designed planning period; no mapping to real billing month",
            "product_labels": ["L", "M", "H"],
            "group_labels": ["low", "high"],
            "source_constrained_configuration": {
                "release_delay_seconds": [86400, 900, 0],
                "release_classes": ["historical", "delayed", "real_time"],
                "source_id": "S-CME-ILA-2026",
                "source_url": SOURCE_URL,
                "source_locator": "section 1 definitions, PDF pages 1-2",
                "source_constraints": {
                    "real_time_upper_seconds_inclusive": 600,
                    "delayed_lower_seconds_exclusive": 600,
                    "delayed_upper_seconds_exclusive": 28800,
                    "historical_first_access_lower_seconds_inclusive": 28800,
                    "historical_prior_real_time_or_delayed_access_allowed": False,
                },
                "constraint_role": "defines feasible release-class configuration and its documented order; it is not a numerical profit coefficient",
                "design_values": True,
                "historical_first_access_only": True,
                "first_provision_delay_seconds": [86400, 900, 0],
                "first_access_delay_seconds": [86400, 900, 0],
                "prior_access_same_observation": [False, False, False],
                "information_scope": "same abstract information set, separate first-provision/first-access contract alternatives; no downloaded trade data",
                "real_time_zero_intentional_delay_not_network_latency": True,
            },
            "synthetic_fields": ["qualities", "fixed_costs", "marginal_costs", "K", "theta", "weight"],
            "eligibility_status": "designed nested scenario: low may access L,M; high may access L,M,H; not a recovered customer entitlement matrix",
            "quality_status": "common cardinal coefficients are designed; not estimated from release seconds, prices, or fees",
            "demand_rule": {"theta": THETA, "d": DELTA,
                            "high": "10 + lambda*d", "low": "10 - lambda*d"},
            "tie_rule": "theta=0 exits; otherwise maximize utility incl outside, then price, then quality, then product index",
            "price_grid_role": "legacy Instance field required for schema only; continuous-price PHT and fixed-menu pricing do not enumerate this grid",
            "changes_from_spec_defaults": [],
        },
    }


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def main():
    (ROOT / "inputs").mkdir(exist_ok=True)
    records = []
    for lam, label in SCENARIOS:
        data = encoded(payload(lam, label))
        relative = f"inputs/APP53-{label}.json"
        destination = ROOT / relative
        if destination.exists() and destination.read_bytes() != data:
            raise SystemExit(f"Refusing to overwrite different frozen input: {destination}")
        destination.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        for method in METHODS:
            records.append({"run_id": f"APP53-{label}--{method}",
                            "scenario_id": f"APP53-{label}", "lambda": lam,
                            "method": method, "input_path": relative,
                            "input_sha256": digest, "budget_seconds": 300,
                            "budget_scope": "one entire scenario-method worker incl imports, all internal solves and replay",
                            "arithmetic": "fractions.Fraction; no tolerance", "status": "NOT_RUN"})
    plan = {"schema_version": "application-plan-v1", "total_groups": 9,
            "total_worker_budget_seconds": 2700, "repetitions": 1,
            "solver_execution_authorized_by_this_script": False, "runs": records}
    path = ROOT / "plan.json"
    data = encoded(plan)
    if path.exists() and path.read_bytes() != data:
        raise SystemExit("Refusing to overwrite a different plan; preserve execution records separately")
    path.write_bytes(data)
    print(json.dumps({"inputs": 3, "plan_rows": 9, "status": "NOT_RUN", "solver_calls": 0}))


if __name__ == "__main__":
    main()
