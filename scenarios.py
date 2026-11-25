"""Deterministic workload and network inputs shared by all placement policies."""

from dataclasses import dataclass
from math import ceil
from random import Random
from statistics import mean, median

TICK_SECONDS = 0.25
REGISTRY = "registry"
BACKBONE = "backbone"
MB = 1_000_000
TOPOLOGIES = ("single-region", "multi-region")


@dataclass(frozen=True)
class Server:
    name: str
    cpu: float
    memory: float
    storage: int
    cache: tuple[str, ...] = ()


@dataclass(frozen=True)
class Link:
    left: str
    right: str
    latency_ms: float
    bandwidth_mbps: float


@dataclass(frozen=True)
class Request:
    id: int
    arrival_tick: int
    access_point: str
    image: str
    cpu: float
    memory: float
    lifetime_ticks: int


@dataclass(frozen=True)
class Scenario:
    servers: tuple[Server, ...]
    links: tuple[Link, ...]
    layers: dict[str, int]
    images: dict[str, tuple[str, ...]]
    requests: tuple[Request, ...]

    def byte_coverage(self):
        caches = [set(server.cache) for server in self.servers]
        total = 0
        cached = 0
        for request in self.requests:
            for layer in self.images[request.image]:
                size = self.layers[layer]
                total += size * len(caches)
                cached += size * sum(layer in cache for cache in caches)
        return cached / total if total else 0


def scaled_profiles(profiles, target_cpu, target_memory, cpu_range, memory_range):
    median_cpu = median(cpu for cpu, _ in profiles)
    median_memory = median(memory for _, memory in profiles)

    def scale(value, midpoint, target, bounds):
        lower, upper = bounds
        return round(4 * max(lower, min(upper, target * value / midpoint))) / 4

    return [
        (scale(cpu, median_cpu, target_cpu, cpu_range),
         scale(memory, median_memory, target_memory, memory_range))
        for cpu, memory in profiles
    ]


def network_layout(topology, rng, bandwidth):
    if topology == "single-region":
        return [Link(REGISTRY, BACKBONE, 8, 2 * bandwidth)], (BACKBONE,) * 5
    if topology != "multi-region":
        raise ValueError(f"Unknown topology: {topology}")
    regions = ("north", "central", "south")
    links = [Link(REGISTRY, BACKBONE, 8, 3 * bandwidth)]
    for region in regions:
        links.append(Link(BACKBONE, region, rng.uniform(8, 20), bandwidth * rng.uniform(0.8, 1.2)))
    return links, tuple(region for region in regions for _ in range(4))


def generate(seed, request_count, initial_cache_fraction, load_multiplier, bandwidth, calibration=None,
             topology="single-region"):
    rng = Random(seed)
    networks = Random(seed + 101)
    cache_rng = Random(seed + 203)
    if calibration:
        machines = scaled_profiles(calibration["machine_profiles"], 8, 16, (4, 12), (8, 24))
        services = scaled_profiles(calibration["service_profiles"], 2, 3, (0.5, 6), (0.5, 8))
    else:
        machines = [(6, 12), (8, 16), (8, 12), (10, 20), (12, 16)]
        services = [(1, 1), (1.5, 2), (2, 3), (2.5, 4), (3, 4), (4, 6)]

    layers = {f"base-{index}": rng.randint(15, 75) * MB for index in range(7)}
    images = {}
    for index in range(14):
        private = f"app-{index}"
        layers[private] = rng.randint(8, 65) * MB
        images[f"image-{index}"] = (f"base-{index % 7}", f"base-{(index + 2) % 7}", private)
    catalog_bytes = sum(layers.values())

    servers = []
    links, site_parents = network_layout(topology, networks, bandwidth)
    for index, parent in enumerate(site_parents):
        name = f"edge-{index}"
        cpu, memory = rng.choice(machines)
        storage = round(catalog_bytes * networks.uniform(0.75, 0.85))
        budget = round(storage * initial_cache_fraction)
        cache = []
        cached_bytes = 0
        for layer in cache_rng.sample(list(layers), len(layers)):
            if cached_bytes + layers[layer] <= budget:
                cache.append(layer)
                cached_bytes += layers[layer]
        servers.append(Server(name, cpu, memory, storage, tuple(cache)))
        links.append(Link(parent, name, networks.uniform(3, 14), bandwidth * networks.uniform(0.6, 1.4)))

    image_ids = list(images)
    popularity = [1 / (rank + 1) ** 0.8 for rank in range(len(image_ids))]
    typical_lifetime = 12
    capacity = min(
        sum(server.cpu for server in servers) / mean(cpu for cpu, _ in services),
        sum(server.memory for server in servers) / mean(mem for _, mem in services),
    )
    rate = capacity * load_multiplier / typical_lifetime
    requests = []
    arrival = 0.0
    for index in range(request_count):
        if index:
            arrival += rng.expovariate(rate)
        cpu, memory = rng.choice(services)
        image = rng.choices(image_ids, weights=popularity, k=1)[0]
        access = rng.choice(servers).name
        lifetime = ceil(rng.uniform(6, 18) / TICK_SECONDS)
        requests.append(Request(index, ceil(arrival / TICK_SECONDS), access, image, cpu, memory, lifetime))
    return Scenario(tuple(servers), tuple(links), layers, images, tuple(requests))


def demo_scenario():
    return Scenario(
        (Server("near", 2, 4, 60 * MB), Server("far", 2, 4, 60 * MB, ("base", "app"))),
        (Link(REGISTRY, BACKBONE, 3, 160), Link(BACKBONE, "near", 3, 40),
         Link(BACKBONE, "far", 20, 40)),
        {"base": 15 * MB, "app": 5 * MB},
        {"demo": ("base", "app")},
        (Request(0, 0, "near", "demo", 1, 1, 40),
         Request(1, 1, "near", "demo", 1, 1, 40),
         Request(2, 2, "near", "demo", 3, 1, 40),
         Request(3, 45, "near", "demo", 1, 1, 40)),
    )
