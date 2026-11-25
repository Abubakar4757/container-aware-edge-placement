"""Download and calibrate the two Alibaba v2018 metadata tables."""

import csv
import hashlib
import io
import json
import tarfile
from collections import Counter
from pathlib import Path
from urllib.request import urlopen

# Verify the published mirror's downloads against hashes from Alibaba's HTTPS documentation.
BASE_URL = "http://aliopentrace.oss-cn-beijing.aliyuncs.com/v2018Traces"
ARCHIVE_HASHES = {
    "machine_meta": "b5b1b786b22cd413a3674b8f2ebfb2f02fac991c95df537f363ef2797c8f6d55",
    "container_meta": "febd75e693d1f208a8941395e7faa7e466e50d21c256eff12a815b7e2fa2053f",
}


def fetch(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in ARCHIVE_HASHES.items():
        archive = directory / f"{name}.tar.gz"
        if archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest() == expected:
            print(f"{archive.name} already verified")
            continue
        with urlopen(f"{BASE_URL}/{archive.name}", timeout=45) as response:
            content = response.read(8 * 1024 * 1024)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Downloaded {archive.name} has an unexpected SHA-256 checksum")
        partial = archive.with_suffix(".part")
        partial.write_bytes(content)
        partial.replace(archive)
        print(f"{archive.name} downloaded and verified")


def read_table(directory, name):
    archive = directory / f"{name}.tar.gz"
    csv_file = directory / f"{name}.csv"
    if archive.is_file():
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        if checksum != ARCHIVE_HASHES[name]:
            raise ValueError(f"{archive} does not match Alibaba's SHA-256 checksum")
        with tarfile.open(archive, "r:gz") as source:
            members = [
                member for member in source.getmembers()
                if member.isfile() and Path(member.name).name == csv_file.name
            ]
            if len(members) != 1:
                raise ValueError(f"Expected exactly one {csv_file.name} in {archive}")
            with io.TextIOWrapper(source.extractfile(members[0]), encoding="utf-8") as stream:
                yield from csv.reader(stream)
    else:
        with csv_file.open(encoding="utf-8-sig", newline="") as stream:
            yield from csv.reader(stream)


def machine_record(fields):
    identity, timestamp, _, _, cpu, memory, status = fields
    return identity, int(timestamp), float(cpu), float(memory), status, None


def container_record(fields):
    identity, _, timestamp, app, status, cpu, _, memory = fields
    return identity, int(timestamp), float(cpu) / 100, float(memory), status, app


def profiles(directory, name, parse_record):
    unique = {}
    counts = Counter()
    statuses = Counter()
    for fields in read_table(directory, name):
        counts["raw_rows"] += 1
        try:
            identity, timestamp, cpu, memory, status, app = parse_record(fields)
        except ValueError:
            counts["invalid_rows"] += 1
            continue
        statuses[status] += 1
        if not identity or not 0 < cpu < float("inf") or not 0 < memory <= 100:
            counts["invalid_rows"] += 1
            continue
        counts["valid_rows"] += 1
        # Prefer the earliest valid resource declaration, giving each entity one vote.
        if identity not in unique or timestamp < unique[identity][0]:
            unique[identity] = (timestamp, cpu, memory, app)
    counts["unique_entities"] = len(unique)
    if not unique:
        raise ValueError(f"{name} has no usable resource profiles")
    rows = [record[1:] for _, record in sorted(unique.items())]
    return rows, {**counts, "statuses": dict(sorted(statuses.items()))}


def calibrate(directory, output):
    machines, machine_counts = profiles(directory, "machine_meta", machine_record)
    containers, container_counts = profiles(directory, "container_meta", container_record)
    groups = Counter(row[2] for row in containers if row[2])
    # Preserve joint CPU/RAM observations while removing raw entity and app identifiers.
    calibration = {
        "source": "Alibaba Cluster Trace v2018",
        "machine_summary": machine_counts,
        "container_summary": container_counts,
        "application_groups": len(groups),
        "groups_by_size": dict(sorted(Counter(groups.values()).items())),
        "machine_profiles": [row[:2] for row in machines],
        "service_profiles": [row[:2] for row in containers],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(calibration, separators=(",", ":")) + "\n", encoding="utf-8")
    return calibration
