# Container-Aware Service Placement at the Edge

An online container-placement simulator built with Python and EdgeSimPy 1.1.0. Read [OVERVIEW.md](OVERVIEW.md) for the handoff summary and final comparisons. [EdgeSimPy paper](https://doi.org/10.1016/j.future.2023.06.013)

## Run the project

Use **Python 3.10 or 3.12** and Git. The published trace-calibrated runs were generated using 3.12. The tests and the one-seed synthetic workflow also ran under 3.10. Create a virtual environment and install the pinned dependencies:

```text
python -m venv .venv
```

| Platform | Install dependencies | Run the demo, tests, and saved-result audit |
| --- | --- | --- |
| macOS or Linux | `.venv/bin/python -m pip install -r requirements.txt` | `.venv/bin/python run_project.py` |
| Windows PowerShell | `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` | `.\.venv\Scripts\python.exe run_project.py` |

With the virtual environment active, run `python experiment.py demo` for the live demonstration. The near server has a 2 ms user path and a 20 MB cold image. The warm server has a 25 ms path. The guarded policy chooses near under its default 8 ms allowance. Try `python experiment.py demo --latency-budget-ms 24` to allow the warm server. The fixture also shows a shared in-flight download and a capacity rejection.

## Reproduce both benchmarks

With the environment active:

```text
python run_project.py --full
```

The command downloads and verifies the two Alibaba metadata archives, preprocesses them, runs both factorial benchmarks, checks the outputs, and generates the figures under `runs/reproduction/`. For an offline smoke experiment, use `python run_project.py --full --synthetic --seeds 1`. The default demo and audit work offline after dependencies are installed.

Only `machine_meta.tar.gz` and `container_meta.tar.gz` are needed. The downloader checks the published SHA-256 hashes. If the mirror is unavailable, place the official archives in `data/raw/`. Extracted CSVs are accepted by `python experiment.py calibrate` when the archives are absent. See [Alibaba’s trace documentation](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md).

## Policy and comparison

The guarded policy filters servers by CPU, memory, and incremental storage. An alternative to the nearest feasible server must stay within the configured extra one-way user-path allowance and offer at least 0.25 s of estimated pull savings. Among the remaining candidates, Combined and Guarded score pull time, path latency, and resource pressure. Pull time is divided by a fixed 4 s reference and user latency by a fixed 20 ms reference. Resource pressure is already a fraction. Fixed references keep the score comparable across environments and do not bound the terms to a maximum of 1.

The pull estimate sums local bytes queued, remaining bytes in active transfers, and bytes from newly required layers. It counts a shared in-flight layer once. The estimate divides this backlog by the uncongested route bottleneck rate. Actual transfer progress and contention are simulated by EdgeSimPy. The score estimates local backlog and does not forecast other sites’ future arrivals.

The **Layer-Aware + Queue** baseline ranks candidates by that same pull estimate. It helps isolate the value of Combined’s latency and resource terms. The queue-ablation experiment holds Combined’s weights fixed and replaces the backlog numerator with image-missing bytes. The **LRScheduler-inspired** comparison uses the weights and thresholds reported in Section VI-A (2.0 and 0.5, 10 MB cached, 0.60 CPU utilization, 0.16 skew), with CPU/memory LeastAllocated as the simulator’s resource score. [Tang et al., LRScheduler](https://arxiv.org/html/2506.03694v1)

The contribution is a comparative study of a local-backlog estimate and a hard, per-request latency rule in an online placement model. Gu et al. already exploit shared image layers. Lou et al. jointly optimize container assignment and layer download order, and their later startup-aware work accounts for bandwidth contention while scheduling dependent tasks. These works motivate the design and put the contribution in context. [Gu et al., 2021](https://doi.org/10.1109/INFOCOM42981.2021.9488779) · [Lou et al., 2023](https://doi.org/10.1109/TSC.2022.3159728) · [Lou et al., 2024](https://doi.org/10.1109/TMC.2023.3238868)

## Experimental assumptions

- **Calibration.** The Alibaba metadata provide the shape and pairing of CPU and normalized memory observations. The preprocessor retains the earliest valid declaration per entity, yielding 4,034 machine and 71,474 container profiles. The simulator rescales them to chosen medians of 8 cores and 16 model memory units per site, and 2 cores and 3 model memory units per service, with clipping at fixed bounds. Model memory units are synthetic and Alibaba’s normalized field supplies no physical RAM measurement. The trace supplies resource profiles only. [Alibaba trace schema](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/schema.txt)
- **Images and network.** Each synthetic image contains two shared layers of 15–75 MB and one private layer of 8–65 MB, giving a 38–215 MB full image range. Nominal bandwidth is 35 or 160 Mb/s to produce contrasting transfer regimes. For scale, a 38–215 MB image on a 35 Mb/s link would take roughly 9–49 s to transfer at full link rate. The 2 ms local hop, 3–14 ms site links, and 8–20 ms inter-region uplinks are chosen propagation settings. Measured container-startup studies motivate varying image transfer cost, while the particular parameter ranges here define synthetic scenarios. [Harter et al., FAST 2016](https://www.usenix.org/conference/fast16/technical-sessions/presentation/harter)
- **Offered load.** The nominal 0.20×, 0.55×, 0.95×, and 1.45× levels scale arrival rates relative to a capacity estimate with a 12 s nominal post-ready lifetime. CPU and memory are reserved while a service provisions, so these multipliers do not predict utilization. Report measured CPU and memory occupancy, including the split between provisioning and ready-service reservations, over the same arrival window for every policy.
- **Design.** Both benchmarks cross three initial cache levels, four offered-load levels, and two bandwidths (24 cells each), with 12 seeds per cell. The five-site single region uses 100 requests per run. The 12-site, three-region topology uses 200 requests and a separate seed range. The 8 and 12 ms guarded variants run across all multi-region cells. Five weight settings for Combined and Guarded are evaluated in four chosen conditions in each topology.
- **Metrics.** Provisioning and path latency comparisons use jointly served requests. The 10, 30, and 60 s readiness rates divide by all submitted requests, counting rejections and late requests as misses. The p95 uses nearest rank per run. Paired p95 differences compare each policy’s p95 on the common ready subset. Confidence intervals resample seed blocks and preserve their matched cells.
- **Scope.** Caches retain layers throughout a run, with storage capacity as a hard constraint. The simulator uses static user locations and reliable links. Path latency represents propagation. The reported reservation fractions describe modeled capacity occupancy.

## Results and figures

| Output | Single-region | Multi-region |
| --- | --- | --- |
| Configuration | [JSON](experiment.json) | [JSON](experiment.multiregion.json) |
| Summary | [JSON](results/summary.json) | [JSON](results/multiregion/summary.json) |
| Per-run metrics | [CSV](results/runs.csv) | [CSV](results/multiregion/runs.csv) |
| Paired comparisons | [CSV](results/paired.csv) | [CSV](results/multiregion/paired.csv) |
| Request-level data | [CSV.GZ](results/requests.csv.gz) | [CSV.GZ](results/multiregion/requests.csv.gz) |

- [RQ1 and RQ4 — Cache × bandwidth heatmap](results/figures/cache-bandwidth-heatmap.png)
- [RQ2 — Success and CPU occupancy versus offered load](results/figures/success-vs-offered-load.png)
- [RQ3 — User-path latency versus provisioning time for all policies](results/figures/latency-vs-provisioning.png)

The verifier reconciles both log sets against run metrics and paired statistics, verifies matched input requests and cap enforcement, and reconstructs resource occupancy and deadline readiness from request records. The source code is distributed under the [GNU GPL v3](LICENSE).
