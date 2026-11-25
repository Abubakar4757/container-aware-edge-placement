"""Generate the research-question figures from the two published summaries."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

from policies import LABELS, POLICIES

COLORS = dict(zip(POLICIES, (
    "#64748b", "#bd6d37", "#2b8296", "#8261a3", "#ac433e", "#734612", "#5b7b42", "#375fbd", "#007e70",
), strict=True))


def heatmap(data, output):
    caches = ("cold", "partial", "warm")
    networks = ("limited", "fast")
    values = []
    for summary in data:
        comparisons = summary["layer_aware_by_cache_network"]
        values.append([
            [-comparisons[f"{cache}/{network}"]["matched_provisioning_s"]["mean"] for network in networks]
            for cache in caches
        ])
    magnitude = max(1, max(abs(value) for panel in values for row in panel for value in row))
    colors = TwoSlopeNorm(vmin=-magnitude, vcenter=0, vmax=magnitude)
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.5), layout="constrained")
    fig.suptitle("RQ1 and RQ4 | Provisioning gain from layer awareness", fontsize=15, weight="bold")
    for axis, summary, panel in zip(axes, data, values):
        mesh = axis.imshow(panel, cmap="RdBu", norm=colors, aspect="auto")
        axis.set_title(summary["topology"].capitalize())
        axis.set_xticks(range(len(networks)), ["35 Mb/s", "160 Mb/s"])
        labels = [f"{cache.title()} ({summary['coverage_pct'][cache]:.0f}% bytes)" for cache in caches]
        axis.set_yticks(range(len(caches)), labels)
        axis.set_xlabel("Nominal bandwidth")
        for index, row in enumerate(panel):
            for column, value in enumerate(row):
                axis.text(column, index, f"{value:+.1f}", ha="center", va="center", weight="bold",
                          color="white" if abs(value) > 0.55 * magnitude else "#182535")
    fig.colorbar(mesh, ax=axes, label="Seconds saved vs Resource Best-Fit")
    fig.supxlabel("Layer-Aware Greedy vs Resource Best-Fit on jointly served requests, averaged over loads.",
                 fontsize=9)
    save(fig, output)


def load_plot(data, output):
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), sharex="col", sharey="row", layout="constrained")
    fig.suptitle("RQ2 | Placement success and CPU occupancy by offered load", fontsize=15, weight="bold")
    featured = ("best-fit", "layer-aware", "layer-queue", "combined", "guarded")
    for index, summary in enumerate(data):
        top, bottom = axes[:, index]
        levels = sorted(summary["offered_load_multipliers"], key=summary["offered_load_multipliers"].get)
        loads = [summary["offered_load_multipliers"][level] for level in levels]
        profile = summary["load_profile"]
        for policy in featured:
            values = [profile[level][policy]["success_pct"]["mean"] for level in levels]
            top.plot(loads, values, marker="o", lw=1.5, color=COLORS[policy], label=LABELS[policy])
        top.set_title(summary["topology"].capitalize())
        bottom.set_title("Combined policy CPU reservations", fontsize=10)
        starting = [profile[level]["combined"]["cpu_provisioning_pct"]["mean"] for level in levels]
        active = [profile[level]["combined"]["cpu_ready_pct"]["mean"] for level in levels]
        bottom.bar(loads, active, width=0.11, color="#387f72", label="Ready services")
        bottom.bar(loads, starting, width=0.11, bottom=active, color="#9fbdd9", label="Provisioning")
        bottom.set_xticks(loads, [f"{value:.2f}×" for value in loads])
        bottom.set_xlabel("Nominal offered load")
        for axis in (top, bottom):
            axis.set_ylim(0, 103)
            axis.set_axisbelow(True)
            axis.grid(axis="y", alpha=0.24)
    axes[0, 0].set_ylabel("Requests ultimately ready (%)")
    axes[1, 0].set_ylabel("Time-weighted CPU occupancy (%)")
    axes[0, 1].legend(loc="lower left", fontsize=8, frameon=False)
    axes[1, 1].legend(loc="upper left", fontsize=8, frameon=False)
    save(fig, output)


def tradeoff(data, output):
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.3), layout="constrained")
    fig.suptitle("RQ3 | User-path latency versus service provisioning", fontsize=15, weight="bold")
    markers = ("o", "x", "s", "v", "D", "^", "P", "h", "*")
    handles = []
    for axis, summary in zip(axes, data):
        axis.set_title(summary["topology"].capitalize())
        axis.set_xlabel("Mean provisioning time (s)")
        axis.grid(alpha=0.24)
        points = []
        for policy, marker in zip(POLICIES, markers, strict=True):
            metric = summary["policies"][policy]
            points.append(axis.scatter(metric["mean_provisioning_s"]["mean"], metric["mean_user_latency_ms"]["mean"],
                                       color=COLORS[policy], marker=marker, s=116 if marker == "x" else 77,
                                       label=LABELS[policy], zorder=5 if marker == "x" else 3))
        if not handles:
            handles.extend(points)
        if summary["topology"] == "multi-region":
            metrics = summary["guarded"]["by_budget"]["detour-12ms"]["metrics"]
            handles.append(axis.scatter(metrics["mean_provisioning_s"]["mean"],
                                        metrics["mean_user_latency_ms"]["mean"], marker="*",
                                        facecolors="white", edgecolors=COLORS["guarded"], linewidth=1.55,
                                        s=110, label="Latency-Guarded (12 ms)", zorder=4))
    axes[0].set_ylabel("Mean one-way user-path latency (ms)")
    handles_and_labels = [(handle, handle.get_label()) for handle in handles]
    fig.legend(*zip(*handles_and_labels), loc="outside lower center", ncols=3, frameon=False, fontsize=8)
    save(fig, output)


def save(figure, output):
    figure.savefig(output, dpi=180, facecolor="white")
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", type=Path, default=Path("results/summary.json"))
    parser.add_argument("--multi", type=Path, default=Path("results/multiregion/summary.json"))
    parser.add_argument("--output", type=Path, default=Path("results/figures"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = [json.loads(path.read_text(encoding="utf-8")) for path in (args.single, args.multi)]
    heatmap(summaries, args.output / "cache-bandwidth-heatmap.png")
    load_plot(summaries, args.output / "success-vs-offered-load.png")
    tradeoff(summaries, args.output / "latency-vs-provisioning.png")


if __name__ == "__main__":
    main()
