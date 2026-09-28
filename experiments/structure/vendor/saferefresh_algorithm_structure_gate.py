#!/usr/bin/env python3
"""Reproducible runner for the SafeRefresh algorithm/structure Gate."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import itertools
import json
import math
import os
import platform
import random
import resource
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from continuous_srmd import solve_menu_continuous
from exact_srmd import BuyerType, Instance
from persistent_hull_tree_dp import random_nested_instance, solve_persistent_hull_tree
from pht_structure_baselines import (
    solve_contiguous_products,
    solve_greedy_add_one,
    solve_restricted_pht,
)
from srmd_direct_milp import solve_direct_milp


ROOT = Path(__file__).resolve().parent
SEED_BASE = 2027091000


def _json_number(value: object) -> object:
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_number) + "\n")


def _balanced_ceilings(M: int, B: int) -> Tuple[int, ...]:
    return tuple(((group + 1) * M) // B - 1 for group in range(B))


def make_benchmark_instance(
    *,
    seed: int,
    family: str,
    M: int,
    B: int,
    T: int,
    N: int,
    aggregate: bool,
) -> Instance:
    if family not in {"balanced", "access_skewed", "correlated"}:
        raise ValueError(f"unknown family {family}")
    if not 1 <= B <= M or N < B or T < 1:
        raise ValueError("require 1 <= B <= M, N >= B, and T >= 1")
    rng = random.Random(seed + {"balanced": 0, "access_skewed": 10**7, "correlated": 2 * 10**7}[family])

    qualities: List[float] = []
    current = 0
    for _ in range(M):
        current += rng.randint(1, 4)
        qualities.append(float(current))

    if family == "access_skewed" and B < M:
        ceilings = tuple(range(B - 1)) + (M - 1,)
    else:
        ceilings = _balanced_ceilings(M, B)

    theta_pool = tuple(float(1 + 2 * j) / 2.0 for j in range(T))
    raw: List[Tuple[int, float, float]] = []
    for group in range(B):
        theta = theta_pool[min(T - 1, (group * T) // B)]
        raw.append((group, theta, 1.0))
    for _ in range(N - B):
        if family == "access_skewed":
            group = rng.choices(range(B), weights=[B - g for g in range(B)], k=1)[0]
            theta = rng.choice(theta_pool)
        elif family == "correlated":
            group = rng.randrange(B)
            center = 0 if B == 1 else round(group * (T - 1) / (B - 1))
            theta = theta_pool[max(0, min(T - 1, center + rng.randint(-2, 2)))]
        else:
            group = rng.randrange(B)
            theta = rng.choice(theta_pool)
        raw.append((group, theta, float(rng.randint(1, 3))))

    buyers: List[BuyerType] = []
    if aggregate:
        weights: Dict[Tuple[int, float], float] = collections.defaultdict(float)
        for group, theta, weight in raw:
            weights[group, theta] += weight
        for index, ((group, theta), weight) in enumerate(sorted(weights.items())):
            buyers.append(BuyerType(group, theta, weight, f"agg-{index}"))
    else:
        buyers = [
            BuyerType(group, theta, weight, f"raw-{index}")
            for index, (group, theta, weight) in enumerate(raw)
        ]

    if family == "correlated":
        fixed = tuple(float(rng.randint(0, 3) + m // 5) for m in range(M))
        marginal = tuple(float(m) / 3.0 + rng.randint(0, 2) / 2.0 for m in range(M))
    else:
        fixed = tuple(float(rng.choice((0, 1, 2, 4, 6))) for _ in range(M))
        marginal = tuple(float(rng.choice((0, 0.5, 1, 2, 3))) for _ in range(M))

    return Instance(
        qualities=tuple(qualities),
        fixed_costs=fixed,
        marginal_costs=marginal,
        safety_ceiling=ceilings,
        buyers=tuple(buyers),
        price_grid=(0.0,),
        name=f"gate-{family}-m{M}-b{B}-t{T}-n{N}-s{seed}",
    )


def continuous_capacity_frontier(instance: Instance) -> Tuple[float, ...]:
    exact = [0.0] + [-math.inf] * instance.M
    for size in range(1, instance.M + 1):
        for menu in itertools.combinations(range(instance.M), size):
            value = solve_menu_continuous(instance, menu).profit_supremum
            exact[size] = max(exact[size], value)
    frontier = [0.0]
    for size in range(1, instance.M + 1):
        frontier.append(max(frontier[-1], exact[size]))
    return tuple(frontier)


def qualification_case(M: int, B: int, N: int, seed: int) -> Dict[str, object]:
    instance = random_nested_instance(seed, B=B, M=M, N=N, fractional=False)
    exact = solve_persistent_hull_tree(instance, M, exact=True)
    floating = solve_persistent_hull_tree(instance, M, exact=False)
    oracle = continuous_capacity_frontier(instance)
    milp_rows = []
    maximum_gap = 0.0
    replay_gap = 0.0
    statuses = []
    for K in range(1, M + 1):
        direct = solve_direct_milp(instance, K, time_limit=20.0)
        statuses.append(direct.status)
        expected = float(exact.capacity_frontier[K])
        maximum_gap = max(
            maximum_gap,
            abs(float(floating.capacity_frontier[K]) - expected),
            abs(oracle[K] - expected),
            abs(direct.objective_upper_semantics - expected),
        )
        replay_gap = max(replay_gap, abs(direct.canonical_replay_profit - expected))
        milp_rows.append(
            {
                "K": K,
                "status": direct.status,
                "objective": direct.objective_upper_semantics,
                "canonical_replay": direct.canonical_replay_profit,
                "gap": direct.objective_upper_semantics - expected,
                "mip_gap": direct.mip_gap,
                "wall_seconds": direct.wall_seconds,
            }
        )
    scale = max(1.0, max(abs(float(v)) for v in exact.capacity_frontier))
    tolerance = 2e-6 * scale
    return {
        "M": M,
        "B": B,
        "N": N,
        "seed": seed,
        "exact_frontier": [float(v) for v in exact.capacity_frontier],
        "float_frontier": [float(v) for v in floating.capacity_frontier],
        "oracle_frontier": list(oracle),
        "milp": milp_rows,
        "maximum_absolute_gap": maximum_gap,
        "maximum_canonical_replay_gap": replay_gap,
        "tolerance": tolerance,
        "pass": all(status == "optimal" for status in statuses) and maximum_gap <= tolerance and replay_gap <= tolerance,
    }


def run_qualification(output: Path, workers: int, limit: int | None = None) -> Dict[str, object]:
    cases = [
        {
            "mode": "qualification",
            "M": M,
            "B": B,
            "N": N,
            "seed": SEED_BASE + seed_offset,
        }
        for M in (3, 4, 5)
        for N in (3, 4, 5)
        for B in range(1, min(M, N) + 1)
        for seed_offset in range(20)
    ]
    if limit is not None:
        cases = cases[:limit]
    rows: List[Dict[str, object]] = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(isolated_worker, case, 180.0) for case in cases]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.append(future.result())
            if index % 25 == 0 or index == len(cases):
                print(f"qualification {index}/{len(cases)}", flush=True)
                _write_json(output, {"status": "running", "completed": index, "rows": rows})
    rows.sort(key=lambda row: (row["M"], row["N"], row["B"], row["seed"]))
    failed = [row for row in rows if not row.get("pass", False)]
    result = {
        "status": "pass" if not failed else "fail",
        "protocol": "SAFE_REFRESH_ALGORITHM_AND_STRUCTURE_GATE_PROTOCOL_CN.md",
        "case_count": len(rows),
        "frontier_points": sum(int(row["M"]) for row in rows),
        "wall_seconds": time.perf_counter() - started,
        "max_absolute_gap": max((float(row.get("maximum_absolute_gap", math.inf)) for row in rows), default=0.0),
        "max_canonical_replay_gap": max((float(row.get("maximum_canonical_replay_gap", math.inf)) for row in rows), default=0.0),
        "failure_count": len(failed),
        "failures": failed,
        "rows": rows,
    }
    _write_json(output, result)
    return result


def topology(result: object) -> Dict[str, int]:
    edges = result.edges
    degree: Dict[int, int] = collections.Counter(edge.parent for edge in edges)
    children = {edge.child for edge in edges}
    parents = {edge.parent for edge in edges if edge.parent >= 0}
    return {
        "branch_nodes": sum(value > 1 for value in degree.values()),
        "max_children": max(degree.values(), default=0),
        "leaves": len(children - parents),
    }


def worker(payload: Dict[str, object]) -> Dict[str, object]:
    if str(payload["mode"]) == "qualification":
        started = time.perf_counter()
        answer = qualification_case(
            int(payload["M"]),
            int(payload["B"]),
            int(payload["N"]),
            int(payload["seed"]),
        )
        answer.update(
            {
                "mode": "qualification",
                "wall_seconds": time.perf_counter() - started,
                "peak_rss_raw": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "platform": platform.platform(),
            }
        )
        return answer
    instance = make_benchmark_instance(
        seed=int(payload["seed"]),
        family=str(payload["family"]),
        M=int(payload["M"]),
        B=int(payload["B"]),
        T=int(payload["T"]),
        N=int(payload["N"]),
        aggregate=bool(payload.get("aggregate", True)),
    )
    K = int(payload["K"])
    mode = str(payload["mode"])
    started = time.perf_counter()
    if mode == "scale":
        full = solve_persistent_hull_tree(instance, K)
        answer: Dict[str, object] = {
            "profit": float(full.profit_supremum),
            "state_count": full.state_count,
            "threshold_count": full.threshold_count,
            "menu_size": len(full.menu),
            "reconstruction_gap": float(full.reconstruction_gap),
            "topology": topology(full),
        }
        accessible_pairs = sum(
            max(0, instance.safety_ceiling[buyer.trust] + 1)
            for buyer in instance.buyers
        )
        pairwise_bound = accessible_pairs * max(1, instance.M)
        if int(payload.get("seed_offset", 0)) < 3 and accessible_pairs <= 5000 and pairwise_bound <= 100000:
            direct = solve_direct_milp(instance, K, time_limit=15.0)
            answer["milp"] = asdict(direct)
        else:
            answer["milp"] = {
                "status": "protocol_subsample" if int(payload.get("seed_offset", 0)) >= 3 else "size_guard",
                "accessible_pairs": accessible_pairs,
                "pairwise_constraint_bound": pairwise_bound,
            }
    elif mode == "structure":
        full = solve_persistent_hull_tree(instance, K)
        path = solve_restricted_pht(instance, K, "path")
        one = solve_restricted_pht(instance, K, "one_branch")
        contiguous = solve_contiguous_products(instance, K)
        greedy = solve_greedy_add_one(instance, K)
        run_monotone = bool(payload.get("run_monotone", False))
        monotone = (
            solve_direct_milp(
                instance,
                K,
                time_limit=20.0,
                require_nondecreasing_prices=True,
            )
            if run_monotone
            else None
        )
        optimum = float(full.profit_supremum)
        values = {
            "full": optimum,
            "path": path.profit_supremum,
            "one_branch": one.profit_supremum,
            "contiguous": contiguous.profit_supremum,
            "greedy": greedy.profit_supremum,
        }
        if monotone is not None and math.isfinite(monotone.objective_upper_semantics):
            values["monotone_price"] = monotone.objective_upper_semantics
        denominator = max(1.0, abs(optimum))
        answer = {
            "profit": optimum,
            "menu_size": len(full.menu),
            "state_count": full.state_count,
            "topology": topology(full),
            "values": values,
            "losses": {name: (optimum - value) / denominator for name, value in values.items() if name != "full"},
            "monotone_milp_status": monotone.status if monotone is not None else "protocol_subsample",
            "monotone_milp": asdict(monotone) if monotone is not None else None,
        }
    else:
        raise ValueError(f"unknown worker mode {mode}")
    answer.update(
        {
            "mode": mode,
            "family": payload["family"],
            "M": payload["M"],
            "B": payload["B"],
            "K": payload["K"],
            "T": payload["T"],
            "N": payload["N"],
            "aggregate": payload.get("aggregate", True),
            "effective_buyer_records": len(instance.buyers),
            "seed": payload["seed"],
            "seed_offset": payload.get("seed_offset"),
            "axis": payload.get("axis"),
            "cohort": payload.get("cohort"),
            "wall_seconds": time.perf_counter() - started,
            "peak_rss_raw": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "platform": platform.platform(),
        }
    )
    return answer


def isolated_worker(payload: Dict[str, object], timeout: float) -> Dict[str, object]:
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(payload)]
    started = time.perf_counter()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {**payload, "status": "timeout", "wall_seconds": time.perf_counter() - started}
    if completed.returncode != 0:
        return {
            **payload,
            "status": "crash",
            "wall_seconds": time.perf_counter() - started,
            "stderr": completed.stderr[-4000:],
        }
    try:
        row = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            **payload,
            "status": "invalid_json",
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    row["status"] = "complete"
    return row


def scale_payloads() -> List[Dict[str, object]]:
    axes: List[Tuple[str, List[Tuple[int, int, int, int, int]]]] = [
        ("M", [(M, min(4, M), min(4, M), 8, 128) for M in (4, 6, 8, 10, 12, 16, 20)]),
        ("T", [(10, 4, 4, T, 128) for T in (4, 6, 8, 10, 12, 16, 20)]),
        ("B", [(12, B, 4, 8, 128) for B in (1, 2, 3, 4, 6, 8, 10)]),
        ("K", [(12, 4, K, 8, 128) for K in (1, 2, 3, 4, 5, 6, 8)]),
        ("N", [(10, 4, 4, 8, N) for N in (32, 128, 512, 2048, 8192, 32768)]),
        ("joint", [(12, 4, 4, 10, 512), (16, 6, 5, 12, 2048), (20, 8, 6, 16, 8192), (24, 10, 8, 20, 32768)]),
    ]
    payloads = []
    for axis, points in axes:
        for family in ("balanced", "access_skewed", "correlated"):
            for M, B, K, T, N in points:
                for offset in range(20):
                    payloads.append(
                        {
                            "mode": "scale",
                            "axis": axis,
                            "family": family,
                            "M": M,
                            "B": B,
                            "K": K,
                            "T": T,
                            "N": N,
                            "aggregate": axis != "N",
                            "seed_offset": offset,
                            "seed": SEED_BASE + offset,
                        }
                    )
    return payloads


def structure_payloads() -> List[Dict[str, object]]:
    payloads = []
    for family in ("balanced", "access_skewed", "correlated"):
        for offset in range(200):
            payloads.append(
                {
                    "mode": "structure",
                    "cohort": "main",
                    "family": family,
                    "M": 8,
                    "B": 4,
                    "K": 4,
                    "T": 8,
                    "N": 128,
                    "aggregate": True,
                    "seed_offset": offset,
                    "run_monotone": offset < 20,
                    "seed": SEED_BASE + offset,
                }
            )
        for offset in range(50):
            payloads.append(
                {
                    "mode": "structure",
                    "cohort": "stress",
                    "family": family,
                    "M": 12,
                    "B": 6,
                    "K": 6,
                    "T": 10,
                    "N": 512,
                    "aggregate": True,
                    "seed_offset": offset,
                    "run_monotone": offset < 10,
                    "seed": SEED_BASE + offset,
                }
            )
    return payloads


def run_isolated_suite(
    payloads: Sequence[Dict[str, object]],
    output: Path,
    workers: int,
    timeout: float,
) -> Dict[str, object]:
    rows: List[Dict[str, object]] = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(isolated_worker, payload, timeout) for payload in payloads]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.append(future.result())
            if index % 25 == 0 or index == len(payloads):
                print(f"suite {index}/{len(payloads)}", flush=True)
                _write_json(output, {"status": "running", "completed": index, "rows": rows})
    result = {
        "status": "complete",
        "case_count": len(rows),
        "wall_seconds": time.perf_counter() - started,
        "rows": rows,
    }
    _write_json(output, result)
    return result


def run_scale_suite(
    payloads: Sequence[Dict[str, object]],
    output: Path,
    workers: int,
) -> Dict[str, object]:
    grouped: Dict[Tuple[object, ...], List[Dict[str, object]]] = collections.defaultdict(list)
    for payload in payloads:
        key = tuple(
            payload.get(field)
            for field in ("axis", "family", "M", "B", "K", "T", "N", "aggregate")
        )
        grouped[key].append(payload)
    groups = [sorted(group, key=lambda row: int(row["seed_offset"])) for group in grouped.values()]

    def run_group(group: List[Dict[str, object]]) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for index, payload in enumerate(group):
            row = isolated_worker(payload, 180.0)
            rows.append(row)
            if index == 9:
                timeouts = sum(item.get("status") == "timeout" for item in rows)
                if timeouts >= 8:
                    for remaining in group[index + 1 :]:
                        rows.append(
                            {
                                **remaining,
                                "status": "pre_registered_early_stop",
                                "reason": "at least 8 of first 10 seeds timed out",
                            }
                        )
                    break
        return rows

    rows: List[Dict[str, object]] = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_group, group) for group in groups]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.extend(future.result())
            print(f"scale groups {index}/{len(groups)}; rows {len(rows)}/{len(payloads)}", flush=True)
            _write_json(output, {"status": "running", "completed_groups": index, "rows": rows})
    result = {
        "status": "complete",
        "group_count": len(groups),
        "case_count": len(rows),
        "wall_seconds": time.perf_counter() - started,
        "rows": rows,
    }
    _write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualification", action="store_true")
    parser.add_argument("--scale", action="store_true")
    parser.add_argument("--structure", action="store_true")
    parser.add_argument("--worker")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--output")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if args.worker:
        print(json.dumps(worker(json.loads(args.worker)), ensure_ascii=False, default=_json_number))
        return
    if args.qualification:
        output = Path(args.output or ROOT / "PHT_TINY_QUALIFICATION_RESULTS.json")
        result = run_qualification(output, args.workers, args.limit)
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))
        return
    if args.scale:
        payloads = scale_payloads()
        if args.limit is not None:
            payloads = payloads[: args.limit]
        output = Path(args.output or ROOT / "PHT_SCALABILITY_GATE_RESULTS.json")
        result = run_scale_suite(payloads, output, args.workers)
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))
        return
    if args.structure:
        payloads = structure_payloads()
        if args.limit is not None:
            payloads = payloads[: args.limit]
        output = Path(args.output or ROOT / "PHT_STRUCTURE_VALUE_GATE_RESULTS.json")
        result = run_isolated_suite(payloads, output, args.workers, timeout=180.0)
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))
        return
    parser.error("choose --qualification, --scale, or --structure")


if __name__ == "__main__":
    main()
