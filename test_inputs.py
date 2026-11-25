import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scenarios import generate, scaled_profiles
from trace_data import ARCHIVE_HASHES, calibrate, read_table


class ScenarioTests(unittest.TestCase):
    def test_capacity_cache_and_request_constraints(self):
        for topology in ("single-region", "multi-region"):
            with self.subTest(topology=topology):
                scenario = generate(1, 40, 0.45, 0.2, 35, topology=topology)
                servers = {server.name: server for server in scenario.servers}
                self.assertEqual(len(scenario.requests), 40)
                self.assertEqual([request.id for request in scenario.requests], list(range(40)))
                arrivals = [request.arrival_tick for request in scenario.requests]
                self.assertEqual(arrivals, sorted(arrivals))
                for server in servers.values():
                    self.assertEqual(len(server.cache), len(set(server.cache)))
                    self.assertLessEqual(sum(scenario.layers[layer] for layer in server.cache),
                                         round(0.45 * server.storage))
                for request in scenario.requests:
                    self.assertIn(request.access_point, servers)
                    self.assertTrue(set(scenario.images[request.image]) <= scenario.layers.keys())
                    self.assertGreater(request.lifetime_ticks, 0)

    def test_factor_changes_share_the_same_request_inputs(self):
        cold = generate(30, 25, 0, 0.2, 35)
        warm = generate(30, 25, 0.9, 0.2, 160)
        self.assertEqual(cold.requests, warm.requests)
        self.assertEqual(cold.layers, warm.layers)
        self.assertEqual(cold.byte_coverage(), 0)
        self.assertGreater(warm.byte_coverage(), 0)
        heavy = generate(30, 25, 0, 1.45, 35)
        self.assertLess(heavy.requests[-1].arrival_tick, cold.requests[-1].arrival_tick)
        self.assertEqual([replace(request, arrival_tick=0) for request in cold.requests],
                         [replace(request, arrival_tick=0) for request in heavy.requests])

    def test_scaling_preserves_pairs_and_caps_extremes(self):
        self.assertEqual(
            scaled_profiles([(1, 10), (2, 5), (50, 20)], 2, 3, (0.5, 6), (0.5, 8)),
            [(1, 3), (2, 1.5), (6, 6)],
        )


class TraceInputTests(unittest.TestCase):
    def test_calibration_preserves_pairs_and_earliest_valid_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "machine_meta.csv").write_text(
                "m2,20,domain,zone,8,25,USING\nm1,12,domain,zone,4,20,USING\n"
                "m1,5,domain,zone,16,40,USING\nm1,1,domain,zone,-1,40,USING\n",
                encoding="utf-8",
            )
            (directory / "container_meta.csv").write_text(
                "c2,m2,5,g,started,250,400,20\nc1,m1,8,g,started,50,100,10\n"
                "c1,m1,3,g,allocated,100,200,12\nmalformed\n",
                encoding="utf-8",
            )
            target = directory / "derived" / "output.json"
            profile = calibrate(directory, target)
            self.assertEqual(profile["machine_profiles"], [(16.0, 40.0), (8.0, 25.0)])
            self.assertEqual(profile["service_profiles"], [(1.0, 12.0), (2.5, 20.0)])
            self.assertEqual(profile["machine_summary"]["invalid_rows"], 1)
            self.assertEqual(profile["container_summary"]["invalid_rows"], 1)
            self.assertEqual(profile["application_groups"], 1)
            self.assertEqual(json.loads(target.read_text())["groups_by_size"], {"2": 1})

    def test_archive_checksum_and_member_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "machine_meta.tar.gz"
            content = b"m,1,d,z,8,20,USING\n"
            with tarfile.open(archive, "w:gz") as package:
                member = tarfile.TarInfo("tables/machine_meta.csv")
                member.size = len(content)
                package.addfile(member, io.BytesIO(content))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                list(read_table(directory, "machine_meta"))
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.dict(ARCHIVE_HASHES, {"machine_meta": checksum}):
                self.assertEqual(list(read_table(directory, "machine_meta"))[0][0], "m")


if __name__ == "__main__":
    unittest.main()
