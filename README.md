# Container-Aware Service Placement at the Edge

An EdgeSimPy implementation of online container placement with reproducible benchmarks. Alibaba Cluster Trace v2018 provides joint CPU and memory profiles. Image layers, caches, topology, arrivals, and bandwidth are generated from fixed seeds.

## Scheduler

**Latency-Guarded Queue-Aware** placement adds a per-request latency cap and download queue awareness to layer-based placement:

1. Filter servers by available CPU, memory, and storage, including disk already reserved for scheduled downloads.
2. Find the feasible server with the shortest one-way user path.
3. Consider alternative servers only when they are within **8 ms** of that path and have at least **0.25 s** lower estimated pull time. The estimate includes queued bytes, active-transfer bytes, and newly required bytes without double-counting shared transfers.
4. Rank the remaining servers using the combined score for pull time, user latency, and resource pressure. The closest feasible server remains eligible throughout.

The latency cap applies relative to the nearest server that can admit that request at decision time. Path latency measures simulated network propagation. Provisioning time is measured separately by the simulator after placement.

The controls include Nearest Feasible, the same combined score without the latency guard, and an LRScheduler-inspired baseline. The latter implements the published adaptive layer weights and thresholds with a CPU/memory LeastAllocated score in place of the Kubernetes plugin stack. This makes the comparison specific to the common simulation environment. [Tang et al., LRScheduler, Sections III–VI](https://arxiv.org/html/2506.03694v1)

## Verified single-region benchmark

- **2,160 runs and 216,000 request records.** The default 8 ms cap held for all 11,888 ready placements made by the guarded policy. Its maximum observed detour was 7.97 ms.
- **Compared with the LRScheduler-inspired baseline**, the guarded policy reduced user-path latency by 6.29 ms on jointly served requests, added 3.91 s of provisioning time, and reduced placement success by 3.11 percentage points.
- **Compared with Nearest Feasible**, the default guarded policy saved an estimated 0.70 s of provisioning time overall. The 95% interval includes zero. In the three-condition sensitivity sweep, a 12 ms cap saved 3.01 s on average with a 95% interval of 0.11 to 5.98 s saved.

## Verified multi-region benchmark

The follow-up adds **2,160 runs and 432,000 request records** across 12 sites and three regions. Both guarded variants enforced their respective caps on every ready placement: **21,388** under the 8 ms cap and **21,615** under the 12 ms cap.

| Policy | Mean provisioning | Mean run p95 user path | Placement success |
| --- | ---: | ---: | ---: |
| LRScheduler-inspired | 61.89 s | 52.32 ms | 54.79% |
| Nearest Feasible | 72.67 s | 46.39 ms | 48.86% |
| Combined, without guard | 44.18 s | 50.04 ms | 56.70% |
| Latency-Guarded, 8 ms | 70.18 s | 46.91 ms | 49.51% |
| Latency-Guarded, 12 ms | 65.89 s | 46.78 ms | 50.03% |

On jointly served requests, the 12 ms variant saved **6.94 s** versus Nearest Feasible (95% interval: 4.07–9.70 s saved). Its paired p95 user-path change was **+0.31 ms** (95% interval: −0.61 to +1.22 ms), and placement success rose **1.17 percentage points**. Against the LRScheduler-inspired baseline, the same variant reduced paired p95 by **6.75 ms**, with **6.82 s** longer provisioning and **4.75 percentage points** lower success. These differences describe the simulated workload and network.

## Run locally

Use Python 3.12 and Git. On Windows, in PowerShell:

```powershell
python -m venv .venv
$python = './.venv/Scripts/python.exe'
& $python -m pip install -r requirements.txt
& $python -m unittest -v
& $python experiment.py demo
& $python experiment.py demo --latency-budget-ms 24
& $python experiment.py compare
& $python experiment.py compare --summary results/multiregion/summary.json
& $python verify_results.py
```

On macOS or Linux, use `.venv/bin/python`. After setup, the demo, tests, and comparison command run offline. In the demo, the near server has a 2 ms user path and a 20 MB cold image. The warm server has a 25 ms path. At an 8 ms detour budget, the guard chooses near. At 24 ms it can choose the warm server, and the CLI shows the actual wait, transfer volume, and detour. The fixture also demonstrates a shared in-flight download and a capacity rejection.

## Reproduce the benchmarks

```powershell
& $python experiment.py fetch
& $python experiment.py calibrate
& $python experiment.py run --calibration data/derived/calibration.json --output runs/single-region
& $python experiment.py run --config experiment.multiregion.json --calibration data/derived/calibration.json --output runs/multi-region
& $python verify_results.py runs/single-region runs/multi-region
& $python plot_results.py --summary runs/multi-region/summary.json --output runs/multi-region/benchmark.png
```

The fetch command retrieves only `machine_meta.tar.gz` and `container_meta.tar.gz`, then checks their published SHA-256 values. If the mirror is unavailable, put the official archives in `data/raw/` and run calibration. Calibration also accepts the two extracted CSV files when the archives are absent. Raw data and derived trace profiles stay local. [Alibaba trace documentation](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md)

For a quick trace-free check, run `experiment.py run --synthetic --seeds 1 --output runs/smoke`.

Both benchmarks cross three initial cache levels, three workload levels, and two nominal bandwidth levels (35 and 160 Mb/s). Policies within each scenario see the same workload and initial state.

- **Single-region.** Five sites share one backbone. Each policy sees 100 requests per scenario. Seeds are 101–112. The 0, 4, 8, 12, and 24 ms allowances are compared in three selected conditions.
- **Multi-region.** Twelve sites are split across three regions. Each region has a shared uplink and all three use the same registry link. Each policy sees 200 requests per scenario, with fresh seeds 401–412. The 8 and 12 ms allowances are compared across all 18 conditions. This evaluates behavior under a change in network structure and workload scale.

Paired provisioning and user-path comparisons use requests that reached readiness under both policies. Transfer and placement success use all submitted requests. The p95 in the policy summary is the mean of scenario-level nearest-rank p95 latencies. The paired p95 delta subtracts each baseline's p95 from the guarded policy's p95 on the jointly served requests within each scenario. Confidence intervals resample seeds while preserving their matched conditions.

| Output | Single-region | Multi-region |
| --- | --- | --- |
| Configuration | [JSON](experiment.json) | [JSON](experiment.multiregion.json) |
| Benchmark plot | [PNG](results/benchmark.png) | [PNG](results/multiregion/benchmark.png) |
| Summary | [JSON](results/summary.json) | [JSON](results/multiregion/summary.json) |
| Per-run metrics | [CSV](results/runs.csv) | [CSV](results/multiregion/runs.csv) |
| Paired comparisons | [CSV](results/paired.csv) | [CSV](results/multiregion/paired.csv) |
| Request traces | [CSV.GZ](results/requests.csv.gz) | [CSV.GZ](results/multiregion/requests.csv.gz) |

The verifier recomputes run metrics and paired differences from the request logs and checks request alignment, rejection accounting, and latency caps on both configurations.
