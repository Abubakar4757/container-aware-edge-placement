"""Plot the latency constraint and startup trade-off from a completed benchmark."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from policies import LABELS

COLORS = {"nearest": "#667681", "adaptive-layer": "#A86020", "combined": "#475AB1", "guarded": "#007E70"}


def plot_contrasts(axis, contrasts, title, xlabel):
    labels = []
    for label, row, color in contrasts:
        position = len(labels)
        labels.append(label)
        if row["mean"] is None:
            continue
        if row["low"] is None:
            axis.scatter(row["mean"], position, color=color, s=32, zorder=3)
        else:
            interval = [[row["mean"] - row["low"]], [row["high"] - row["mean"]]]
            axis.errorbar(row["mean"], position, xerr=interval, fmt="o", color=color, capsize=4, zorder=3)
    axis.set_yticks(range(len(labels)), labels)
    axis.invert_yaxis()
    axis.axvline(0, color="#A8B3BA", linewidth=1)
    axis.set_xlabel(xlabel)
    axis.set_title(title, loc="left", fontweight="bold")
    axis.grid(axis="x", color="#E3E9EE", linewidth=0.8)


def plot(data, output):
    metrics = data["policies"]
    policies = tuple(COLORS)
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.1), layout="constrained")
    topology = data["topology"].replace("-", " ").capitalize()
    fig.suptitle(f"{topology} placement benchmark", fontsize=17, fontweight="bold", x=0.02, ha="left")
    threshold = data["guardrails"]["max_detour_ms"]
    violations = [metrics[policy]["over_budget_pct"]["mean"] for policy in policies]
    bars = left.barh(range(len(policies)), violations, color=[COLORS[p] for p in policies], height=0.55)
    left.set_yticks(range(len(policies)), [LABELS[p] for p in policies])
    left.invert_yaxis()
    left.set_xlim(0, max(violations) * 1.3 + 1)
    left.bar_label(bars, labels=[f"{value:.1f}%" for value in violations], padding=5)
    left.set_xlabel(f"Ready placements more than {threshold:g} ms above closest feasible site")
    left.set_title("Latency allowance", loc="left", fontweight="bold")
    left.grid(axis="x", color="#E3E9EE", linewidth=0.8)

    if data["topology"] == "multi-region":
        contrasts = [
            (f'{variant["budget_ms"]:g} ms / {LABELS[baseline]}',
             variant["paired"][baseline]["matched_p95_latency_ms"], COLORS[baseline])
            for variant in sorted(data["guarded"]["by_budget"].values(), key=lambda item: item["budget_ms"])
            for baseline in ("nearest", "adaptive-layer", "combined")
        ]
        plot_contrasts(right, contrasts, "Paired p95 user-path latency", "Guarded minus baseline p95 (ms)")
    else:
        contrasts = [
            (f"{network.capitalize()} / {LABELS[baseline]}",
             baselines[baseline]["matched_provisioning_s"], COLORS[baseline])
            for network, baselines in data["guarded"]["paired_by_network"].items()
            for baseline in ("nearest", "combined")
        ]
        plot_contrasts(right, contrasts, "Paired provisioning differences", "Guarded minus baseline startup time (s)")
    for axis in (left, right):
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.spines["bottom"].set_color("#CDD6DC")
        axis.tick_params(length=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path("results/summary.json"))
    parser.add_argument("--output", type=Path, default=Path("results/benchmark.png"))
    args = parser.parse_args()
    plot(json.loads(args.summary.read_text(encoding="utf-8")), args.output)


if __name__ == "__main__":
    main()
