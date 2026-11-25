import unittest
from dataclasses import replace
from random import Random

from experiment import operating_region, p95, paired_difference
from policies import Candidate, Guardrails, POLICIES, WEIGHTS, adaptive_layer_score, choose
from scenarios import MB, demo_scenario, generate
from simulation import simulate


class ProvisioningTests(unittest.TestCase):
    def test_cache_reuse_and_shared_inflight_transfer(self):
        scenario = demo_scenario()
        first, _ = simulate(scenario, "first-fit")
        reuse, _ = simulate(scenario, "layer-aware")
        self.assertEqual(first[0].server, "near")
        self.assertEqual(reuse[0].server, "far")
        self.assertEqual(reuse[0].provisioning_seconds, 0.25)
        self.assertEqual(reuse[0].user_latency_ms, 25)
        self.assertEqual(first[0].downloaded_bytes, 20 * MB)
        self.assertEqual(first[1].downloaded_bytes, 0)
        self.assertEqual(first[1].missing_bytes, 20 * MB)
        self.assertEqual(first[0].ready_seconds, first[1].ready_seconds)
        self.assertEqual(first[2].rejection, "cpu_or_memory")

    def test_single_layer_transfer_takes_link_time_plus_one_tick(self):
        scenario = demo_scenario()
        single = replace(scenario, images={"demo": ("base",)}, requests=scenario.requests[:1])
        first, _ = simulate(single, "first-fit")
        self.assertEqual(first[0].downloaded_bytes, 15 * MB)
        self.assertEqual(first[0].provisioning_seconds, 3.25)

    def test_remaining_flow_receives_bandwidth_after_other_flow_finishes(self):
        first, _ = simulate(demo_scenario(), "first-fit")
        # 20 MB over 5 MB/s with both flows backlogged until the smaller one finishes.
        self.assertEqual(first[0].provisioning_seconds, 4.25)

    def test_storage_capacity_is_a_hard_constraint(self):
        scenario = demo_scenario()
        near, far = scenario.servers
        scenario = replace(scenario, servers=(replace(near, storage=19 * MB), far))
        first, _ = simulate(scenario, "first-fit")
        self.assertEqual(first[0].server, "far")

    def test_exact_end_of_lifetime_frees_cpu_before_admission(self):
        scenario = demo_scenario()
        request = replace(scenario.requests[0], cpu=2, lifetime_ticks=2)
        same_site = replace(request, id=1, arrival_tick=3)
        scenario = replace(scenario, requests=(request, same_site))
        results, _ = simulate(scenario, "layer-aware")
        self.assertEqual([row.server for row in results], ["far", "far"])

    def test_policy_replays_are_deterministic(self):
        scenario = generate(19, 16, 0.45, 1.25, 35)
        for policy in POLICIES:
            with self.subTest(policy=policy):
                first = simulate(scenario, policy, seed=12)
                self.assertEqual(first, simulate(scenario, policy, seed=12))

    def test_region_classifies_interval_against_tolerances(self):
        limits = {"min_provisioning_gain_s": 0.25, "max_user_latency_cost_ms": 2,
                  "max_success_cost_pp": 2}
        metrics = {"matched_provisioning_s": {"low": -1, "high": -0.5},
                   "matched_latency_ms": {"low": 1, "high": 2},
                   "success_pct": {"low": -1, "high": 0}}
        self.assertEqual(operating_region(metrics, limits), "within")
        metrics["matched_latency_ms"]["high"] = 3
        self.assertEqual(operating_region(metrics, limits), "uncertain")
        metrics["matched_latency_ms"]["low"] = 2.1
        self.assertEqual(operating_region(metrics, limits), "outside")

    def test_guard_filters_excess_detour_and_requires_pull_savings(self):
        nearest = Candidate("near", 20 * MB, 20 * MB, 4, 2, 0.2)
        too_far = Candidate("far", 0, 0, 0, 18, 0.2)
        marginal = Candidate("marginal", 19 * MB, 19 * MB, 3.9, 3, 0.2)
        good = Candidate("nearby", 0, 0, 0, 8, 0.2)
        options = [nearest, too_far, marginal]
        self.assertEqual(choose("guarded", options, Random(1), WEIGHTS["balanced"]), nearest)
        self.assertEqual(choose("guarded", options + [good], Random(1), WEIGHTS["balanced"]), good)

    def test_guard_changes_placement_as_allowance_changes(self):
        scenario = demo_scenario()
        strict, _ = simulate(scenario, "guarded")
        tolerant, _ = simulate(scenario, "guarded", guardrails=Guardrails(max_detour_ms=23))
        self.assertEqual((strict[0].server, strict[0].detour_ms), ("near", 0))
        self.assertEqual((tolerant[0].server, tolerant[0].detour_ms), ("far", 23))

    def test_guard_is_measured_from_currently_feasible_sites(self):
        scenario = demo_scenario()
        near, far = scenario.servers
        scenario = replace(scenario, servers=(replace(near, storage=19 * MB), far))
        results, _ = simulate(scenario, "guarded")
        self.assertEqual((results[0].server, results[0].nearest_feasible_ms, results[0].detour_ms),
                         ("far", 25, 0))

    def test_adaptive_layer_policy_reduces_cache_weight_on_busy_nodes(self):
        candidate = Candidate("candidate", 20 * MB, 20 * MB, 4, 10, 0.5,
                              cached_bytes=20 * MB, cache_fraction=0.5, cpu_utilization=0.4)
        self.assertEqual(adaptive_layer_score(candidate), 1.5)
        self.assertEqual(adaptive_layer_score(replace(candidate, cpu_utilization=0.6)), 0.75)

    def test_multiregion_topology_and_placements(self):
        scenario = generate(401, 30, 0.45, 0.95, 35, topology="multi-region")
        self.assertEqual(len(scenario.servers), 12)
        self.assertEqual(len(scenario.links), 16)
        for region in ("north", "central", "south"):
            self.assertEqual(sum(link.left == region for link in scenario.links), 4)
        for allowance in (8, 12):
            results, _ = simulate(scenario, "guarded", guardrails=Guardrails(max_detour_ms=allowance))
            ready = [row for row in results if row.ready_seconds is not None]
            self.assertTrue(ready)
            self.assertTrue(all(row.detour_ms <= allowance for row in ready))

    def test_pairing_requires_matching_request_ids(self):
        outcomes, _ = simulate(demo_scenario(), "nearest")
        with self.assertRaisesRegex(ValueError, "Mismatched paired requests"):
            paired_difference(outcomes, outcomes[::-1], {}, {})

    def test_p95_uses_the_nearest_rank(self):
        self.assertEqual(p95(range(1, 21)), 19)
        self.assertEqual(p95([2]), 2)
        self.assertIsNone(p95([]))


if __name__ == "__main__":
    unittest.main()
