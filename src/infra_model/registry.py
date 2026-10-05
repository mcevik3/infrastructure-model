"""Canonical identities and containment, independent of graph libraries."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .errors import Issue, ValidationError
from .loader import SourceDocument
from .schema import KIND_PATHS


@dataclass(frozen=True)
class Entry:
    data: dict[str, Any]
    source: str
    path: str
    parent: str | None = None

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def kind(self) -> str:
        return self.data["kind"]


class Registry:
    """Index validated source dictionaries and their embedded semantic entities."""

    def __init__(self, documents: list[SourceDocument]):
        self.entries: dict[str, Entry] = {}
        self.documents: list[dict[str, Any]] = []
        self._issues: list[Issue] = []
        self._siblings: dict[tuple[str, str, str], str] = {}
        for document in documents:
            data = deepcopy(document.data)
            object_id = f"{KIND_PATHS[data['kind']]}/{data['metadata']['name']}"
            if object_id in self.entries:
                previous = self.entries[object_id]
                self._issues.append(Issue("duplicate_name", f"{object_id} also defined in {previous.source}", document.source, "/metadata/name"))
                continue
            data["id"] = object_id
            entry = Entry(data, document.source, "")
            self.entries[object_id] = entry
            self.documents.append(data)
            spec = data["spec"]
            if entry.kind == "Server":
                for adapter in self._children(entry, spec, "/spec", "networkAdapters", "network-adapter", "NetworkAdapter"):
                    self._children(adapter, adapter.data, adapter.path, "interfaces", "interface", "Interface")
                self._children(entry, spec, "/spec", "storageDevices", "storage-device", "StorageDevice")
            elif entry.kind == "NetworkDevice":
                self._children(entry, spec, "/spec", "interfaces", "interface", "Interface")
            elif entry.kind == "Cluster":
                for node in self._children(entry, spec, "/spec", "nodes", "node", "ClusterNode"):
                    self._children(node, node.data, node.path, "networkAttachments", "network-attachment", "NetworkAttachment")
        if self._issues:
            raise ValidationError(self._issues)

    def _children(self, parent: Entry, container: dict, path: str, field: str, segment: str, kind: str) -> list[Entry]:
        children = []
        for index, data in enumerate(container.get(field, [])):
            child_path = f"{path}/{field}/{index}"
            sibling = (parent.id, field, data["name"])
            if sibling in self._siblings:
                self._issues.append(Issue("duplicate_component_name", f"name {data['name']!r} repeats in {parent.id}/{field}", parent.source, child_path + "/name"))
                continue
            object_id = f"{parent.id}/{segment}/{data['name']}"
            self._siblings[sibling] = object_id
            data.update(id=object_id, kind=kind)
            entry = Entry(data, parent.source, child_path, parent.id)
            self.entries[object_id] = entry
            children.append(entry)
        return children

    def of_kind(self, kind: str) -> list[Entry]:
        return [entry for entry in self.entries.values() if entry.kind == kind]

    def owner(self, entry: Entry) -> Entry:
        while entry.parent is not None:
            entry = self.entries[entry.parent]
        return entry

    def node_site(self, node: Entry) -> str | None:
        return node.data.get("placement", {}).get("siteRef") or self.entries[node.parent].data["spec"].get("siteRef")

    def effective_capabilities(self, resource: Entry) -> dict:
        spec = resource.data["spec"]
        profile = self.entries.get(spec.get("profileRef"))
        capabilities = deepcopy(profile.data["spec"].get("capabilities", {})) if profile else {}
        return _overlay(capabilities, spec.get("capabilities", {}))


def _overlay(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _overlay(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base
