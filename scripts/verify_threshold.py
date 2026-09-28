#!/usr/bin/env python3
"""Verify all published threshold-scan records without calling an optimizer."""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction as F
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "experiments/threshold"
RUN = DATA / "original"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def decode_instance(raw, types):
    numeric = {k: tuple(map(F, raw[k])) for k in
               ("qualities", "fixed_costs", "marginal_costs", "price_grid")}
    return types.Instance(**numeric, safety_ceiling=tuple(raw["safety_ceiling"]),
        buyers=tuple(types.BuyerType(b["trust"], F(b["theta"]), F(b["weight"]), b["name"])
                     for b in raw["buyers"]), name=raw["name"])


def decode_certificate(raw):
    result = dict(raw)
    for key in ("profit_supremum", "verified_profit", "reconstruction_gap"):
        result[key] = F(result[key])
    for key in ("prices", "capacity_frontier", "exact_size_profit"):
        result[key] = [(-float("inf") if x == "-Infinity" else F(x)) for x in result[key]]
    result["edges"] = [SimpleNamespace(**{k: F(v) if k in ("threshold", "contribution") else v
                                          for k, v in edge.items()}) for edge in result["edges"]]
    result["group_edge_thresholds"] = [list(map(F, values)) for values in result["group_edge_thresholds"]]
    return SimpleNamespace(**result)


def metric(values):
    return {"observations": values, "n": len(values), "median": statistics.median(values),
            "minimum": min(values), "maximum": max(values)}


def verify(output):
    config = read(DATA / "protocol/t_scan_v1.json")
    meta = read(RUN / "summary.json")
    rows = [json.loads(line) for line in (RUN / "results.jsonl").read_text().splitlines() if line.strip()]
    planned = read(DATA / "protocol/generated/planned_attempts.json")["planned_attempts"]
    input_audit = {x["case_id"]: x for x in read(DATA / "protocol/generated/input_plan_audit.json")["checks"]}
    fingerprint = meta["fingerprint"]
    require(config == read(RUN / "protocol.json"), "Archived and generated protocols differ")
    require(len(rows) == len(planned) == 48, "Expected all 48 attempts")
    require(Counter(row["status"] for row in rows) == {"EXACT_COMPLETE": 48}, "Not all runs completed")
    require(meta["status"] == "FINISHED" and meta["status_counts"] == {"EXACT_COMPLETE": 48}, "Summary status")
    require(meta["environment"]["system"] == "Linux", "RSS unit convention requires Linux")
    require(fingerprint["limits"] == config["limits"], "Limits mismatch")
    snapshot = config["solver_snapshot"]
    require(fingerprint["source_hashes"] == {snapshot["file"]: snapshot["sha256"],
        snapshot["type_file"]: snapshot["type_file_sha256"]}, "Frozen solver hashes mismatch")
    require(read(RUN / "preflight.json")["status"] == "PASS", "Original preflight did not pass")
    require(digest(RUN / "protocol.json") == fingerprint["protocol_sha256"], "Protocol hash")
    require(digest(RUN / "code/t_scan_runner.py") == fingerprint["runner_sha256"], "Controller hash")
    require(digest(RUN / "code/t_scan_worker.py") == fingerprint["worker_sha256"], "Worker hash")
    require(digest(DATA / "protocol/build_scan_plan.py") == meta["generator_sha256"], "Generator hash")
    for name, expected in fingerprint["source_hashes"].items():
        require(digest(RUN / "sources" / name) == expected, "Solver snapshot hash: " + name)

    generator = load_module("threshold_input_generator", DATA / "protocol/build_scan_plan.py")
    types = load_module("threshold_input_types", RUN / "sources/exact_srmd.py")
    worker = load_module("threshold_certificate_validator", RUN / "code/t_scan_worker.py")
    instances, cases = {}, {}
    for T in config["T_values"]:
        for profile in config["profiles"]:
            for workload in config["workloads"]:
                raw, audit = generator.generate(profile, workload, T, config)
                case_id = audit["case_id"]
                path = RUN / "inputs" / (case_id + ".json")
                require(generator.canonical_bytes(raw) == path.read_bytes(), "Input regeneration: " + case_id)
                require(audit == input_audit[case_id], "Generated input metadata: " + case_id)
                instance = decode_instance(raw, types)
                instance.validate()
                effective = len({(instance.safety_ceiling[b.trust], b.theta) for b in instance.buyers})
                require(effective == profile["B"] * (T - 1), "Effective type count: " + case_id)
                instances[case_id] = instance
                cases[case_id] = {"case_id": case_id, "profile": profile["id"], "workload": workload,
                    "T": T, "M": profile["M"], "B": profile["B"], "K": profile["K"],
                    "N_raw": len(instance.buyers), "N_effective": effective, "total_weight": "120"}
    require(len(cases) == len(input_audit) == 16, "Expected 16 inputs")

    replay_rows = []
    for row, plan in zip(rows, planned, strict=True):
        ident, case_id = row["attempt_id"], row["case_id"]
        for key in ("attempt_id", "case_id", "repetition", "input_sha256"):
            require(row[key] == plan[key], "Attempt plan mismatch: " + ident)
        folder = RUN / "attempts" / ident
        certificate = read(folder / "certificate.json")
        require(digest(folder / "certificate.json") == row["certificate_sha256"], "Certificate hash: " + ident)
        require(digest(RUN / "inputs" / (case_id + ".json")) == row["input_sha256"], "Input hash: " + ident)
        controller, payload, job = (read(folder / name) for name in
                                    ("controller_result.json", "worker_result.json", "job.json"))
        require(all(row[k] == v for k, v in controller.items()), "Controller/ledger mismatch: " + ident)
        require(payload["status"] == "EXACT_COMPLETE" and row["return_code"] == 0, "Worker status: " + ident)
        case = cases[case_id]
        require(all(job[k] == case[k] for k in ("M", "K", "T")) and job["N"] == case["N_raw"],
                "Job dimensions: " + ident)
        require(job["input_sha256"] == row["input_sha256"] and job["source_hashes"] == fingerprint["source_hashes"],
                "Job input/source identities: " + ident)
        require(job["memory_limit_bytes"] == config["limits"]["per_worker_address_space_bytes"],
                "Job address-space limit: " + ident)
        require(payload["controls"]["affinity"] == [fingerprint["cpu"]], "Single CPU: " + ident)
        require(payload["controls"]["rlimit_as"] == [config["limits"]["per_worker_address_space_bytes"]] * 2,
                "Address-space limit: " + ident)
        require(all(v == "1" for v in payload["controls"]["thread_environment"].values()), "Thread limits: " + ident)
        require(row["peak_rss_bytes"] == row["peak_rss_linux_kib"] * 1024, "RSS unit mismatch: " + ident)
        require(0 < row["process_wall_seconds"] < config["limits"]["per_attempt_wall_seconds"], "Time limit: " + ident)
        require(0 < row["solver_wall_seconds"] < row["process_wall_seconds"], "Solver vs process time: " + ident)
        result = decode_certificate(certificate)
        validation = worker.validate_result(instances[case_id], result, job)
        require(validation["replay_profit"] == F(row["objective"]) == F(payload["objective"]), "Exact objective: " + ident)
        require(result.capacity_frontier == list(map(F, row["capacity_frontier"])) == list(map(F, payload["capacity_frontier"])),
                "Frontier agreement: " + ident)
        require(result.state_count == row["state_count"] == payload["state_count"], "State count agreement: " + ident)
        replay_rows.append({"attempt_id": ident, "case_id": case_id, "status": "PASS",
            "exact_replay_profit": str(validation["replay_profit"]),
            "canonical_assignment": "PASS", "capacity_frontier": "PASS", "edges_and_group_paths": "PASS"})

    scan_cases, aggregate = [], []
    for case_id in sorted(cases):
        selected = sorted((r for r in rows if r["case_id"] == case_id), key=lambda r: r["repetition"])
        require([r["repetition"] for r in selected] == [1, 2, 3], "Three repeats: " + case_id)
        require(len({r["objective"] for r in selected}) == 1, "Repetition objective mismatch: " + case_id)
        require(len({tuple(r["capacity_frontier"]) for r in selected}) == 1, "Repetition frontier mismatch: " + case_id)
        case = cases[case_id]
        time_values = [r["process_wall_seconds"] for r in selected]
        memory_values = [r["peak_rss_bytes"] / 2**20 for r in selected]
        scan_cases.append({k: case[k] for k in ("case_id", "profile", "workload", "T", "M", "B", "K")} |
                          {"process_wall_seconds": time_values, "peak_rss_mib": memory_values})
        aggregate.append(case | {"process_wall_seconds": metric(time_values),
                                  "peak_rss_mib": metric(memory_values), "exact_profit": selected[0]["objective"]})
    expected = read(DATA / "expected_figure5_data.json")
    require(scan_cases == expected["scan_cases"], "Regenerated Figure 5 records differ from current paper source")
    dump(output / "regenerated_figure5_data.json", {"scan_cases": scan_cases})
    dump(output / "replayed_certificates.json", replay_rows)
    dump(output / "case_summary.json", aggregate)
    summary = {"status": "PASS", "scope": "saved_results_and_exact_certificate_replay",
        "solver_calls": 0, "inputs_regenerated": 16, "certificates_replayed": 48,
        "exact_completed_runs": 48, "figure5_source_match": True,
        "statistics": "median, minimum and maximum of all three recorded process-time/RSS observations",
        "raw_records_per_input": 960, "effective_type_count": "B*(T-1)",
        "N_effective_by_profile": {p: sorted({v["N_effective"] for v in cases.values() if v["profile"] == p})
                                    for p in ("A", "B")},
        "historical_environment": meta["environment"], "limits": config["limits"],
        "interpretation": "Exact replay checks returned menus and certificates; this command does not independently rerun optimization or remeasure historical timings."}
    dump(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New output directory; existing directories are refused")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        summary = verify(args.output)
    except Exception as exc:
        dump(args.output / "summary.json", {"status": "FAIL", "error": str(exc)})
        raise
    print(json.dumps({k: summary[k] for k in ("status", "inputs_regenerated", "certificates_replayed", "figure5_source_match")}))


if __name__ == "__main__":
    main()
