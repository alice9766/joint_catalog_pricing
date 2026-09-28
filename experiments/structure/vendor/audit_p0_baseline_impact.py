#!/usr/bin/env python3
"""Recompute only fixed-active-menu baselines; never overwrite historical data.

Run after the P0 fixed-menu correction.  Uses new processes, immutable source
hashes, deterministic source identifiers, and append-only completion records.
The archived full-PHT objective is a comparison reference, not recomputed here.
Examples:
  python code/audit_p0_baseline_impact.py --limit-per-suite 12 --output-dir results/p0_correction_results/pilot
  python code/audit_p0_baseline_impact.py --workers 2
  python code/audit_p0_baseline_impact.py --workers 2 --resume
"""
from __future__ import annotations
import argparse
import collections
import concurrent.futures
import dataclasses
import hashlib
import itertools
import json
import math
import multiprocessing
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from datetime import datetime, timezone

# Prevent implicit BLAS pools inside the limited number of worker processes.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = {
    "structure": "results/PHT_STRUCTURE_VALUE_GATE_RESULTS.json",
    "s1": "results/s1_confirmatory_20260911/main.jsonl",
    "s1_config": "results/SAFE_REFRESH_SYNTHETIC_EVALUATION_S1_CONFIG.json",
}
STRUCTURE_FIELDS = ("family", "M", "B", "K", "T", "N", "aggregate", "seed")
S1_FIELDS = ("quality_shape", "demand_shape", "access_profile", "cost_profile", "seed_offset")
EXPECTED_COUNTS = {"structure": 750, "s1": 648}
REL_TOL = 1e-8


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def object_digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def source_key(suite, row):
    fields = STRUCTURE_FIELDS if suite == "structure" else S1_FIELDS
    return {field: row[field] for field in fields}


def source_id(suite, row):
    return suite + ":" + object_digest(source_key(suite, row))


def load_sources(root):
    from saferefresh_algorithm_structure_gate import structure_payloads
    structure = json.loads((root / SOURCE_FILES["structure"]).read_text())["rows"]
    s1 = [json.loads(line) for line in (root / SOURCE_FILES["s1"]).read_text().splitlines() if line.strip()]
    config = json.loads((root / SOURCE_FILES["s1_config"]).read_text())
    source = {"structure": structure, "s1": s1}
    for suite, rows in source.items():
        if len(rows) != EXPECTED_COUNTS[suite]:
            raise ValueError(f"{suite}: unexpected source count {len(rows)}")
        ids = [source_id(suite, row) for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{suite}: duplicate source identifiers")
        if any(row.get("status", "complete") != "complete" for row in rows):
            raise ValueError(f"{suite}: source contains incomplete rows")
    expected_structure = {source_id("structure", row) for row in structure_payloads()}
    if {source_id("structure", row) for row in structure} != expected_structure:
        raise ValueError("structure source does not match the complete frozen schedule")
    c = config["main"]
    expected_s1 = {
        source_id("s1", dict(zip(S1_FIELDS, values)))
        for values in itertools.product(c["quality_shapes"], c["demand_shapes"], c["access_profiles"], c["cost_profiles"], c["seed_offsets"])
    }
    if {source_id("s1", row) for row in s1} != expected_s1:
        raise ValueError("S1 source does not match the complete frozen factorial")
    return source, config


def worker(task):
    # Imports occur afresh in each spawn child; no old interactive-module cache.
    from pht_structure_baselines import solve_contiguous_products, solve_greedy_add_one
    from saferefresh_algorithm_structure_gate import make_benchmark_instance
    from run_saferefresh_s1 import build_instance_from_payload
    suite, index, source, config = task
    if suite == "structure":
        instance = make_benchmark_instance(**{name: source[name] for name in STRUCTURE_FIELDS if name != "K"})
    else:
        instance, metadata = build_instance_from_payload(source, config)
        expected_vectors = {
            "qualities": instance.qualities,
            "fixed_costs": instance.fixed_costs,
            "marginal_costs": instance.marginal_costs,
            "safety_ceilings": instance.safety_ceiling,
        }
        for field, values in expected_vectors.items():
            if list(values) != source[field]:
                raise ValueError(f"S1 input regeneration mismatch {field}: source index {index}")
        for field in ("seed", "theta_pool", "group_population", "effective_buyer_records", "raw_buyer_count"):
            if metadata[field] != source[field]:
                raise ValueError(f"S1 metadata regeneration mismatch {field}: source index {index}")
    if len(instance.buyers) != source["effective_buyer_records"]:
        raise ValueError("effective buyer count differs from source")
    full = float(source["profit"])
    denominator = max(1.0, abs(full))
    tol = REL_TOL * denominator
    result = {
        "suite": suite, "source_row_index": index,
        "source_id": source_id(suite, source), "source_key": source_key(suite, source),
        "source_row_sha256": object_digest(source),
        "generated_instance_sha256": object_digest(dataclasses.asdict(instance)),
        "archived_full_pht_profit": full, "relative_denominator": denominator,
        "change_tolerance_absolute": tol, "baselines": {}, "status": "complete",
    }
    for name, solver in (("contiguous", solve_contiguous_products), ("greedy", solve_greedy_add_one)):
        started = time.perf_counter()
        candidate = solver(instance, int(source["K"]))
        elapsed = time.perf_counter() - started
        old_field = "greedy_add_one" if suite == "s1" and name == "greedy" else name
        old = float(source["values"][old_field]); new = float(candidate.profit_supremum)
        if not math.isfinite(new):
            raise ValueError("baseline result is nonfinite")
        old_menu = source.get("baseline_menus", {}).get(old_field)
        menu = list(candidate.menu)
        if len(menu) > int(source["K"]) or len(set(menu)) != len(menu):
            raise ValueError("baseline menu violates capacity or uniqueness")
        if name == "contiguous" and menu and menu != list(range(menu[0], menu[-1] + 1)):
            raise ValueError("contiguous baseline returned a noncontiguous menu")
        result["baselines"][name] = {
            "old_profit": old, "corrected_profit": new, "profit_change": new-old,
            "material_change": abs(new-old) > tol,
            "old_normalized_loss": (full-old)/denominator,
            "corrected_normalized_loss": (full-new)/denominator,
            "corrected_menu": menu, "old_menu": old_menu,
            "menu_changed": None if old_menu is None else old_menu != menu,
            "wall_seconds": elapsed,
            "above_archived_full_optimum": new > full + tol,
            "contiguous_below_old": name == "contiguous" and new < old - tol,
        }
    return result


def quantile(values, probability):
    values = sorted(values)
    pos = (len(values) - 1) * probability
    low = math.floor(pos); high = math.ceil(pos)
    return values[low] * (high-pos) + values[high] * (pos-low) if high != low else values[low]


def distribution(values):
    return {"count": len(values), "mean": statistics.mean(values), "median": statistics.median(values), "p95": quantile(values, .95), "maximum": max(values), "minimum": min(values)}


def summarize(records, planned, all_source_counts, elapsed, output):
    records = sorted(records, key=lambda row: (row["suite"], row["source_row_index"]))
    if len({row["source_id"] for row in records}) != len(records):
        raise ValueError("duplicate completed result IDs")
    expected_ids = {source_id(task[0], task[2]) for task in planned}
    actual_ids = {row["source_id"] for row in records}
    if not actual_ids.issubset(expected_ids):
        raise ValueError("result IDs not in the selected source schedule")
    violations = []
    by_suite = {}
    for suite in EXPECTED_COUNTS:
        rows = [r for r in records if r["suite"] == suite]
        expected_count = sum(t[0] == suite for t in planned)
        entry = {"source_count": all_source_counts[suite], "selected_count": expected_count, "completed_count": len(rows), "baselines": {}}
        for name in ("contiguous", "greedy"):
            if not rows: continue
            b = [r["baselines"][name] for r in rows]
            entry["baselines"][name] = {
                "materially_changed_count": sum(x["material_change"] for x in b),
                "improved_count": sum(x["profit_change"] > r["change_tolerance_absolute"] for r,x in zip(rows,b)),
                "worsened_count": sum(x["profit_change"] < -r["change_tolerance_absolute"] for r,x in zip(rows,b)),
                "known_menu_changed_count": sum(x["menu_changed"] is True for x in b),
                "old_loss": distribution([x["old_normalized_loss"] for x in b]),
                "corrected_loss": distribution([x["corrected_normalized_loss"] for x in b]),
                "profit_change": distribution([x["profit_change"] for x in b]),
                "wall_seconds": distribution([x["wall_seconds"] for x in b]),
                "old_strict_loss_count": sum(x["old_profit"] < r["archived_full_pht_profit"]-r["change_tolerance_absolute"] for r,x in zip(rows,b)),
                "corrected_strict_loss_count": sum(x["corrected_profit"] < r["archived_full_pht_profit"]-r["change_tolerance_absolute"] for r,x in zip(rows,b)),
            }
            for r,x in zip(rows,b):
                if x["above_archived_full_optimum"] or x["contiguous_below_old"]:
                    violations.append({"source_id":r["source_id"],"baseline":name,"detail":x})
        by_suite[suite] = entry
    complete = actual_ids == expected_ids
    full = complete and all(by_suite[s]["completed_count"] == EXPECTED_COUNTS[s] for s in EXPECTED_COUNTS)
    report = {
        "schema": "saferefresh-p0-baseline-impact-summary-v1",
        "status": "FAIL" if violations else ("COMPLETE_FULL" if full else "COMPLETE_SELECTED" if complete else "PARTIAL"),
        "scope": "Only corrected contiguous and greedy baselines; archived main-PHT objectives are references, not recomputed.",
        "relative_tolerance": REL_TOL, "all_source_counts": all_source_counts,
        "selected_count": len(planned), "completed_count": len(records),
        "selected_source_ids_sha256": object_digest(sorted(expected_ids)),
        "completed_source_ids_sha256": object_digest(sorted(actual_ids)),
        "current_invocation_wall_seconds": elapsed, "by_suite": by_suite,
        "violations": violations,
        "interpretation": "No detected numerical changes would not invalidate the semantic counterexample; the general implementation still required correction.",
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    changes = [r for r in records if any(x["material_change"] for x in r["baselines"].values())]
    (output / "material_changes.jsonl").write_text("".join(canonical_json(r)+"\n" for r in changes))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results/p0_correction_results")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit-per-suite", type=int, default=0, help="0=full; positive=evenly spaced deterministic sample per suite")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 4 or args.limit_per_suite < 0:
        parser.error("workers must be 1..4 and limit nonnegative")
    output = args.output_dir.resolve()
    if output == ROOT / "results" or any(str(output) == str((ROOT / p).parent) for p in SOURCE_FILES.values()):
        raise ValueError("refusing to write into a historical source directory")
    sources, config = load_sources(ROOT)
    source_hashes = {name: digest(ROOT / path) for name, path in SOURCE_FILES.items()}
    # Hash all non-test Python code to identify every dependency, including the
    # new shared fixed-menu module, without guessing its implementation name.
    code_hashes = {p.name: digest(p) for p in sorted((ROOT/"code").glob("*.py")) if not p.name.startswith("test_")}
    planned = []
    for suite, rows in sources.items():
        n = len(rows); limit = min(args.limit_per_suite or n, n)
        selected = list(range(n)) if limit == n else ([0] if limit == 1 else sorted({round(i*(n-1)/(limit-1)) for i in range(limit)}))
        planned.extend((suite, index, rows[index], config) for index in selected)
    invariants = {"source_hashes":source_hashes,"code_hashes":code_hashes,"selected_source_ids_sha256":object_digest(sorted(source_id(t[0],t[2]) for t in planned)),"relative_tolerance":REL_TOL}
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / "metadata.json"; raw_path = output / "baseline_recompute.jsonl"
    if metadata_path.exists() or raw_path.exists():
        if not args.resume:
            raise ValueError("output exists; choose a new directory or use --resume")
        if not metadata_path.exists():
            raise ValueError("cannot resume without metadata")
        metadata = json.loads(metadata_path.read_text())
        if metadata["invariants"] != invariants:
            raise ValueError("resume source, code, schedule, or tolerance changed")
    else:
        metadata = {"schema":"saferefresh-p0-baseline-impact-run-v1","created_at":datetime.now(timezone.utc).isoformat(),"invariants":invariants,"source_paths":SOURCE_FILES,"expected_source_counts":EXPECTED_COUNTS,"source_schedules_verified":True,"workers":args.workers,"limit_per_suite":args.limit_per_suite,"environment":{"python":sys.version,"platform":platform.platform(),"cpu_count":os.cpu_count()},"never_rerun":["MILP","full PHT experiment suites","D2 measurement","S1 sensitivity","S2 decomposition"]}
        metadata_path.write_text(json.dumps(metadata,indent=2,ensure_ascii=False)+"\n")
    records = []
    if raw_path.exists():
        for line in raw_path.read_text().splitlines():
            if line.strip(): records.append(json.loads(line))
    completed_ids = {r["source_id"] for r in records}
    if len(completed_ids) != len(records): raise ValueError("duplicate checkpoint IDs")
    pending = [task for task in planned if source_id(task[0],task[2]) not in completed_ids]
    started = time.perf_counter()
    with raw_path.open("a") as handle:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as executor:
            futures = {executor.submit(worker, task): task for task in pending}
            for future in concurrent.futures.as_completed(futures):
                record = future.result()
                handle.write(canonical_json(record)+"\n"); handle.flush()
                records.append(record)
                if len(records)%50 == 0 or len(records)==len(planned):
                    report = summarize(records,planned,{k:len(v) for k,v in sources.items()},time.perf_counter()-started,output)
                    print(json.dumps({"completed":len(records),"selected":len(planned),"seconds":round(time.perf_counter()-started,2),"status":report["status"]}),flush=True)
    # The source files are immutable throughout a correction run.
    if any(digest(ROOT/SOURCE_FILES[name]) != sha for name,sha in source_hashes.items()):
        raise ValueError("historical source file changed during run")
    if any(digest(ROOT/"code"/name) != sha for name,sha in code_hashes.items()):
        raise ValueError("code changed during run")
    report = summarize(records,planned,{k:len(v) for k,v in sources.items()},time.perf_counter()-started,output)
    print(json.dumps({"status":report["status"],"completed":len(records),"output":str(output)},ensure_ascii=False),flush=True)
    return 1 if report["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
