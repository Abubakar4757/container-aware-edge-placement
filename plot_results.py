"""Plot the latency constraint and startup trade-off from a completed benchmark."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from policies import LABELS

COLORS = {"nearest": "#667681", "adaptive-layer": "#A86020", "combined": "#475AB1", "guarded": "#007E70"}


def plot(data, output):
    metrics = data["policies"]
    policies = tuple(COLORS)
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.1), layout="constrained")
    fig.suptitle("Container placement benchmark", fontsize=17, fontweight="bold", x=0.02, ha="left")
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

    labels = []
    contrasts = data["guarded"]["paired_by_network"]
    for network in ("limited", "fast"):
        for baseline in ("nearest", "combined"):
            row = contrasts[network][baseline]["matched_provisioning_s"]
            position = len(labels)
            labels.append(f"{network.capitalize()} / {LABELS[baseline]}")
            if row["low"] is None:
                right.scatter(row["mean"], position, color=COLORS[baseline], s=32, zorder=3)
                continue
            interval = [[row["mean"] - row["low"]], [row["high"] - row["mean"]]]
            right.errorbar(row["mean"], position, xerr=interval, fmt="o",
                           color=COLORS[baseline], capsize=4, zorder=3)
    right.set_yticks(range(len(labels)), labels)
    right.invert_yaxis()
    right.axvline(0, color="#A8B3BA", linewidth=1)
    right.set_xlabel("Guarded minus baseline startup time (s)")
    right.set_title("Paired provisioning differences", loc="left", fontweight="bold")
    right.grid(axis="x", color="#E3E9EE", linewidth=0.8)
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
