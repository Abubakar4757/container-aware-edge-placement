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

## Verified benchmark

- **2,160 runs and 216,000 request records.** The default 8 ms cap held for all 11,888 ready placements made by the guarded policy. Its maximum observed detour was 7.97 ms.
- **Compared with the LRScheduler-inspired baseline**, the guarded policy reduced user-path latency by 6.29 ms on jointly served requests, added 3.91 s of provisioning time, and reduced placement success by 3.11 percentage points.
- **Compared with Nearest Feasible**, the default guarded policy saved an estimated 0.70 s of provisioning time overall. The 95% interval includes zero. In the three-condition sensitivity sweep, a 12 ms cap saved 3.01 s on average with a 95% interval of 0.11 to 5.98 s saved.

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
```

On macOS or Linux, use `.venv/bin/python`. After setup, the demo, tests, and comparison command run offline. In the demo, the near server has a 2 ms user path and a 20 MB cold image. The warm server has a 25 ms path. At an 8 ms detour budget, the guard chooses near. At 24 ms it can choose the warm server, and the CLI shows the actual wait, transfer volume, and detour. The fixture also demonstrates a shared in-flight download and a capacity rejection.

## Reproduce the benchmarks

```powershell
& $python experiment.py fetch
& $python experiment.py calibrate
& $python experiment.py run --calibration data/derived/calibration.json --output runs/reproduced
& $python experiment.py compare --summary runs/reproduced/summary.json
& $python plot_results.py --summary runs/reproduced/summary.json --output runs/reproduced/benchmark.png
```

The fetch command retrieves only `machine_meta.tar.gz` and `container_meta.tar.gz`, then checks their published SHA-256 values. If the mirror is unavailable, put the official archives in `data/raw/` and run calibration. Calibration also accepts the two extracted CSV files when the archives are absent. Raw data and derived trace profiles stay local. [Alibaba trace documentation](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md)

For a quick trace-free check, run `experiment.py run --synthetic --seeds 1 --output runs/smoke`.

Each policy receives the same 100 requests for each scenario. The benchmark crosses three initial cache levels, three workload levels, and two bandwidth levels, with 12 seeds per condition. It also evaluates queue-blind and weight variants and sweeps the latency cap over 0, 4, 8, 12, and 24 ms in three selected conditions. Paired provisioning and user-path comparisons use requests that reached readiness under both policies. Transfer and placement success use all submitted requests.

| Output | Contents |
| --- | --- |
| [Benchmark plot](results/benchmark.png) | Detour violations and paired startup trade-offs |
| [Summary](results/summary.json) | Policy means, paired bootstrap intervals, guardrail sweep, and operating conditions |
| [Per-run metrics](results/runs.csv) | Provisioning, download volume, latency, placement success, imbalance, and over-budget placements |
| [Paired comparisons](results/paired.csv) | Matched differences and common ready-request counts |
| [Request traces](results/requests.csv.gz) | Actual placement, timing, transfers, feasible-path reference, detour, and rejection reason |
