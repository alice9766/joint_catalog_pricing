#!/usr/bin/env python3
"""Static checks only. Does not import or call any optimization routine."""
from pathlib import Path
import ast
import hashlib
import json
from collections import defaultdict
from fractions import Fraction
from generate_inputs import payload, encoded, SCENARIOS, METHODS

ROOT = Path(__file__).resolve().parent


def main():
    plan = json.loads((ROOT / "plan.json").read_text())
    assert len(plan["runs"]) == 9
    assert len({r["run_id"] for r in plan["runs"]}) == 9
    summaries = []
    for lam, label in SCENARIOS:
        path = ROOT / "inputs" / f"APP53-{label}.json"
        raw = path.read_bytes()
        assert raw == encoded(payload(lam, label)), "generator/manifest divergence"
        obj = json.loads(raw)
        ins = obj["instance"]
        q, h, c = [list(map(Fraction, ins[k])) for k in ("qualities", "fixed_costs", "marginal_costs")]
        assert q == [1, 2, 3] and h == [1, 1, 1] and c == [0, 1, 2]
        assert q[0] > 0 and all(a < b for a, b in zip(q, q[1:]))
        assert all(x >= 0 for x in h+c)
        assert ins["safety_ceiling"] == [1, 2] and obj["K"] == 2
        assert ins["price_grid"] == [0]
        group, theta = defaultdict(int), defaultdict(int)
        assert len(ins["buyers"]) == 8
        for b in ins["buyers"]:
            assert b["trust"] in [0, 1] and b["theta"] in [1, 2, 3, 4] and b["weight"] > 0
            group[b["trust"]] += b["weight"]
            theta[b["theta"]] += b["weight"]
        assert dict(group) == {0: 40, 1: 40}
        assert dict(theta) == {1: 20, 2: 20, 3: 20, 4: 20}
        cfg = obj["metadata"]["source_constrained_configuration"]
        hist, delayed, real = cfg["release_delay_seconds"]
        bounds = cfg["source_constraints"]
        assert bounds == {"real_time_upper_seconds_inclusive": 600,
                          "delayed_lower_seconds_exclusive": 600,
                          "delayed_upper_seconds_exclusive": 28800,
                          "historical_first_access_lower_seconds_inclusive": 28800,
                          "historical_prior_real_time_or_delayed_access_allowed": False}
        assert hist >= bounds["historical_first_access_lower_seconds_inclusive"]
        assert bounds["delayed_lower_seconds_exclusive"] < delayed < bounds["delayed_upper_seconds_exclusive"]
        assert 0 <= real <= bounds["real_time_upper_seconds_inclusive"]
        assert cfg["first_provision_delay_seconds"] == cfg["first_access_delay_seconds"] == cfg["release_delay_seconds"]
        assert cfg["prior_access_same_observation"] == [False, False, False]
        assert hist > delayed > real
        assert cfg["release_classes"] == ["historical", "delayed", "real_time"]
        sources = json.loads((ROOT / "sources/source_manifest.json").read_text())["sources"]
        source = next(s for s in sources if s["source_id"] == cfg["source_id"])
        assert source["url"] == cfg["source_url"]
        assert cfg["historical_first_access_only"]
        assert cfg["real_time_zero_intentional_delay_not_network_latency"]
        assert obj["metadata"]["changes_from_spec_defaults"] == []
        rows = [r for r in plan["runs"] if r["scenario_id"] == obj["scenario_id"]]
        assert [r["method"] for r in rows] == METHODS
        for r in rows:
            assert r["input_sha256"] == hashlib.sha256(raw).hexdigest()
            assert r["budget_seconds"] == 300 and r["status"] == "NOT_RUN"
        summaries.append({"scenario_id": obj["scenario_id"], "lambda": lam,
                          "types": 8, "weight": 80, "group_weights": [40, 40],
                          "theta_weights": [20, 20, 20, 20],
                          "input_sha256": hashlib.sha256(raw).hexdigest()})
    # Parse syntax without importing vendor modules or runner execution paths.
    pyfiles = sorted(ROOT.glob("*.py")) + sorted((ROOT / "vendor").glob("*.py"))
    for path in pyfiles:
        ast.parse(path.read_text(), filename=str(path))
    provenance = json.loads((ROOT / "vendor_provenance.json").read_text())
    for item in provenance["files"]:
        digest = hashlib.sha256((ROOT / item["snapshot_path"]).read_bytes()).hexdigest()
        assert digest == item["sha256"]
    manifest = ROOT / "application_manifest.json"
    if manifest.exists():
        for item in json.loads(manifest.read_text())["files"]:
            # This script writes its own deterministic report below.
            if item["path"] == "static_validation.json":
                continue
            path = ROOT / item["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item["path"]
    result = {"status": "STATIC_CHECKS_PASS", "solver_calls": 0,
              "solver_runtime_correctness_tested": False,
              "scenarios": summaries, "plan_groups": 9,
              "checked_python_syntax_files": len(pyfiles),
              "source_constraints_checked": "configured release delays satisfy encoded categories; source authenticity handled in source_mapping.md",
              "runtime_gate": "first explicitly started application batch must pass exact replay; static validation is not a solver smoke or result"}
    (ROOT / "static_validation.json").write_bytes(encoded(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
