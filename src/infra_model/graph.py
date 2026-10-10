"""Normalized topology with a private NetworkX MultiDiGraph implementation."""

from copy import deepcopy
from typing import Any, Literal

import networkx as nx

from .registry import Registry


class Topology:
    """Read-only dictionary views of entities and directed relationships.

    Physical connections have reciprocal edges. All other edges follow the
    direction described in the model specification. Parallel links retain
    their distinct canonical Link IDs as edge keys.
    """

    def __init__(self, registry: Registry):
        self._graph = nx.MultiDiGraph()
        for entry in registry.entries.values():
            self._graph.add_node(entry.id, **deepcopy(entry.data))
        for entry in registry.entries.values():
            data = entry.data
            if entry.parent:
                self._edge(entry.parent, entry.id, "contains", entry.id)
            fields = data["spec"] if entry.parent is None else data
            if entry.kind == "ClusterNode":
                fields = data.get("placement", {})
            if "siteRef" in fields:
                relation = "targets_site" if entry.kind in {"Cluster", "ClusterNode"} else "located_at"
                self._edge(entry.id, fields["siteRef"], relation)
            elif entry.kind == "ClusterNode" and registry.node_site(entry):
                self._edge(entry.id, registry.node_site(entry), "targets_site")
            if entry.kind == "Network":
                for site in fields.get("siteRefs", []):
                    self._edge(entry.id, site, "scoped_to")
            if "profileRef" in fields:
                self._edge(entry.id, fields["profileRef"], "uses_profile")
            if entry.kind == "ClusterNode" and "resourceRef" in fields:
                self._edge(entry.id, fields["resourceRef"], "placed_on")
            elif entry.kind == "NetworkAttachment":
                self._edge(entry.id, data["networkRef"], "attached_to")
                self._edge(entry.parent, data["networkRef"], "network_attachment", entry.id, attachment=entry.id)
            elif entry.kind == "Link":
                left, right = fields["endpoints"]
                for endpoint in (left, right):
                    self._edge(entry.id, endpoint, "terminates_at", endpoint)
                self._edge(left, right, "physical_link", entry.id, link=entry.id)
                self._edge(right, left, "physical_link", entry.id, link=entry.id)
            if entry.kind in {"StorageController", "StorageDevice", "StorageVolume"}:
                relation = {"StorageController": "has_storage_controller",
                            "StorageDevice": "has_storage_device",
                            "StorageVolume": "has_storage_volume"}[entry.kind]
                self._edge(entry.parent, entry.id, relation)
                if "controllerRef" in data:
                    relation = "controls" if entry.kind == "StorageDevice" else "provides"
                    self._edge(data["controllerRef"], entry.id, relation)
                for device in data.get("deviceRefs", []):
                    self._edge(entry.id, device, "uses_device")
            elif entry.kind == "Accelerator":
                self._edge(entry.parent, entry.id, "has_accelerator")
            elif entry.kind in {"PartitionTable", "PhysicalVolume", "VolumeGroup", "LogicalVolume"}:
                self._edge(entry.parent, entry.id, "configures")
                if "sourceRef" in data:
                    self._edge(entry.id, data["sourceRef"], "uses_block_device")
                for pv in data.get("physicalVolumeRefs", []):
                    self._edge(entry.id, pv, "uses_pv")
                if "volumeGroupRef" in data:
                    self._edge(entry.id, data["volumeGroupRef"], "allocated_from")
            elif entry.kind == "Partition":
                self._edge(entry.parent, entry.id, "has_partition")
            elif entry.kind == "DeviceRequirement":
                self._edge(entry.parent, entry.id, "requires_device")

    def _edge(self, source: str, target: str, relation: str, key: str | None = None, **attributes: Any) -> None:
        self._graph.add_edge(source, target, key=key or relation, relation=relation, **attributes)

    def __len__(self) -> int:
        return self._graph.number_of_nodes()

    def __contains__(self, object_id: str) -> bool:
        return object_id in self._graph

    def nodes(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [deepcopy(data) for _, data in self._graph.nodes(data=True)
                if kind is None or data["kind"] == kind]

    def edges(self, relation: str | None = None) -> list[dict[str, Any]]:
        return [deepcopy({"source": source, "target": target, "key": key, **data})
                for source, target, key, data in self._graph.edges(keys=True, data=True)
                if relation is None or data["relation"] == relation]

    def neighbors(self, object_id: str, relation: str | None = None,
                  direction: Literal["out", "in", "both"] = "out") -> list[str]:
        if object_id not in self._graph:
            raise KeyError(object_id)
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be 'out', 'in', or 'both'")
        neighbors = set()
        if direction in {"out", "both"}:
            for _, target, data in self._graph.out_edges(object_id, data=True):
                if relation is None or data["relation"] == relation:
                    neighbors.add(target)
        if direction in {"in", "both"}:
            for source, _, data in self._graph.in_edges(object_id, data=True):
                if relation is None or data["relation"] == relation:
                    neighbors.add(source)
        return sorted(neighbors)
