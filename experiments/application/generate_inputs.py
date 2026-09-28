#!/usr/bin/env python3
"""Generate the three application inputs, byte-identical to the recorded inputs."""
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
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True, help="new directory for the three JSON inputs")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for lam, label in SCENARIOS:
        (args.output / f"APP53-{label}.json").write_bytes(encoded(payload(lam, label)))
    print(json.dumps({"inputs": 3, "output": str(args.output)}))


if __name__ == "__main__":
    main()
