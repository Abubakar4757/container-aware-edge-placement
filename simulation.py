"""EdgeSimPy adapter for online service admission and container provisioning."""

from dataclasses import dataclass
from heapq import heappop, heappush
from random import Random
from statistics import mean, pstdev

import networkx as nx
from edge_sim_py import Application, EdgeServer, NetworkFlow, Service, Simulator

from policies import Candidate, Guardrails, POLICIES, WEIGHTS, choose
from scenarios import REGISTRY, TICK_SECONDS

ACCESS_DELAY_MS = 2


@dataclass
class Outcome:
    request_id: int
    request_seconds: float
    image: str
    access_point: str
    server: str | None = None
    ready_seconds: float | None = None
    provisioning_seconds: float | None = None
    missing_bytes: int | None = None
    downloaded_bytes: int = 0
    user_latency_ms: float | None = None
    nearest_feasible_ms: float | None = None
    detour_ms: float | None = None
    rejection: str | None = None


def dataset_for(scenario):
    """Build an EdgeSimPy dataset using its public attributes/relationships format."""
    dataset = {}

    def add(kind, relationships=None, **attributes):
        records = dataset.setdefault(kind, [])
        obj_id = len(records) + 1
        records.append({"attributes": {"id": obj_id, **attributes}, "relationships": relationships or {}})
        return {"class": kind, "id": obj_id}

    node_names = dict.fromkeys(node for link in scenario.links for node in (link.left, link.right))
    switches = {name: add("NetworkSwitch") for name in node_names}
    for link in scenario.links:
        add("NetworkLink", {"nodes": [switches[link.left], switches[link.right]]},
            delay=link.latency_ms, bandwidth=link.bandwidth_mbps * 1_000_000 / 8 * TICK_SECONDS)
    for image, layers in scenario.images.items():
        add("ContainerImage", name=image, digest=image, layers_digests=list(layers))

    def add_server(name, cpu, memory, disk, layer_ids):
        station = add("BaseStation", {"network_switch": switches[name]})
        server = add("EdgeServer", {"base_station": station, "network_switch": switches[name]},
                     model_name=name, cpu=cpu, memory=memory, disk=disk)
        server_record = dataset["EdgeServer"][-1]
        layers = [add("ContainerLayer", {"server": server}, digest=layer, size=scenario.layers[layer])
                  for layer in layer_ids]
        server_record["relationships"]["container_layers"] = layers
        server_record["attributes"]["disk_demand"] = sum(scenario.layers[layer] for layer in layer_ids)
        return server

    registry = add_server(REGISTRY, 1, 1, sum(scenario.layers.values()), list(scenario.layers))
    registry_ref = add("ContainerRegistry", {"server": registry})
    dataset["EdgeServer"][registry["id"] - 1]["relationships"]["container_registries"] = [registry_ref]
    for server in scenario.servers:
        add_server(server.name, server.cpu, server.memory, server.storage, server.cache)
    return dataset


class PlacementRun:
    def __init__(self, scenario, policy, seed, weights, use_queue, guardrails):
        self.scenario = scenario
        self.policy = policy
        self.rng = Random(seed)
        self.weights = weights
        self.use_queue = use_queue
        self.guardrails = guardrails
        self.model = Simulator(
            resource_management_algorithm=self.admit,
            resource_management_algorithm_parameters={},
            tick_duration=round(TICK_SECONDS * 1_000),
            tick_unit="milliseconds",
            dump_interval=float("inf"),
        )
        self.model.initialize(dataset_for(scenario))
        servers = {server.model_name: server for server in EdgeServer.all()}
        registry = servers.pop(REGISTRY)
        self.servers = servers
        graph = self.model.topology
        self.rates = {}
        self.latencies = {}
        for name, server in servers.items():
            path = nx.shortest_path(graph, registry.network_switch, server.network_switch)
            links = [graph[left][right] for left, right in zip(path, path[1:])]
            self.rates[name] = min(link.bandwidth for link in links) / TICK_SECONDS
            self.latencies[name] = {
                point: ACCESS_DELAY_MS + nx.shortest_path_length(
                    graph, source.network_switch, server.network_switch, weight="delay",
                )
                for point, source in servers.items()
            }
        self.outcomes = []
        self.pending = []
        self.expirations = []
        self.imbalance = []
        self.request_cursor = 0

    def candidates(self, request, service):
        candidates = []
        image = self.scenario.images[request.image]
        sizes = self.scenario.layers
        image_bytes = sum(sizes[layer] for layer in image)
        for name, server in self.servers.items():
            if not server.has_capacity_to_host(service):
                continue
            cached = {layer.digest for layer in server.container_layers}
            queued = {layer.digest: layer.size for layer in server.waiting_queue}
            queued.update({flow.metadata["object"].digest: flow.data_to_transfer for flow in server.download_queue})
            missing = sum(sizes[layer] for layer in image if layer not in cached)
            new = sum(sizes[layer] for layer in image if layer not in cached and layer not in queued)
            if self.use_queue:
                estimated_bytes = sum(queued.values()) + new if missing else 0
            else:
                estimated_bytes = missing
            needs_pull = estimated_bytes / self.rates[name]
            pressure = ((server.cpu_demand + request.cpu) / server.cpu
                        + (server.memory_demand + request.memory) / server.memory) / 2
            cpu = server.cpu_demand / server.cpu
            memory = server.memory_demand / server.memory
            cached_bytes = image_bytes - missing
            candidates.append(Candidate(name, missing, new, needs_pull,
                                        self.latencies[name][request.access_point], pressure,
                                        cached_bytes, cached_bytes / image_bytes, cpu, abs(cpu - memory) / 2))
        return candidates

    def admit(self, parameters):
        tick = parameters["current_step"] - 1
        while self.expirations and self.expirations[0][0] <= tick:
            _, _, service = heappop(self.expirations)
            server = service.server
            server.cpu_demand -= service.cpu_demand
            server.memory_demand -= service.memory_demand
            server.services.remove(service)
            self.remove(service.application)
            self.remove(service)
        requests = self.scenario.requests
        while self.request_cursor < len(requests) and requests[self.request_cursor].arrival_tick == tick:
            request = requests[self.request_cursor]
            self.request_cursor += 1
            result = Outcome(request.id, tick * TICK_SECONDS, request.image, request.access_point)
            self.outcomes.append(result)
            service = Service(image_digest=request.image, cpu_demand=request.cpu, memory_demand=request.memory)
            candidates = self.candidates(request, service)
            selected = choose(self.policy, candidates, self.rng, self.weights, self.guardrails)
            if selected is None:
                Service.remove(service)
                fits_compute = any(
                    server.cpu - server.cpu_demand >= request.cpu
                    and server.memory - server.memory_demand >= request.memory
                    for server in self.servers.values()
                )
                result.rejection = "storage" if fits_compute else "cpu_or_memory"
            else:
                app = Application(label=f"request-{request.id}")
                self.model.initialize_agent(app)
                self.model.initialize_agent(service)
                service.application = app
                app.services.append(service)
                service.provision(self.servers[selected.name])
                result.server = selected.name
                result.missing_bytes = selected.missing_bytes
                result.downloaded_bytes = selected.new_bytes
                result.user_latency_ms = selected.latency_ms
                result.nearest_feasible_ms = min(candidate.latency_ms for candidate in candidates)
                result.detour_ms = result.user_latency_ms - result.nearest_feasible_ms
                self.pending.append((request, service, result))
            utilization = [server.cpu_demand / server.cpu for server in self.servers.values()]
            self.imbalance.append(pstdev(utilization) / mean(utilization) if any(utilization) else 0)

    def remove(self, component):
        self.model.schedule.remove(component)
        type(component).remove(component)

    def execute(self):
        requests = self.scenario.requests
        if not requests:
            return [], 0
        final_arrival = requests[-1].arrival_tick
        # Every link and layer is finite. Fail loudly if a run unexpectedly stops making progress.
        deadline = final_arrival + round(3_600 / TICK_SECONDS)
        try:
            while self.request_cursor < len(requests) or self.pending:
                self.model.step()
                tick = self.model.schedule.steps
                still_pending = []
                for request, service, result in self.pending:
                    if service.being_provisioned:
                        still_pending.append((request, service, result))
                    else:
                        result.ready_seconds = tick * TICK_SECONDS
                        result.provisioning_seconds = result.ready_seconds - result.request_seconds
                        heappush(self.expirations, (tick + request.lifetime_ticks, request.id, service))
                self.pending = still_pending
                for flow in list(NetworkFlow.all()):
                    # EdgeSimPy recalculates surviving flows' bandwidth on the tick after a flow ends.
                    if flow.status == "finished" and flow.end < tick:
                        self.remove(flow)
                if tick > deadline:
                    raise RuntimeError("Provisioning failed to finish within the simulated deadline")
            return self.outcomes, mean(self.imbalance)
        finally:
            Simulator.remove(self.model)


def simulate(scenario, policy, seed=0, weights=WEIGHTS["balanced"], use_queue=True,
             guardrails=Guardrails()):
    if policy not in POLICIES:
        raise ValueError(f"Unknown policy: {policy}")
    return PlacementRun(scenario, policy, seed, weights, use_queue, guardrails).execute()
