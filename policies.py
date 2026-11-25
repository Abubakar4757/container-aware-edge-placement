"""Placement strategies over a single feasibility-filtered candidate set."""

from dataclasses import dataclass
from math import isfinite

POLICIES = ("random", "first-fit", "best-fit", "layer-aware", "layer-queue", "adaptive-layer", "nearest",
            "combined", "guarded")
PULL_SCALE_SECONDS = 4.0
PATH_SCALE_MS = 20.0
WEIGHTS = {
    "balanced": (0.55, 0.25, 0.20),
    "pull-heavy": (0.75, 0.15, 0.10),
    "latency-heavy": (0.25, 0.60, 0.15),
    "resource-heavy": (0.25, 0.15, 0.60),
    "equal-weight": (1 / 3, 1 / 3, 1 / 3),
}
LABELS = {
    "random": "Random Feasible",
    "first-fit": "First-Fit",
    "best-fit": "Resource Best-Fit",
    "layer-aware": "Layer-Aware Greedy",
    "layer-queue": "Layer-Aware + Queue",
    "adaptive-layer": "LRScheduler-inspired",
    "nearest": "Nearest Feasible",
    "combined": "Combined",
    "guarded": "Latency-Guarded Queue-Aware",
}


@dataclass(frozen=True)
class Candidate:
    name: str
    missing_bytes: int
    new_bytes: int
    pull_seconds: float
    latency_ms: float
    resource_pressure: float
    cached_bytes: int = 0
    cache_fraction: float = 0
    cpu_utilization: float = 0
    utilization_skew: float = 0


@dataclass(frozen=True)
class Guardrails:
    max_detour_ms: float = 8.0
    min_pull_gain_s: float = 0.25

    def __post_init__(self):
        if not isfinite(self.max_detour_ms) or self.max_detour_ms < 0:
            raise ValueError("The maximum detour must be a finite, nonnegative number of milliseconds")
        if not isfinite(self.min_pull_gain_s) or self.min_pull_gain_s < 0:
            raise ValueError("The minimum gain must be a finite, nonnegative number of seconds")


def adaptive_layer_score(candidate):
    """Tang et al.'s adaptive layer weight with LeastAllocated as the resource score."""
    light_and_balanced = (candidate.cached_bytes > 10_000_000
                          and candidate.cpu_utilization < 0.60 and candidate.utilization_skew < 0.16)
    weight = 2.0 if light_and_balanced else 0.5
    return weight * candidate.cache_fraction + (1 - candidate.resource_pressure)


def choose(policy, candidates, rng, weights, guardrails=Guardrails()):
    if not candidates:
        return None
    if policy == "random":
        return rng.choice(candidates)
    if policy == "first-fit":
        return candidates[0]
    if policy == "best-fit":
        return max(candidates, key=lambda candidate: candidate.resource_pressure)
    if policy == "layer-aware":
        return min(candidates, key=lambda candidate: candidate.missing_bytes)
    if policy == "layer-queue":
        return min(candidates, key=lambda candidate: candidate.pull_seconds)
    if policy == "adaptive-layer":
        return max(candidates, key=adaptive_layer_score)
    if policy in ("nearest", "guarded"):
        nearest = min(candidates, key=lambda candidate: candidate.latency_ms)
        if policy == "nearest":
            return nearest
        candidates = [
            candidate for candidate in candidates
            if candidate is nearest or (
                candidate.latency_ms - nearest.latency_ms <= guardrails.max_detour_ms
                and nearest.pull_seconds - candidate.pull_seconds >= guardrails.min_pull_gain_s
            )
        ]
        policy = "combined"
    if policy == "combined":
        pull, latency, resources = weights
        return min(
            candidates,
            key=lambda candidate: (
                pull * candidate.pull_seconds / PULL_SCALE_SECONDS
                + latency * candidate.latency_ms / PATH_SCALE_MS
                + resources * candidate.resource_pressure
            ),
        )
    raise ValueError(f"Unknown policy: {policy}")
