"""Run matched factorial experiments and export data for the report and demo."""

import argparse
import csv
import gzip
import itertools
import json
from dataclasses import asdict, replace
from pathlib import Path
from random import Random
from statistics import mean, median, quantiles

from policies import Guardrails, LABELS, POLICIES, WEIGHTS
from scenarios import MB, demo_scenario, generate
from simulation import simulate
from trace_data import calibrate, fetch

METRICS = ("mean_provisioning_s", "download_mb_per_request", "mean_user_latency_ms",
           "success_pct", "cpu_imbalance_cv", "mean_detour_ms", "max_detour_ms", "over_budget_pct")
COMPARISON_METRICS = (
    "matched_provisioning_s", "download_mb_per_request", "matched_latency_ms", "success_pct",
)


def summary(outcomes, imbalance, budget_ms):
    ready = [row for row in outcomes if row.ready_seconds is not None]
    times = [row.provisioning_seconds for row in ready]
    return {
        "requests": len(outcomes),
        "ready": len(ready),
        "success_pct": len(ready) / len(outcomes) * 100,
        "mean_provisioning_s": mean(times) if times else None,
        "median_provisioning_s": median(times) if times else None,
        "iqr_provisioning_s": (
            quantiles(times, n=4, method="inclusive")[2]
            - quantiles(times, n=4, method="inclusive")[0] if len(times) > 1 else None
        ),
        "mean_user_latency_ms": mean(row.user_latency_ms for row in ready) if ready else None,
        "missing_mb_per_ready": mean(row.missing_bytes for row in ready) / MB if ready else None,
        "download_mb_per_request": sum(row.downloaded_bytes for row in outcomes) / MB / len(outcomes),
        "cpu_imbalance_cv": imbalance,
        "mean_detour_ms": mean(row.detour_ms for row in ready) if ready else None,
        "max_detour_ms": max((row.detour_ms for row in ready), default=None),
        "over_budget_pct": (
            100 * sum(row.detour_ms > budget_ms + 1e-9 for row in ready) / len(ready) if ready else None
        ),
        "resource_rejections": sum(row.rejection == "cpu_or_memory" for row in outcomes),
        "storage_rejections": sum(row.rejection == "storage" for row in outcomes),
    }


def paired_difference(outcomes, reference, run, baseline):
    matched = [(left, right) for left, right in zip(outcomes, reference)
               if left.ready_seconds is not None and right.ready_seconds is not None]
    return {
        "common_ready": len(matched),
        "matched_provisioning_s": (
            mean(left.provisioning_seconds - right.provisioning_seconds for left, right in matched)
            if matched else None
        ),
        "matched_latency_ms": (
            mean(left.user_latency_ms - right.user_latency_ms for left, right in matched)
            if matched else None
        ),
        "download_mb_per_request": run["download_mb_per_request"] - baseline["download_mb_per_request"],
        "success_pct": run["success_pct"] - baseline["success_pct"],
    }


def write_rows(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def intervals(rows, metrics):
    result = {}
    for metric in metrics:
        by_seed = {}
        for row in rows:
            if row[metric] is not None:
                by_seed.setdefault(row["seed"], []).append(row[metric])
        values = [mean(cells) for cells in by_seed.values()]
        if len(values) < 2:
            result[metric] = {"mean": mean(values) if values else None, "low": None, "high": None}
            continue
        rng = Random(8246)
        boot = sorted(mean(rng.choices(values, k=len(values))) for _ in range(2_000))
        result[metric] = {"mean": mean(values), "low": boot[49], "high": boot[1949]}
    return result


def operating_region(metrics, limits):
    bounds = (
        (metrics["matched_provisioning_s"], -limits["min_provisioning_gain_s"], False),
        (metrics["matched_latency_ms"], limits["max_user_latency_cost_ms"], False),
        (metrics["success_pct"], -limits["max_success_cost_pp"], True),
    )
    if any(interval["low"] is None for interval, _, _ in bounds):
        return "insufficient_seeds"
    if any(interval["high"] < limit if larger else interval["low"] > limit
           for interval, limit, larger in bounds):
        return "outside"
    if all(interval["low"] >= limit if larger else interval["high"] <= limit
           for interval, limit, larger in bounds):
        return "within"
    return "uncertain"


def run_experiments(config, output, calibration, limit, pilot=False):
    output.mkdir(parents=True, exist_ok=True)
    cells = list(itertools.product(config["cache_fractions"], config["load_multipliers"],
                                   config["backhaul_mbps"]))
    if pilot:
        cells = [tuple(cell) for cell in config["weight_sensitivity"]]
        seeds = (1, 2, 3)
    else:
        start = config["seed_start"]
        seeds = range(start, start + (limit or config["seed_count"]))
    counts = config["requests_per_run"]
    default_guardrails = Guardrails(**config["guardrails"])
    budget_variants = {f"detour-{budget:g}ms": budget for budget in config["guard_sensitivity_ms"]}
    run_rows, pair_rows = [], []
    with gzip.open(output / "requests.csv.gz", "wt", encoding="utf-8", newline="") as stream:
        request_writer = None
        for cache, load, network in cells:
            for seed in seeds:
                scenario = generate(seed, counts, config["cache_fractions"][cache],
                                    config["load_multipliers"][load], config["backhaul_mbps"][network],
                                    calibration)
                cell = {"cache": cache, "load": load, "network": network, "seed": seed,
                        "initial_byte_coverage": scenario.byte_coverage()}
                variants = [(policy, "balanced") for policy in POLICIES]
                variants.append(("combined", "queue-blind"))
                if [cache, load, network] in config["weight_sensitivity"]:
                    variants.extend(("combined", weight) for weight in WEIGHTS if weight != "balanced")
                    variants.extend(("guarded", label) for label in budget_variants)
                runs = {}
                for policy, variant in variants:
                    limits = replace(default_guardrails, max_detour_ms=budget_variants[variant]) if (
                        variant in budget_variants
                    ) else default_guardrails
                    outcomes, imbalance = simulate(scenario, policy, seed,
                                                   WEIGHTS.get(variant, WEIGHTS["balanced"]),
                                                   use_queue=variant != "queue-blind", guardrails=limits)
                    metrics = summary(outcomes, imbalance, limits.max_detour_ms)
                    if policy == "guarded" and metrics["over_budget_pct"]:
                        raise RuntimeError(f"Latency guard violated for {cell}, {variant}")
                    runs[(policy, variant)] = (outcomes, metrics)
                    keys = {**cell, "policy": policy, "weights": variant, "detour_budget_ms": limits.max_detour_ms}
                    run_rows.append({**keys, **metrics})
                    for outcome in outcomes:
                        row = {**keys, **asdict(outcome)}
                        if request_writer is None:
                            request_writer = csv.DictWriter(stream, fieldnames=list(row))
                            request_writer.writeheader()
                        request_writer.writerow(row)
                for policy, variant in variants:
                    if policy == "best-fit":
                        continue
                    outcomes, metrics = runs[(policy, variant)]
                    references = {
                        "combined": ("layer-aware", "best-fit"),
                        "guarded": ("nearest", "combined", "adaptive-layer", "best-fit"),
                    }.get(policy, ("best-fit",))
                    for reference in references:
                        reference_outcomes, reference_metrics = runs[(reference, "balanced")]
                        pair_rows.append({
                            **cell, "policy": policy, "weights": variant, "reference": reference,
                            **paired_difference(outcomes, reference_outcomes, metrics, reference_metrics),
                        })
                combined, combined_metrics = runs[("combined", "balanced")]
                blind, blind_metrics = runs[("combined", "queue-blind")]
                pair_rows.append({
                    **cell, "policy": "combined", "weights": "balanced", "reference": "queue-blind",
                    **paired_difference(combined, blind, combined_metrics, blind_metrics),
                })
            print(f"Completed {cache} / {load} / {network}", flush=True)
    write_rows(output / "runs.csv", run_rows)
    write_rows(output / "paired.csv", pair_rows)
    result = {
        "source": calibration["source"] if calibration else "synthetic resources",
        "run_count": len(run_rows), "seeds": list(seeds), "cells": len(cells),
        "requests_per_run": counts,
        "guardrails": config["guardrails"],
        "trace_summary": ({key: calibration[key] for key in
                           ("machine_summary", "container_summary", "application_groups")}
                          if calibration else None),
        "policies": {
            policy: intervals([row for row in run_rows if row["policy"] == policy and row["weights"] == "balanced"],
                              METRICS) for policy in POLICIES
        },
        "success_by_load": {
            load: {
                policy: intervals([row for row in run_rows if row["load"] == load
                                   and row["policy"] == policy and row["weights"] == "balanced"],
                                  ("success_pct",))["success_pct"]
                for policy in POLICIES
            }
            for load in dict.fromkeys(load for _, load, _ in cells)
        },
        "paired": {
            policy: intervals([row for row in pair_rows if row["policy"] == policy and row["weights"] == "balanced"
                               and row["reference"] == ("layer-aware" if policy == "combined" else "best-fit")],
                              COMPARISON_METRICS)
            for policy in ("layer-aware", "combined")
        },
        "queue_ablation": intervals([row for row in pair_rows if row["reference"] == "queue-blind"],
                                   COMPARISON_METRICS),
        "guarded": {
            "worst_observed_detour_ms": max(
                row["max_detour_ms"] or 0 for row in run_rows
                if row["policy"] == "guarded" and row["weights"] == "balanced"
            ),
            "paired": {
                reference: intervals([
                    row for row in pair_rows if row["policy"] == "guarded" and row["weights"] == "balanced"
                    and row["reference"] == reference
                ], COMPARISON_METRICS)
                for reference in ("nearest", "combined", "adaptive-layer", "best-fit")
            },
            "paired_by_network": {
                network: {
                    reference: intervals([
                        row for row in pair_rows if row["policy"] == "guarded" and row["weights"] == "balanced"
                        and row["reference"] == reference and row["network"] == network
                    ], COMPARISON_METRICS)
                    for reference in ("nearest", "combined")
                }
                for network in config["backhaul_mbps"]
            },
            "budget_sensitivity": {
                label: {
                    "budget_ms": budget,
                    "metrics": intervals([
                        row for row in pair_rows if row["policy"] == "guarded"
                        and row["weights"] == label and row["reference"] == "nearest"
                        and [row["cache"], row["load"], row["network"]] in config["weight_sensitivity"]
                    ], COMPARISON_METRICS),
                }
                for label, budget in {"balanced": default_guardrails.max_detour_ms, **budget_variants}.items()
            },
        },
        "queue_ablation_by_network": {
            network: intervals([row for row in pair_rows if row["reference"] == "queue-blind"
                                and row["network"] == network], COMPARISON_METRICS)
            for network in config["backhaul_mbps"]
        },
        "operating_limits": config["operating_limits"],
        "operating_regions": {},
        "layer_aware_by_cache_network": {
            f"{cache}/{network}": intervals([
                row for row in pair_rows if row["policy"] == "layer-aware"
                and row["cache"] == cache and row["network"] == network
            ], COMPARISON_METRICS)
            for cache, network in dict.fromkeys((cache, net) for cache, _, net in cells)
        },
        "coverage_pct": {
            cache: 100 * mean(row["initial_byte_coverage"] for row in run_rows
                              if row["cache"] == cache and row["policy"] == "first-fit")
            for cache in config["cache_fractions"] if any(cell[0] == cache for cell in cells)
        },
        "weight_sensitivity": {
            variant: intervals([
                row for row in pair_rows if row["policy"] == "combined" and row["weights"] == variant
                and row["reference"] == "layer-aware"
                and [row["cache"], row["load"], row["network"]] in config["weight_sensitivity"]
            ], COMPARISON_METRICS)
            for variant in WEIGHTS
        },
    }
    for cache, load, network in cells:
        key = f"{cache}/{load}/{network}"
        cell_pairs = [row for row in pair_rows if (row["cache"], row["load"], row["network"]) == (cache, load, network)
                      and row["weights"] == "balanced"]
        result["operating_regions"][key] = {}
        for policy in ("layer-aware", "combined", "guarded"):
            metrics = intervals([row for row in cell_pairs if row["policy"] == policy
                                 and row["reference"] == "best-fit"], COMPARISON_METRICS)
            result["operating_regions"][key][policy] = {
                "classification": operating_region(metrics, config["operating_limits"]), "metrics": metrics,
            }
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def demo(guardrails):
    print("Two servers | near: 2 ms, empty cache | far: 25 ms, warm cache")
    print("The first two requests overlap. Request 2 exceeds every server's CPU capacity.")
    print(f"Guarded policy detour allowance: {guardrails.max_detour_ms:g} ms")
    for policy in POLICIES:
        outcomes, _ = simulate(demo_scenario(), policy, guardrails=guardrails)
        print(f"\n{LABELS[policy]}")
        for row in outcomes:
            if row.rejection:
                print(f"  #{row.request_id} rejected ({row.rejection})")
            else:
                print(f"  #{row.request_id} {row.server:4s}  {row.provisioning_seconds:5.2f} s  "
                      f"{row.user_latency_ms:4.1f} ms  {row.downloaded_bytes / MB:4.0f} MB transferred  "
                      f"{row.detour_ms:4.1f} ms detour")


def compare(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"{data['run_count']} runs | {data['cells']} conditions | {data['source']}")
    print(f"{'Policy':29s} {'Ready %':>8s} {'Startup s':>10s} {'Path ms':>9s} {'Over cap %':>11s}")
    for policy, values in data["policies"].items():
        stats = [values[key]["mean"] for key in ("success_pct", "mean_provisioning_s",
                                                "mean_user_latency_ms", "over_budget_pct")]
        print(f"{LABELS[policy]:29s} " + " ".join(
            f"{value:>{width}.2f}" if value is not None else f"{'n/a':>{width}s}"
            for value, width in zip(stats, (8, 10, 9, 11))
        ))
    guard = data["guarded"]
    print(f"\nGuarded max observed detour: {guard['worst_observed_detour_ms']:.3f} ms "
          f"(cap {data['guardrails']['max_detour_ms']:g} ms)")
    for reference, metrics in guard["paired"].items():
        value = metrics["matched_provisioning_s"]
        if value["low"] is not None:
            print(f"Guarded - {LABELS[reference]} startup: {value['mean']:+.2f} s "
                  f"[{value['low']:+.2f}, {value['high']:+.2f}], paired 95% interval")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fixture = commands.add_parser("demo")
    fixture.add_argument("--latency-budget-ms", type=float, default=Guardrails().max_detour_ms)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--summary", type=Path, default=Path("results/summary.json"))
    download = commands.add_parser("fetch")
    download.add_argument("--raw", type=Path, default=Path("data/raw"))
    prep = commands.add_parser("calibrate")
    prep.add_argument("--raw", type=Path, default=Path("data/raw"))
    prep.add_argument("--output", type=Path, default=Path("data/derived/calibration.json"))
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, default=Path("experiment.json"))
    run.add_argument("--output", type=Path, default=Path("runs/main"))
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--calibration", type=Path)
    source.add_argument("--synthetic", action="store_true")
    run.add_argument("--seeds", type=int, help="Override the seed count for a smoke run")
    run.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    if args.command == "demo":
        try:
            guards = Guardrails(max_detour_ms=args.latency_budget_ms)
        except ValueError as error:
            parser.error(str(error))
        demo(guards)
    elif args.command == "compare":
        compare(args.summary)
    elif args.command == "fetch":
        fetch(args.raw)
    elif args.command == "calibrate":
        profile = calibrate(args.raw, args.output)
        for label in ("machine_summary", "container_summary", "application_groups"):
            print(f"{label}: {profile[label]}")
    else:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if args.seeds is not None and args.seeds < 1:
            parser.error("--seeds must be positive")
        calibration = json.loads(args.calibration.read_text(encoding="utf-8")) if args.calibration else None
        run_experiments(config, args.output, calibration, args.seeds, args.pilot)


if __name__ == "__main__":
    main()
