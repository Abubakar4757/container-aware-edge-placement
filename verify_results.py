"""Reconcile published run summaries and paired comparisons with the request logs."""

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from itertools import groupby
from math import ceil, isclose
from pathlib import Path
from statistics import mean, median, quantiles

from experiment import COMPARISON_METRICS, METRICS, intervals
from policies import POLICIES

FACTORS = ("cache", "load", "network", "seed")


def fields(row, names):
    return tuple(row[name] for name in names)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_number(actual, expected, label):
    if expected is None:
        require(actual in ("", None), f"{label}: expected missing metric, got {actual}")
    else:
        require(actual not in ("", None) and isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-9),
                f"{label}: got {actual}, expected {expected}")


def tail(values):
    return sorted(values)[ceil(0.95 * len(values)) - 1] if values else None


def load_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def verify_intervals(published, rows, metrics, label):
    values = [{"seed": int(row["seed"]), **{
        name: float(row[name]) if row[name] else None for name in metrics
    }} for row in rows]
    for metric, expected in intervals(values, metrics).items():
        for statistic, value in expected.items():
            check_number(published[metric][statistic], value, f"{label}: {metric} {statistic}")


def verify_run(rows, run, count):
    key = fields(run, FACTORS + ("policy", "weights"))
    require(len(rows) == count, f"{key}: expected {count} requests, found {len(rows)}")
    workload = []
    ready = {}
    rejected = Counter()
    download_bytes = 0
    violations = 0
    for index, row in enumerate(rows):
        require(int(row["request_id"]) == index, f"{key}: missing, duplicate, or unordered request ID")
        check_number(row["detour_budget_ms"], float(run["detour_budget_ms"]), key)
        workload.append(fields(row, ("request_seconds", "image", "access_point")))
        bytes_pulled = int(row["downloaded_bytes"])
        download_bytes += bytes_pulled
        if not row["ready_seconds"]:
            require(row["rejection"] in ("storage", "cpu_or_memory") and not row["server"] and bytes_pulled == 0,
                    f"{key}: rejection has invalid state")
            rejected[row["rejection"]] += 1
            continue
        latency = float(row["user_latency_ms"])
        startup = float(row["provisioning_seconds"])
        detour = float(row["detour_ms"])
        check_number(startup, float(row["ready_seconds"]) - float(row["request_seconds"]), key)
        check_number(detour, latency - float(row["nearest_feasible_ms"]), key)
        require(startup > 0 and latency >= 0 and detour >= 0 and row["server"] and not row["rejection"]
                and 0 <= bytes_pulled <= int(row["missing_bytes"]), f"{key}: invalid ready request")
        if run["policy"] == "nearest":
            check_number(detour, 0, key)
        violations += detour > float(run["detour_budget_ms"]) + 1e-9
        ready[index] = (startup, latency, detour)
    if run["policy"] == "guarded":
        require(violations == 0, f"{key}: {violations} guarded placements exceeded the configured cap")
    download_mb = download_bytes / 1_000_000 / count
    success = 100 * len(ready) / count
    startup = [metrics[0] for metrics in ready.values()]
    latency = [metrics[1] for metrics in ready.values()]
    detours = [metrics[2] for metrics in ready.values()]
    quartiles = quantiles(startup, n=4, method="inclusive") if len(startup) > 1 else None
    for metric, expected in {
        "requests": count, "ready": len(ready), "success_pct": success,
        "mean_provisioning_s": mean(startup) if ready else None,
        "median_provisioning_s": median(startup) if ready else None,
        "iqr_provisioning_s": quartiles[2] - quartiles[0] if quartiles else None,
        "mean_user_latency_ms": mean(latency) if ready else None,
        "p95_user_latency_ms": tail(latency),
        "missing_mb_per_ready": (mean(int(row["missing_bytes"]) for row in rows if row["ready_seconds"])
                                 / 1_000_000 if ready else None),
        "download_mb_per_request": download_mb,
        "mean_detour_ms": mean(detours) if ready else None,
        "max_detour_ms": max(detours, default=None),
        "over_budget_pct": 100 * violations / len(ready) if ready else None,
        "storage_rejections": rejected["storage"], "resource_rejections": rejected["cpu_or_memory"],
    }.items():
        check_number(run[metric], expected, f"{key}: {metric}")
    return workload, ready, download_mb, success


def verify_pair(pair, runs, key):
    ready, download, success = runs[(pair["policy"], pair["weights"])]
    baseline_key = ("combined", "queue-blind") if pair["reference"] == "queue-blind" else (
        pair["reference"], "balanced",
    )
    baseline, baseline_download, baseline_success = runs[baseline_key]
    matched = sorted(ready.keys() & baseline.keys())
    left = [ready[index][1] for index in matched]
    right = [baseline[index][1] for index in matched]
    for metric, expected in {
        "common_ready": len(matched),
        "matched_provisioning_s": mean(ready[i][0] - baseline[i][0] for i in matched) if matched else None,
        "matched_latency_ms": mean(a - b for a, b in zip(left, right)) if matched else None,
        "matched_p95_latency_ms": tail(left) - tail(right) if matched else None,
        "download_mb_per_request": download - baseline_download,
        "success_pct": success - baseline_success,
    }.items():
        check_number(pair[metric], expected, f"{key}: {pair['policy']} vs {pair['reference']}, {metric}")


def verify(directory):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    run_rows = load_csv(directory / "runs.csv")
    pair_rows = load_csv(directory / "paired.csv")
    runs = {fields(row, FACTORS + ("policy", "weights")): row for row in run_rows}
    pairs = defaultdict(list)
    for row in pair_rows:
        pairs[fields(row, FACTORS)].append(row)
    require(len(runs) == len(run_rows) == summary["run_count"], "Run count or run identity mismatch")
    require(len({fields(row, FACTORS + ("policy", "weights", "reference")) for row in pair_rows})
            == len(pair_rows), "Duplicate paired comparison")
    scenario_keys = set()
    ready_counts = Counter()
    request_total = 0
    remaining = runs.copy()
    with gzip.open(directory / "requests.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        for key, scenario in groupby(csv.DictReader(stream), lambda row: fields(row, FACTORS)):
            require(key not in scenario_keys, f"{key}: duplicate scenario group")
            scenario_keys.add(key)
            scenario_runs = {}
            shared_workload = None
            for variant, group in groupby(scenario, lambda row: fields(row, ("policy", "weights"))):
                rows = list(group)
                request_total += len(rows)
                run = remaining.pop(key + variant)
                workload, ready, download, success = verify_run(rows, run, summary["requests_per_run"])
                if shared_workload is None:
                    shared_workload = workload
                require(workload == shared_workload, f"{key}: policies received different request sequences")
                scenario_runs[variant] = ready, download, success
                if variant[0] == "guarded":
                    ready_counts[variant[1]] += len(ready)
            require(all((policy, "balanced") in scenario_runs for policy in POLICIES),
                    f"{key}: missing primary policy")
            for pair in pairs.pop(key):
                verify_pair(pair, scenario_runs, key)
    expected_keys = {tuple(cell.split("/")) + (str(seed),) for cell in summary["operating_regions"]
                     for seed in summary["seeds"]}
    require(not (remaining or pairs) and scenario_keys == expected_keys, "Missing runs, scenarios, or pairs")
    for policy in POLICIES:
        rows = [row for row in run_rows if row["policy"] == policy and row["weights"] == "balanced"]
        verify_intervals(summary["policies"][policy], rows, METRICS, policy)
    guarded_pairs = [row for row in pair_rows if row["policy"] == "guarded"]
    sweep_cells = {fields(row, FACTORS) for row in guarded_pairs if row["weights"] != "balanced"}
    for reference, published in summary["guarded"]["paired"].items():
        rows = [row for row in guarded_pairs if row["weights"] == "balanced" and row["reference"] == reference]
        verify_intervals(published, rows, COMPARISON_METRICS, f"Guarded vs {reference}")
    for variant, result in summary["guarded"]["by_budget"].items():
        check_number(result["ready_placements"], ready_counts[variant], variant)
        rows = [row for row in run_rows if row["policy"] == "guarded" and row["weights"] == variant]
        for row in rows:
            check_number(row["detour_budget_ms"], result["budget_ms"], variant)
        maximum = max((float(row["max_detour_ms"] or 0) for row in rows), default=0)
        check_number(result["worst_observed_detour_ms"], maximum, variant)
        verify_intervals(result["metrics"], [row for row in rows if fields(row, FACTORS) in sweep_cells],
                         METRICS, variant)
        for reference, published in result["paired"].items():
            pairs_in_sweep = [
                row for row in guarded_pairs if row["weights"] == variant and row["reference"] == reference
                and fields(row, FACTORS) in sweep_cells
            ]
            verify_intervals(published, pairs_in_sweep, COMPARISON_METRICS, f"{variant} vs {reference}")
    print(f"{directory}: {len(runs):,} runs, {request_total:,} request records, "
          f"{len(pair_rows):,} paired comparisons, {sum(ready_counts.values()):,} guarded placements, "
          "zero guard violations.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("results", type=Path, nargs="*", default=[Path("results"), Path("results/multiregion")])
    for target in cli.parse_args().results:
        verify(target)
