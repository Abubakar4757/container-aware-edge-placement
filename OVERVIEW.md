# Container-Aware Service Placement at the Edge

## Research question

When does reusing cached layers justify placing a service farther from the user? The project compares deployment time, path latency, and service readiness under the same incoming workloads.

## What we built

We used **EdgeSimPy 1.1.0** to simulate container placement, downloads, shared links, and CPU, memory, and storage limits. Alibaba Cluster Trace v2018 supplies the shape and pairing of CPU and normalized memory profiles. Site and service target medians, image layers, network delays, bandwidth, cache states, and request arrivals are scenario parameters. [Experimental assumptions and references](README.md#experimental-assumptions)

The project implements a local pull estimate that accounts for queued downloads, remaining bytes of active transfers, and reuse of shared in-flight layers. Combined uses that estimate alongside user-path latency and resource pressure. Guarded also enforces a configurable extra-latency cap from the closest feasible server. **Layer-Aware + Queue** uses the same pull estimate to test how much value Combined’s extra terms add.

## Experiments and figures

The final benchmark includes **7,008 runs and 1,056,000 request records**. Each topology crosses three initial cache levels, four nominal offered loads (0.20× to 1.45×), and two bandwidth levels, with 12 seeds per cell. The single-region topology has five sites and 100 requests per run. The multi-region topology has 12 sites across three regions, separate seeds, and 200 requests per run.

| Question | Figure | What to compare |
| --- | --- | --- |
| RQ1 and RQ4: When does layer reuse help? | [Cache × bandwidth heatmap](results/figures/cache-bandwidth-heatmap.png) | Provisioning gain versus Resource Best-Fit on jointly served requests. |
| RQ2: What changes with load? | [Success and CPU occupancy](results/figures/success-vs-offered-load.png) | Eventual readiness and capacity held during provisioning versus after readiness. |
| RQ3: What is the trade-off? | [Latency versus provisioning](results/figures/latency-vs-provisioning.png) | All primary policies plus the 12 ms guarded variant on the multi-region topology. |

The 10, 30, and 60 s readiness rates count **all submitted requests**, including rejections as misses. Paired latency and provisioning statistics use jointly served requests. CPU and memory occupancy reflect simulated reservations over the common arrival window. User-path latency measures one-way propagation.

## Single-region results

- At 0.20× load, Combined placed **97.28%** of requests and held **27.78%** of CPU capacity on average. At 0.55×, these were **76.19%** and **65.85%**. This shows why offered load and occupancy should be reported separately.
- Against the queue-blind version of the same score, Combined saved **10.86 s** of paired provisioning time on limited links and **0.92 s** on fast links. Both 95% intervals exclude zero.
- Against Layer-Aware + Queue, Combined reduced paired mean user-path latency by **2.39 ms** while its estimated provisioning change was **+0.21 s**, with a 95% interval spanning zero. Within 30 s, Combined and Layer-Aware + Queue served **61.87%** and **61.92%** of all requests, respectively.

## Multi-region results

- At 0.20× nominal load, Combined placed **91.54%** of requests and held **33.74%** of CPU capacity. At 0.55×, it placed **72.83%** and held **70.49%**. Even the lowest setting had some rejected requests.
- Combined’s queue estimate saved **24.71 s** of paired provisioning time on limited links and **3.27 s** on fast links versus the queue-blind score. Both 95% intervals exclude zero.
- Layer-Aware + Queue was faster than Combined on jointly served requests by **1.51 s**. Combined reduced paired mean user-path latency by **11.14 ms**. Within 30 s, Combined served **55.27%** of all requests and Layer-Aware + Queue served **55.89%**.
- The 12 ms guarded variant saved **6.32 s** of paired provisioning time versus Nearest Feasible (95% interval of 3.77–8.76 s saved) and raised 30 s readiness by **1.72 percentage points**. Its paired p95 user-path change was **+0.16 ms**, with a 95% interval spanning zero. The 8 and 12 ms variants had zero allowance violations across **67,605** successful placements at those balanced settings.
- Compared with the LRScheduler-inspired baseline, the 12 ms variant lowered paired p95 user-path latency by **10.70 ms**, added **7.13 s** of provisioning time, and lowered overall placement success by **4.43 percentage points**. The guard represents a latency trade-off under the simulated conditions.

## Where to find the evidence

- [Single-region summary](results/summary.json) and [multi-region summary](results/multiregion/summary.json) contain the exact policy means, paired intervals, utilization breakdowns, and five-weight sensitivity results.
- In each results folder, `runs.csv` contains per-run metrics, `paired.csv` contains comparisons, and `requests.csv.gz` contains the request logs.
- Run `python run_project.py` after installing dependencies to execute the demo, tests, and audit. `python run_project.py --full` reproduces both trace-calibrated experiments. [Setup and reproduction](README.md#run-the-project)
