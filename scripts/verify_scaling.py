#!/usr/bin/env python3
"""Regenerate joint-scaling inputs and replay all 42 saved certificates exactly.

This verifier uses Fraction arithmetic and does not import the optimizer.
"""
from __future__ import annotations
import argparse
import bisect
import csv
from collections import Counter, defaultdict
from fractions import Fraction as F
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "experiments/scaling"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_csv(path, rows):
    with Path(path).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def replay(inp, cert, capacity):
    """Independent demand, price recovery, edge contribution and path checks."""
    q, fixed, cost = [list(map(F, inp[key])) for key in
                      ("qualities", "fixed_costs", "marginal_costs")]
    menu = cert["menu"]
    require(len(menu) == len(cert["prices"]) == len(set(menu)) <= capacity,
            "menu size/capacity")
    prices = dict(zip(menu, map(F, cert["prices"])))
    require(all(0 <= m < len(q) and prices[m] >= 0 for m in menu), "invalid menu")
    buyers = [(inp["safety_ceiling"][b["trust"]], F(b["theta"]), F(b["weight"]))
              for b in inp["buyers"]]
    thresholds = {F(0)} | {t for _, t, _ in buyers if t > 0}
    ceilings = sorted({cap for cap, t, _ in buyers if cap >= 0 and t > 0})
    require(cert["threshold_count"] == len(thresholds), "threshold count")
    require(cert["active_ceilings"] == ceilings, "active ceilings")
    assignments, demand = [], {m: F(0) for m in menu}
    for cap, theta, weight in buyers:
        choice = -1
        if theta > 0:
            options = [(F(0), F(0), F(0), -1)]
            options.extend((theta*q[m]-prices[m], prices[m], q[m], m)
                           for m in menu if m <= cap)
            choice = max(options)[3]
        assignments.append(choice)
        if choice != -1:
            demand[choice] += weight
    profit = sum(((prices[m]-cost[m])*demand[m]-fixed[m] for m in menu), F(0))
    require(assignments == cert["assignment"], "assignment mismatch")
    require(profit == F(cert["verified_profit"]) == F(cert["profit_supremum"]),
            "canonical profit mismatch")
    require(F(cert["reconstruction_gap"]) == 0, "reconstruction gap")
    frontier = list(map(F, cert["capacity_frontier"]))
    exact = [float("-inf") if v == "-Infinity" else F(v)
             for v in cert["exact_size_profit"]]
    require(len(frontier) == len(exact) == capacity+1 and frontier[0] == exact[0] == 0,
            "capacity arrays")
    require(all(frontier[k] == max(exact[:k+1]) for k in range(capacity+1)),
            "frontier prefix maximum")
    require(frontier[-1] == profit, "frontier objective")
    edges = cert["edges"]
    require(len(edges) == len(menu) and {e["child"] for e in edges} == set(menu),
            "edge children")
    P = ceilings[-1]+1
    contribution = F(0)
    for e in edges:
        a, u, boundary = e["parent"], e["child"], e["boundary"]
        slope = F(e["threshold"])
        require(a == -1 or a in prices, "edge parent")
        require(a < u < boundary <= P and slope in thresholds, "edge range/slope")
        start = bisect.bisect_left(ceilings, u)
        end = (len(ceilings) if boundary == P else bisect.bisect_left(ceilings, boundary))-1
        require(start == e["start_group"] <= e["end_group"] == end, "edge lifetime")
        qa, ca, pa = (F(0), F(0), F(0)) if a == -1 else (q[a], cost[a], prices[a])
        require(prices[u] == pa+slope*(q[u]-qa), "edge price recovery")
        tail = sum((w for cap, t, w in buyers if t > 0 and t >= slope and
                    start <= bisect.bisect_left(ceilings, cap) <= end), F(0))
        local = (slope*(q[u]-qa)-(cost[u]-ca))*tail-fixed[u]
        require(F(e["contribution"]) == local, "edge contribution")
        contribution += local
    require(contribution == profit, "edge profit sum")
    require(len(cert["group_hull_paths"]) == len(cert["group_edge_thresholds"]) == len(ceilings),
            "group paths count")
    for group in range(len(ceilings)):
        active = [e for e in edges if e["start_group"] <= group <= e["end_group"]]
        by_parent = {e["parent"]: e for e in active}
        require(len(by_parent) == len(active), "multiple active children")
        path, slopes, parent = [], [], -1
        while parent in by_parent:
            edge = by_parent[parent]
            path.append(edge["child"])
            slopes.append(F(edge["threshold"]))
            parent = edge["child"]
        require(len(path) == len(active), "disconnected active path")
        require(path == cert["group_hull_paths"][group], "saved path")
        require(slopes == list(map(F, cert["group_edge_thresholds"][group])), "saved path slopes")
        require(slopes == sorted(slopes), "path slope ordering")
    return {"status": "PASS", "canonical_profit": str(profit),
            "N_raw": len(buyers), "N_eff": len({(cap, t) for cap, t, _ in buyers}),
            "selected_contracts": len(menu), "replayed_assignments": len(assignments)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="New directory; existing output is never overwritten.")
    args = parser.parse_args()
    out = args.output.resolve()
    require(not out.exists(), "output already exists")
    out.mkdir(parents=True)
    manifest = read(DATA/"source_manifest.json")
    for item in manifest["files"]:
        require(sha(DATA/item["path"]) == item["sha256"], "source changed: "+item["path"])
    protocol = read(DATA/"protocol/protocol_frozen.json")
    plan = read(DATA/"protocol/plan.json")["planned_attempts"]
    require(len(plan) == 42 and len({r["attempt_id"] for r in plan}) == 42, "planned denominator")
    spec = importlib.util.spec_from_file_location("scaling_generator", DATA/"code/input_generator.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    profiles = {p["id"]: p for p in protocol["declared_profiles"]}
    profiles["SMOKE-M04-B2-K2"] = {"id": "SMOKE-M04-B2-K2", "M": 4, "B": 2, "K": 2,
        "qualities": ["1", "8/3", "13/3", "6"], "ceilings": [1, 3],
        "fixed_costs": ["2", "5", "3", "7"], "marginal_costs": ["0", "1", "1/2", "2"]}
    inputs, generation = {}, []
    for entry in [*{p["case_id"]: p for p in plan}.values(), protocol["smoke_case"]]:
        profile_id = entry["case_id"].rsplit("-"+entry["workload"]+"-T", 1)[0]
        generated, _ = generator.generate(profiles[profile_id], entry["workload"], entry["T"], protocol)
        inp_path = DATA/entry["input_file"]
        require(generator.canonical_bytes(generated) == inp_path.read_bytes(),
                "regenerated input differs: "+entry["case_id"])
        require(sha(inp_path) == entry["input_sha256"], "planned input hash")
        inputs[entry["case_id"]] = generated
        generation.append({"case_id": entry["case_id"], "status": "BYTE_IDENTICAL",
                           "sha256": sha(inp_path)})
    rows, checks = [], []
    for entry in plan:
        folder = DATA/"recorded/attempts"/entry["attempt_id"]
        row, worker, command = (read(folder/name) for name in ("final.json", "worker_result.json", "command.json"))
        require(all(row[k] == entry[k] for k in entry if k != "status"), "plan/record mismatch")
        require(row["status"] == "EXACT_COMPLETE" and row["complete_return"] and
                row["process_launched"] and row["solver_started"], "incomplete recorded attempt")
        require(0 <= row["process_wall_seconds"] < 300 and command["deadline_seconds"] == 300,
                "completion deadline")
        require(worker["controls"]["rlimit_as"] == [4*1024**3]*2 and
                len(worker["controls"]["affinity"]) == 1, "CPU/memory controls")
        require(all(v == "1" for v in worker["controls"]["thread_environment"].values()), "thread controls")
        require(row["peak_rss_bytes"] == row["peak_rss_linux_kib"]*1024, "RSS units")
        require(sha(folder/"certificate.json") == row["certificate_sha256"] == worker["certificate_sha256"],
                "certificate hash")
        check = replay(inputs[row["case_id"]], read(folder/"certificate.json"), row["K"])
        require(F(check["canonical_profit"]) == F(row["objective"]), "recorded objective")
        checks.append({"attempt_id": entry["attempt_id"], **check})
        rows.append({k: row[k] for k in ("attempt_id", "case_id", "repetition", "cohort", "M", "B", "K", "T",
                     "status", "process_wall_seconds", "solver_wall_seconds", "external_validation_wall_seconds",
                     "cpu_total_seconds", "peak_rss_bytes", "state_count", "objective", "environment_id")}
                    | {"N_raw": check["N_raw"], "N_eff": check["N_eff"],
                       "peak_rss_mib": row["peak_rss_bytes"]/1024**2})
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    per_input = []
    for case_id, group in sorted(grouped.items()):
        require(sorted(r["repetition"] for r in group) == [1, 2, 3], "repeat count")
        require(len({r["objective"] for r in group}) == 1, "deterministic objective inconsistency")
        per_input.append({k: group[0][k] for k in ("case_id", "cohort", "M", "B", "K", "T", "N_raw", "N_eff")}
                         | {"runs": 3, "completed": 3, "time_min_s": min(r["process_wall_seconds"] for r in group),
                            "time_median_s": statistics.median(r["process_wall_seconds"] for r in group),
                            "time_max_s": max(r["process_wall_seconds"] for r in group),
                            "peak_rss_mib": max(r["peak_rss_mib"] for r in group), "objective": group[0]["objective"]})
    table = []
    for M in (8, 12, 24):
        for T in (20, 40):
            groups = [[r for r in rows if r["M"] == M and r["T"] == T and r["B"] == r["K"] == 4 and
                       "-"+workload+"-" in r["case_id"]] for workload in ("independent", "correlated")]
            require(all(len(g) == 3 for g in groups), "Table II row denominator")
            table.append({"M": M, "T": T, "independent_time_s": statistics.median(r["process_wall_seconds"] for r in groups[0]),
                          "correlated_time_s": statistics.median(r["process_wall_seconds"] for r in groups[1]),
                          "peak_rss_mib": max(r["peak_rss_mib"] for g in groups for r in g),
                          "N_raw": groups[0][0]["N_raw"], "N_eff": groups[0][0]["N_eff"]})
    reference = (DATA/"reference/table2_joint_scale.tex").read_text()
    reference_rows = re.findall(r"^\s*(\d+)\s*&\s*(\d+)\s*&\s*([\d.]+)\s*&\s*([\d.]+)\s*&\s*([\d.]+)\s*\\\\", reference, re.M)
    formatted = [(str(r["M"]), str(r["T"]), *(f'{r[k]:.3f}' for k in
                  ("independent_time_s", "correlated_time_s", "peak_rss_mib"))) for r in table]
    require(formatted == reference_rows, "Table II differs from frozen manuscript")
    replacement = iter(formatted)
    regenerated_tex = re.sub(r"^\s*\d+\s*&\s*\d+\s*&\s*[\d.]+\s*&\s*[\d.]+\s*&\s*[\d.]+\s*\\\\",
                             lambda _: " & ".join(next(replacement))+r" \\", reference, flags=re.M)
    (out/"table_ii.tex").write_text(regenerated_tex)
    write_csv(out/"table_ii.csv", table)
    write_csv(out/"attempts.csv", rows)
    write_csv(out/"per_input.csv", per_input)
    write(out/"certificate_replay.json", {"status": "PASS", "checks": checks})
    write(out/"input_regeneration.json", {"status": "PASS", "checks": generation})
    summary = {"status": "PASS", "formal_inputs": 14, "formal_runs": 42, "complete_runs": 42,
               "table_ii_inputs": 12, "table_ii_runs": 36, "supplemental_inputs": 2, "supplemental_runs": 6,
               "input_regeneration_including_smoke": "15/15 byte-identical", "certificate_replays": 42,
               "table_ii_rows_match": 6, "optimizer_calls": 0,
               "N_raw_all_inputs": 960, "main_N_eff_by_T": {"20": 76, "40": 156},
               "supplemental_N_eff": 234, "status_counts": dict(Counter(r["status"] for r in rows)),
               "environment_count": len({r["environment_id"] for r in rows}),
               "verification_scope": "Independent exact replay of supplied menus, recovered prices, edge contributions and capacity-array consistency; not a second independent global optimization proof.",
               "timing_scope": "End-to-end process wall time, including imports, input loading, solving, reconstruction, validation and certificate writing. RSS is actual peak resident memory, not the 4 GiB address-space limit."}
    write(out/"summary.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
