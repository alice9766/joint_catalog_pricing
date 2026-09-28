#!/usr/bin/env python3
"""Run the frozen SafeRefresh Synthetic Evaluation Protocol S1."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import itertools
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from pathlib import Path
from statistics import NormalDist
from typing import Dict, Iterable, List, Sequence, Tuple

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from exact_srmd import BuyerType, Instance, evaluate
from persistent_hull_tree_dp import solve_persistent_hull_tree
from pht_structure_baselines import (
    solve_contiguous_products,
    solve_greedy_add_one,
    solve_restricted_pht,
)
from saferefresh_algorithm_structure_gate import qualification_case, topology


ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / "SAFE_REFRESH_SYNTHETIC_EVALUATION_S1_PROTOCOL_FROZEN_CN.md"
CONFIG = ROOT / "SAFE_REFRESH_SYNTHETIC_EVALUATION_S1_CONFIG.json"
DESIGN_HASH = ROOT / "SAFE_REFRESH_SYNTHETIC_EVALUATION_S1_DESIGN_SHA256.txt"
FACTOR_OFFSETS = {
    "quality_shape": {"diminishing": 0, "linear": 1_000_000, "accelerating": 2_000_000},
    "demand_shape": {"uniform": 0, "heavy_tail": 10_000, "bimodal": 20_000, "near_tie": 30_000},
    "access_profile": {"balanced": 0, "restrictive": 100, "permissive": 200},
    "cost_profile": {"activation_heavy": 0, "balanced": 10, "service_heavy": 20},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config() -> Dict[str, object]:
    return json.loads(CONFIG.read_text())


def verify_frozen_design() -> Dict[str, str]:
    expected: Dict[str, str] = {}
    for line in DESIGN_HASH.read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        expected[name.strip()] = digest
    actual = {PROTOCOL.name: sha256(PROTOCOL), CONFIG.name: sha256(CONFIG)}
    if expected != actual:
        raise RuntimeError(f"frozen S1 design hash mismatch: expected={expected}, actual={actual}")
    return actual


def stable_seed(base: int, factors: Dict[str, str], seed_offset: int) -> int:
    value = base + seed_offset
    for factor, choice in factors.items():
        value += FACTOR_OFFSETS[factor][choice]
    return value


def quality_vector(shape: str, M: int) -> Tuple[float, ...]:
    if shape == "diminishing":
        values = [math.sqrt((m + 1) / M) for m in range(M)]
    elif shape == "linear":
        values = [(m + 1) / M for m in range(M)]
    elif shape == "accelerating":
        values = [((m + 1) / M) ** 2 for m in range(M)]
    else:
        raise ValueError(f"unknown quality shape {shape}")
    maximum = values[-1]
    return tuple(round(value / maximum, 12) for value in values)


def theta_pool(shape: str, T: int) -> Tuple[float, ...]:
    probabilities = [(j + 0.5) / T for j in range(T)]
    normal = NormalDist()
    if shape == "uniform":
        values = [0.25 + probability * 3.75 for probability in probabilities]
    elif shape == "heavy_tail":
        values = [
            min(6.0, max(0.15, math.exp(-0.25 + 0.85 * normal.inv_cdf(probability))))
            for probability in probabilities
        ]
    elif shape == "bimodal":
        half = T // 2
        low = [
            max(0.15, 0.65 + 0.14 * normal.inv_cdf((j + 0.5) / half))
            for j in range(half)
        ]
        high_count = T - half
        high = [
            max(0.8, 2.65 + 0.32 * normal.inv_cdf((j + 0.5) / high_count))
            for j in range(high_count)
        ]
        values = low + high
    elif shape == "near_tie":
        values = [1.5 + (j - (T - 1) / 2) * 0.003 for j in range(T)]
    else:
        raise ValueError(f"unknown demand shape {shape}")
    rounded = tuple(round(value, 9) for value in sorted(values))
    if any(rounded[j] >= rounded[j + 1] for j in range(T - 1)):
        raise AssertionError("theta pool must be strictly increasing")
    return rounded


def access_spec(profile: str, M: int, B: int) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    if profile == "balanced":
        ceilings = tuple(((group + 1) * M) // B - 1 for group in range(B))
        group_weights = tuple(1 for _ in range(B))
    elif profile == "restrictive":
        ceilings = tuple(list(range(B - 1)) + [M - 1])
        group_weights = tuple(B - group for group in range(B))
    elif profile == "permissive":
        ceilings = tuple(M - B + group for group in range(B))
        group_weights = tuple(group + 1 for group in range(B))
    else:
        raise ValueError(f"unknown access profile {profile}")
    return ceilings, group_weights


def cost_vectors(profile: str, M: int, rng: random.Random) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    fixed: List[float] = []
    marginal: List[float] = []
    for m in range(M):
        if profile == "activation_heavy":
            h = 4.0 + 1.2 * ((m + 1) ** 1.25)
            c = 0.02 + 0.008 * m
        elif profile == "balanced":
            h = 1.5 + 0.5 * (m + 1)
            c = 0.05 + 0.035 * m
        elif profile == "service_heavy":
            h = 0.2 + 0.1 * m
            c = 0.10 + 0.075 * m
        else:
            raise ValueError(f"unknown cost profile {profile}")
        fixed.append(round(h * rng.uniform(0.95, 1.05), 9))
        marginal.append(round(c * rng.uniform(0.95, 1.05), 9))
    return tuple(fixed), tuple(marginal)


def make_s1_instance(
    *,
    quality_shape: str,
    demand_shape: str,
    access_profile: str,
    cost_profile: str,
    M: int,
    B: int,
    T: int,
    N: int,
    seed_base: int,
    seed_offset: int,
) -> Tuple[Instance, Dict[str, object]]:
    factors = {
        "quality_shape": quality_shape,
        "demand_shape": demand_shape,
        "access_profile": access_profile,
        "cost_profile": cost_profile,
    }
    seed = stable_seed(seed_base, factors, seed_offset)
    rng = random.Random(seed)
    qualities = quality_vector(quality_shape, M)
    types = theta_pool(demand_shape, T)
    ceilings, group_weights = access_spec(access_profile, M, B)
    fixed, marginal = cost_vectors(cost_profile, M, rng)

    raw: List[Tuple[int, float]] = []
    for group in range(B):
        raw.append((group, types[(group * T) // B]))
    for _ in range(N - B):
        group = rng.choices(range(B), weights=group_weights, k=1)[0]
        raw.append((group, rng.choice(types)))
    counts: Dict[Tuple[int, float], int] = {}
    for key in raw:
        counts[key] = counts.get(key, 0) + 1
    buyers = tuple(
        BuyerType(group, theta, float(weight), f"g{group}-t{index}")
        for index, ((group, theta), weight) in enumerate(sorted(counts.items()))
    )
    instance = Instance(
        qualities=qualities,
        fixed_costs=fixed,
        marginal_costs=marginal,
        safety_ceiling=ceilings,
        buyers=buyers,
        price_grid=(0.0,),
        name=(
            f"s1-{quality_shape}-{demand_shape}-{access_profile}-{cost_profile}"
            f"-s{seed_offset}"
        ),
    )
    instance.validate()
    metadata = {
        **factors,
        "seed": seed,
        "seed_offset": seed_offset,
        "raw_buyer_count": N,
        "effective_buyer_records": len(buyers),
        "theta_pool": list(types),
        "group_population": [sum(weight for (group, _), weight in counts.items() if group == b) for b in range(B)],
    }
    return instance, metadata


def perturb_instance(instance: Instance, epsilon: float, seed: int) -> Instance:
    rng = random.Random(seed)
    increments = [instance.qualities[0]] + [
        instance.qualities[m] - instance.qualities[m - 1] for m in range(1, instance.M)
    ]
    perturbed_increments = [increment * rng.uniform(1.0 - epsilon, 1.0 + epsilon) for increment in increments]
    qualities: List[float] = []
    total = 0.0
    for increment in perturbed_increments:
        total += increment
        qualities.append(total)
    maximum = qualities[-1]
    qualities = [value / maximum for value in qualities]
    fixed = tuple(cost * rng.uniform(1.0 - epsilon, 1.0 + epsilon) for cost in instance.fixed_costs)
    marginal = tuple(cost * rng.uniform(1.0 - epsilon, 1.0 + epsilon) for cost in instance.marginal_costs)
    theta_factors = {
        theta: rng.uniform(1.0 - epsilon, 1.0 + epsilon)
        for theta in sorted({buyer.theta for buyer in instance.buyers})
    }
    buyers = tuple(
        BuyerType(
            buyer.trust,
            buyer.theta * theta_factors[buyer.theta],
            buyer.weight,
            buyer.name,
        )
        for buyer in instance.buyers
    )
    answer = Instance(
        qualities=tuple(qualities),
        fixed_costs=fixed,
        marginal_costs=marginal,
        safety_ceiling=instance.safety_ceiling,
        buyers=buyers,
        price_grid=(0.0,),
        name=f"{instance.name}-eps{epsilon}-p{seed}",
    )
    answer.validate()
    return answer


def choice_weight(instance: Instance, choices: Sequence[int]) -> Dict[str, float]:
    answer: Dict[str, float] = {}
    for buyer, choice in zip(instance.buyers, choices):
        key = f"g{buyer.trust}:" + ("exit" if choice < 0 else f"m{choice}")
        answer[key] = answer.get(key, 0.0) + float(buyer.weight)
    return answer


def solution_economics(instance: Instance, menu: Sequence[int], prices: Sequence[float], choices: Sequence[int]) -> Dict[str, object]:
    price_map = dict(zip(menu, prices))
    demand = [0.0] * instance.M
    for buyer, choice in zip(instance.buyers, choices):
        if choice >= 0:
            demand[choice] += float(buyer.weight)
    revenue = sum(price_map[m] * demand[m] for m in menu)
    variable = sum(instance.marginal_costs[m] * demand[m] for m in menu)
    fixed = sum(instance.fixed_costs[m] for m in menu)
    participation = sum(demand)
    return {
        "revenue": revenue,
        "fixed_cost": fixed,
        "variable_cost": variable,
        "profit": revenue - fixed - variable,
        "participation_weight": participation,
        "demand_by_product": demand,
        "choice_weight": choice_weight(instance, choices),
    }


def main_payloads(config: Dict[str, object]) -> List[Dict[str, object]]:
    main = config["main"]
    assert isinstance(main, dict)
    payloads = []
    for quality_shape, demand_shape, access_profile, cost_profile, seed_offset in itertools.product(
        main["quality_shapes"],
        main["demand_shapes"],
        main["access_profiles"],
        main["cost_profiles"],
        main["seed_offsets"],
    ):
        payloads.append(
            {
                "suite": "main",
                "quality_shape": quality_shape,
                "demand_shape": demand_shape,
                "access_profile": access_profile,
                "cost_profile": cost_profile,
                "seed_offset": seed_offset,
            }
        )
    return payloads


def sensitivity_payloads(config: Dict[str, object]) -> List[Dict[str, object]]:
    main = config["main"]
    sensitivity = config["sensitivity"]
    assert isinstance(main, dict) and isinstance(sensitivity, dict)
    payloads = []
    for quality_shape, demand_shape, access_profile, cost_profile, seed_offset, epsilon in itertools.product(
        main["quality_shapes"],
        main["demand_shapes"],
        main["access_profiles"],
        main["cost_profiles"],
        sensitivity["nominal_seed_offsets"],
        sensitivity["epsilons"],
    ):
        payloads.append(
            {
                "suite": "sensitivity",
                "quality_shape": quality_shape,
                "demand_shape": demand_shape,
                "access_profile": access_profile,
                "cost_profile": cost_profile,
                "seed_offset": seed_offset,
                "epsilon": epsilon,
            }
        )
    return payloads


def qualification_payloads(config: Dict[str, object]) -> List[Dict[str, object]]:
    qualification = config["qualification"]
    assert isinstance(qualification, dict)
    return [
        {"suite": "qualification", "M": M, "N": N, "B": B, "seed_offset": offset}
        for M in qualification["M"]
        for N in qualification["N"]
        for B in range(1, min(M, N) + 1)
        for offset in qualification["seed_offsets"]
    ]


def build_instance_from_payload(payload: Dict[str, object], config: Dict[str, object]) -> Tuple[Instance, Dict[str, object]]:
    main = config["main"]
    assert isinstance(main, dict)
    return make_s1_instance(
        quality_shape=str(payload["quality_shape"]),
        demand_shape=str(payload["demand_shape"]),
        access_profile=str(payload["access_profile"]),
        cost_profile=str(payload["cost_profile"]),
        M=int(main["M"]),
        B=int(main["B"]),
        T=int(main["T"]),
        N=int(main["N"]),
        seed_base=int(main["seed_base"]),
        seed_offset=int(payload["seed_offset"]),
    )


def run_main_case(payload: Dict[str, object], config: Dict[str, object]) -> Dict[str, object]:
    instance, metadata = build_instance_from_payload(payload, config)
    main = config["main"]
    assert isinstance(main, dict)
    K = int(main["K"])
    timings: Dict[str, float] = {}

    started = time.perf_counter()
    full = solve_persistent_hull_tree(instance, K)
    timings["full_k"] = time.perf_counter() - started
    started = time.perf_counter()
    full_capacity = solve_persistent_hull_tree(instance, instance.M)
    timings["full_capacity"] = time.perf_counter() - started
    started = time.perf_counter()
    path = solve_restricted_pht(instance, K, "path")
    timings["path"] = time.perf_counter() - started
    started = time.perf_counter()
    one = solve_restricted_pht(instance, K, "one_branch")
    timings["one_branch"] = time.perf_counter() - started
    started = time.perf_counter()
    contiguous = solve_contiguous_products(instance, K)
    timings["contiguous"] = time.perf_counter() - started
    started = time.perf_counter()
    greedy = solve_greedy_add_one(instance, K)
    timings["greedy_add_one"] = time.perf_counter() - started

    optimum = float(full.profit_supremum)
    values = {
        "full": optimum,
        "path": float(path.profit_supremum),
        "one_branch": float(one.profit_supremum),
        "contiguous": float(contiguous.profit_supremum),
        "greedy_add_one": float(greedy.profit_supremum),
    }
    denominator = max(1.0, abs(optimum))
    frontier = [float(value) for value in full_capacity.capacity_frontier]
    terminal = frontier[-1]
    frontier_tolerance = 1e-8 * max(1.0, abs(terminal))
    k_star = next(k for k, value in enumerate(frontier) if terminal - value <= frontier_tolerance)
    economics = solution_economics(
        instance,
        full.menu,
        [float(value) for value in full.prices],
        full.assignment,
    )
    replay_gap = float(economics["profit"]) - optimum
    restricted_violation = max(values[name] - optimum for name in values if name != "full")
    row = {
        "suite": "main",
        "status": "complete",
        **metadata,
        "M": instance.M,
        "B": instance.G,
        "K": K,
        "qualities": list(instance.qualities),
        "fixed_costs": list(instance.fixed_costs),
        "marginal_costs": list(instance.marginal_costs),
        "safety_ceilings": list(instance.safety_ceiling),
        "profit": optimum,
        "menu": list(full.menu),
        "prices": [float(value) for value in full.prices],
        "assignment": list(full.assignment),
        "economics": economics,
        "capacity_frontier": frontier,
        "capacity_marginal_values": [frontier[k] - frontier[k - 1] for k in range(1, len(frontier))],
        "k_star": k_star,
        "topology": topology(full),
        "state_count": full.state_count,
        "threshold_count": full.threshold_count,
        "reconstruction_gap": float(full.reconstruction_gap),
        "replay_gap": replay_gap,
        "values": values,
        "losses": {name: (optimum - value) / denominator for name, value in values.items() if name != "full"},
        "restricted_violation": restricted_violation,
        "baseline_menus": {"contiguous": list(contiguous.menu), "greedy_add_one": list(greedy.menu)},
        "timings_seconds": timings,
    }
    return row


def run_sensitivity_case(payload: Dict[str, object], config: Dict[str, object]) -> Dict[str, object]:
    instance, metadata = build_instance_from_payload(payload, config)
    main = config["main"]
    sensitivity = config["sensitivity"]
    assert isinstance(main, dict) and isinstance(sensitivity, dict)
    K = int(main["K"])
    epsilon = float(payload["epsilon"])
    perturb_seed = (
        int(sensitivity["perturb_seed_base"])
        + int(metadata["seed"])
        + int(round(epsilon * 1000)) * 1_000_000
    )
    started = time.perf_counter()
    nominal = solve_persistent_hull_tree(instance, K)
    true_instance = perturb_instance(instance, epsilon, perturb_seed)
    true_opt = solve_persistent_hull_tree(true_instance, K)
    deployed = evaluate(
        true_instance,
        nominal.menu,
        [float(value) for value in nominal.prices],
    )
    denominator = max(1.0, abs(float(true_opt.profit_supremum)))
    regret = (float(true_opt.profit_supremum) - deployed.profit) / denominator
    total_weight = sum(float(buyer.weight) for buyer in true_instance.buyers)
    changed_weight = sum(
        float(buyer.weight)
        for buyer, nominal_choice, deployed_choice in zip(
            true_instance.buyers, nominal.assignment, deployed.choices
        )
        if nominal_choice != deployed_choice
    )
    return {
        "suite": "sensitivity",
        "status": "complete",
        **metadata,
        "M": instance.M,
        "B": instance.G,
        "K": K,
        "epsilon": epsilon,
        "perturb_seed": perturb_seed,
        "nominal_profit": float(nominal.profit_supremum),
        "nominal_menu": list(nominal.menu),
        "nominal_prices": [float(value) for value in nominal.prices],
        "true_opt_profit": float(true_opt.profit_supremum),
        "true_opt_menu": list(true_opt.menu),
        "true_opt_prices": [float(value) for value in true_opt.prices],
        "deployed_profit": deployed.profit,
        "deployed_revenue": deployed.revenue,
        "deployed_fixed_cost": deployed.fixed_cost,
        "deployed_variable_cost": deployed.variable_cost,
        "regret": regret,
        "menu_changed": tuple(nominal.menu) != tuple(true_opt.menu),
        "choice_change_weight_fraction": changed_weight / total_weight,
        "nominal_reconstruction_gap": float(nominal.reconstruction_gap),
        "true_reconstruction_gap": float(true_opt.reconstruction_gap),
        "qualities_true": list(true_instance.qualities),
        "fixed_costs_true": list(true_instance.fixed_costs),
        "marginal_costs_true": list(true_instance.marginal_costs),
        "wall_seconds": time.perf_counter() - started,
    }


def worker(payload: Dict[str, object]) -> Dict[str, object]:
    config = load_config()
    suite = str(payload["suite"])
    if suite == "qualification":
        qualification = config["qualification"]
        assert isinstance(qualification, dict)
        seed = int(qualification["seed_base"]) + int(payload["seed_offset"])
        row = qualification_case(int(payload["M"]), int(payload["B"]), int(payload["N"]), seed)
        row.update({"suite": suite, "seed_offset": payload["seed_offset"], "status": "complete"})
        return row
    if suite == "main":
        return run_main_case(payload, config)
    if suite == "sensitivity":
        return run_sensitivity_case(payload, config)
    raise ValueError(f"unknown suite {suite}")


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
            "stderr": completed.stderr[-6000:],
        }
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            **payload,
            "status": "invalid_json",
            "wall_seconds": time.perf_counter() - started,
            "stdout": completed.stdout[-6000:],
            "stderr": completed.stderr[-6000:],
        }


def row_key(row: Dict[str, object]) -> Tuple[object, ...]:
    if row["suite"] == "qualification":
        return (row["M"], row["N"], row["B"], row["seed_offset"])
    base = (
        row["quality_shape"],
        row["demand_shape"],
        row["access_profile"],
        row["cost_profile"],
        row["seed_offset"],
    )
    return base + ((row.get("epsilon"),) if row["suite"] == "sensitivity" else ())


def write_jsonl_new(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    if path.exists():
        raise FileExistsError(f"formal S1 output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def ensure_metadata(output_dir: Path, design_hashes: Dict[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "run_metadata.json"
    payload = {
        "protocol_id": "SafeRefresh-S1-v1.0",
        "historical_boundary": {"gate_d2": "FAIL", "e8": "NOT_RUN_STOP_RULE", "s1_is_e8": False},
        "design_hashes": design_hashes,
        "code_hashes": {
            Path(__file__).name: sha256(Path(__file__)),
            "persistent_hull_tree_dp.py": sha256(ROOT / "persistent_hull_tree_dp.py"),
            "pht_structure_baselines.py": sha256(ROOT / "pht_structure_baselines.py"),
            "continuous_srmd.py": sha256(ROOT / "continuous_srmd.py"),
            "srmd_direct_milp.py": sha256(ROOT / "srmd_direct_milp.py"),
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
    }
    if path.exists():
        existing = json.loads(path.read_text())
        if existing != payload:
            raise RuntimeError("S1 run metadata/code hash changed between suites")
        return
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def run_suite(suite: str, output_dir: Path, workers: int) -> None:
    design_hashes = verify_frozen_design()
    config = load_config()
    ensure_metadata(output_dir, design_hashes)
    if suite == "qualification":
        payloads = qualification_payloads(config)
    elif suite == "main":
        payloads = main_payloads(config)
    elif suite == "sensitivity":
        payloads = sensitivity_payloads(config)
    else:
        raise ValueError(suite)
    expected = int(config[suite]["expected_instances"] if suite != "sensitivity" else config[suite]["expected_scenarios"])
    if len(payloads) != expected:
        raise AssertionError(f"{suite} payload count {len(payloads)} != frozen {expected}")
    output = output_dir / f"{suite}.jsonl"
    if output.exists():
        raise FileExistsError(f"formal S1 suite already exists: {output}")
    timeout = float(config["execution"]["worker_timeout_seconds"])
    rows: List[Dict[str, object]] = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(isolated_worker, payload, timeout) for payload in payloads]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.append(future.result())
            if index % 25 == 0 or index == len(payloads):
                print(f"S1 {suite}: {index}/{len(payloads)}", flush=True)
    rows.sort(key=row_key)
    write_jsonl_new(output, rows)
    elapsed = time.perf_counter() - started
    print(json.dumps({"suite": suite, "rows": len(rows), "wall_seconds": elapsed, "output": str(output)}, indent=2))


def smoke() -> None:
    verify_frozen_design()
    config = load_config()
    payload = main_payloads(config)[0]
    payload["seed_offset"] = int(config["execution"]["smoke_seed"]) - int(config["main"]["seed_base"])
    row = worker(payload)
    if row["status"] != "complete" or abs(float(row["reconstruction_gap"])) > 1e-6:
        raise AssertionError("S1 smoke failed")
    print(json.dumps({"status": "pass", "profit": row["profit"], "menu": row["menu"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("qualification", "main", "sensitivity"))
    parser.add_argument("--output-dir", default=str(ROOT / "results" / "s1_confirmatory_20260911"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--worker")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.loads(args.worker)), ensure_ascii=False))
        return
    if args.smoke:
        smoke()
        return
    if not args.suite:
        parser.error("choose --suite or --smoke")
    run_suite(args.suite, Path(args.output_dir), args.workers)


if __name__ == "__main__":
    main()
