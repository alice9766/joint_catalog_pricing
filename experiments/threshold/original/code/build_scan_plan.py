#!/usr/bin/env python3
"""Generate and check declared inputs only. This module never imports a solver.

No runtime, memory, objective, or scale result is produced. NOT_RUN is retained.
"""
from __future__ import annotations

import argparse
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def generate(profile, workload, T, config):
    D = T - 1
    B = profile["B"]
    N = config["raw_record_count"]
    assert D >= 2 and N % B == 0 and N // B >= D
    assert profile["ceilings"][-1] == profile["M"] - 1
    assert len(set(profile["ceilings"])) == B
    lo, hi = F(config["theta_min"]), F(config["theta_max"])
    theta = [lo + (hi - lo) * F(r, D - 1) for r in range(D)]
    base, extra = divmod(N // B, D)
    copies = [base + int(r < extra) for r in range(D)]
    group_mass = F(config["total_weight"]) / B
    buyers = []
    for b in range(B):
        shape = []
        for r in range(D):
            x = F(r, D - 1)
            a = F(1)
            if workload == "correlated":
                if b == 0:
                    a = 2 - x
                elif b == B - 1:
                    a = 1 + x
            shape.append(a)
        normalizer = sum(shape, F(0))
        for r in range(D):
            weight = group_mass * shape[r] / normalizer / copies[r]
            for j in range(copies[r]):
                buyers.append({"trust": b, "theta": str(theta[r]),
                               "weight": str(weight), "name": f"g{b}-v{r}-copy{j}"})
    case_id = f"{profile['id']}-{workload}-T{T}"
    instance = {"name": case_id,
                "qualities": profile["qualities"],
                "fixed_costs": profile["fixed_costs"],
                "marginal_costs": profile["marginal_costs"],
                "safety_ceiling": profile["ceilings"],
                "price_grid": config["legacy_price_grid"], "buyers": buyers}
    assert len(buyers) == N
    positive = {F(b["theta"]) for b in buyers if F(b["theta"]) > 0}
    assert len(positive | {F(0)}) == T and len(positive) == D
    assert min(positive) == lo and max(positive) == hi
    assert sum((F(b["weight"]) for b in buyers), F(0)) == F(config["total_weight"])
    group_diagnostics = []
    for b in range(B):
        rows = [v for v in buyers if v["trust"] == b]
        assert len(rows) == N // B
        assert {F(v["theta"]) for v in rows} == positive
        assert sum((F(v["weight"]) for v in rows), F(0)) == group_mass
        assert profile["ceilings"][b] >= 0
        masses = {t: F(0) for t in theta}
        for row in rows:
            masses[F(row["theta"])] += F(row["weight"]) / group_mass
        mean = sum((t * w for t, w in masses.items()), F(0))
        variance = sum(((t - mean) ** 2 * w for t, w in masses.items()), F(0))
        cumulative, cdf_error = F(0), F(0)
        for t, mass in sorted(masses.items()):
            x = (t - lo) / (hi - lo)
            target_cdf = x
            if workload == "correlated":
                if b == 0:
                    target_cdf = (4 * x - x * x) / 3
                elif b == B - 1:
                    target_cdf = (2 * x + x * x) / 3
            cdf_error = max(cdf_error, abs(cumulative - target_cdf),
                            abs(cumulative + mass - target_cdf))
            cumulative += mass
        group_diagnostics.append({"group": b, "weight": str(group_mass),
                                  "theta_mean": str(mean), "theta_variance": str(variance),
                                  "minimum_support_probability": str(min(masses.values())),
                                  "cdf_sup_distance_to_declared_density": str(cdf_error)})
    numbers = [F(v) for key in ("qualities", "fixed_costs", "marginal_costs", "price_grid")
               for v in instance[key]]
    numbers += [F(v[key]) for v in buyers for key in ("theta", "weight")]
    theta_den_bits = max(x.denominator.bit_length() for x in positive)
    stats = {"case_id": case_id, "M": profile["M"], "P": profile["M"], "B": B,
             "K": profile["K"], "T": T, "distinct_positive_theta": D,
             "N_raw": N, "distinct_group_theta_pairs": B * D,
             "total_weight": str(F(config["total_weight"])),
             "theta_min": str(lo), "theta_max": str(hi),
             "max_numerator_bits": max(abs(x.numerator).bit_length() for x in numbers),
             "max_denominator_bits": max(x.denominator.bit_length() for x in numbers),
             "max_theta_denominator_bits": theta_den_bits,
             "group_distribution_diagnostics": group_diagnostics,
             "canonical_input_bytes": len(canonical_bytes(instance)),
             "input_sha256": hashlib.sha256(canonical_bytes(instance)).hexdigest(),
             "input_check": "PASS", "scan_status": "NOT_RUN"}
    return instance, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("t_scan_v1.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--emit-inputs", action="store_true",
                        help="Write rational JSON inputs; still does not run any solver.")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    assert config["scan_status"] == "NOT_RUN"
    rows, attempts = [], []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for T in config["T_values"]:
        for profile in config["profiles"]:
            for workload in config["workloads"]:
                instance, stats = generate(profile, workload, T, config)
                rows.append(stats)
                if args.emit_inputs:
                    folder = args.output_dir / "inputs"
                    folder.mkdir(exist_ok=True)
                    (folder / f"{stats['case_id']}.json").write_bytes(canonical_bytes(instance))
                for rep in range(1, config["repetitions"] + 1):
                    attempts.append({"attempt_id": f"{stats['case_id']}-r{rep}",
                                     "case_id": stats["case_id"], "repetition": rep,
                                     "input_sha256": stats["input_sha256"],
                                     "status": "NOT_RUN"})
    header = {"protocol_id": config["protocol_id"], "scan_status": "NOT_RUN",
              "solver_calls": 0, "benchmark_results_produced": 0}
    audit = {**header, "scope": "INPUT_GENERATION_ONLY", "input_configurations": len(rows),
             "planned_attempts": len(attempts), "checks": rows}
    plan = {**header, "planned_attempts": attempts}
    for name, value in (("input_plan_audit.json", audit), ("planned_attempts.json", plan)):
        (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({**header, "input_configurations": len(rows),
                      "planned_attempts": len(attempts), "input_checks": "PASS"}))


if __name__ == "__main__":
    main()
