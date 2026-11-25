# Container-Aware Service Placement at the Edge

## Research question

When should an edge scheduler place a service farther from the user to reuse cached container layers and reduce startup time?

## What we built

We used EdgeSimPy 1.1.0 to simulate container placement, image-layer downloads, link contention, and CPU, memory, and storage limits. Alibaba Cluster Trace v2018 metadata calibrated CPU and memory profiles. The edge networks, images, caches, and request arrivals are synthetic and generated from fixed seeds.

Our **latency-guarded, queue-aware policy** considers a farther server only when it meets two conditions. Its extra one-way user-path latency stays within a configurable allowance relative to the closest feasible server, and its estimated image pull is at least 0.25 s faster. The pull estimate includes queued downloads, remaining bytes in active transfers, and newly required layers. The comparison includes Nearest Feasible, an unguarded combined policy, and an adaptation of LRScheduler’s published scoring for the simulator.

## What we found

| Experiment | Setup | Key result |
| --- | --- | --- |
| Single-region | 5 sites, 2,160 runs, 216,000 request records | Queue information saved 12.71 s of paired provisioning time on limited links and 1.22 s on fast links versus the same combined score without queue information. |
| Multi-region | 12 sites across 3 regions, 2,160 runs, 432,000 request records | The 12 ms variant saved 6.94 s versus Nearest Feasible on jointly served requests. The 95% interval was 4.07–9.70 s saved. The paired p95 user-path change was +0.31 ms, with an interval spanning zero. |

Both multi-region guarded variants stayed within their respective 8 ms or 12 ms allowances on all **43,003 successful placements**. The 12 ms variant lowered paired p95 user-path latency by **6.75 ms** versus the LRScheduler-inspired baseline, while provisioning took **6.82 s longer** and placement success fell **4.75 percentage points**. User-path latency measures simulated one-way propagation. Paired comparisons use requests that reached readiness under both policies.

## Files to use

- [README](README.md) for setup, methodology, exact results, and reproduction steps.
- [Single-region summary](results/summary.json) and [chart](results/benchmark.png).
- [Multi-region summary](results/multiregion/summary.json) and [chart](results/multiregion/benchmark.png).
- `runs.csv` for one row per run, `paired.csv` for paired comparisons, and `requests.csv.gz` for request-level records in each results folder.
- `python experiment.py demo` for the live demo after installing the dependencies. `python verify_results.py` audits both result sets.

The attached project files contain the complete implementation and both generated result sets. The reproduction workflow downloads and verifies the original Alibaba metadata separately.
