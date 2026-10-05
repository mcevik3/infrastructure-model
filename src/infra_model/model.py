"""The public model facade; domain values are normalized dictionaries."""

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from .errors import LoadError, ValidationReport
from .graph import Topology
from .loader import SourceDocument, _json_copy, load_documents
from .registry import Registry
from .schema import validate_documents
from .validator import validate_registry


class InfrastructureModel:
    """Construct with load() or from_documents(); SourceDocument plumbing is internal."""

    def __init__(self, documents: list[SourceDocument]):
        """Internal construction from loader records; use the public classmethods."""
        if not documents:
            raise LoadError("no model documents supplied")
        self._documents = deepcopy(documents)
        self._registry: Registry | None = None
        self._topology: Topology | None = None
        self._report: ValidationReport | None = None

    @classmethod
    def load(cls, path: str | Path) -> "InfrastructureModel":
        """Parse YAML now; validate explicitly or on the first query."""
        return cls(load_documents(path))

    @classmethod
    def from_documents(cls, documents: Iterable[dict[str, Any]]) -> "InfrastructureModel":
        """Load JSON-compatible source dictionaries without YAML/filesystem IO."""
        sources = []
        for index, document in enumerate(documents, 1):
            if not isinstance(document, dict):
                raise LoadError(f"memory#document={index}: document must be a mapping")
            try:
                sources.append(SourceDocument(_json_copy(document), f"memory#document={index}"))
            except (ValueError, RecursionError) as exc:
                raise LoadError(f"memory#document={index}: {exc}") from exc
        return cls(sources)

    def validate(self) -> ValidationReport:
        """Run schema, identity and semantic checks; raise ValidationError on failure."""
        if self._report is None:
            validate_documents(self._documents)
            registry = Registry(self._documents)
            warnings = validate_registry(registry)
            self._registry = registry
            self._report = ValidationReport(len(registry.documents), len(registry.entries), warnings)
        return self._report

    def _validated_registry(self) -> Registry:
        self.validate()
        assert self._registry is not None
        return self._registry

    def get(self, object_id: str) -> dict[str, Any]:
        """Return a defensive copy of a top-level or embedded entity."""
        return deepcopy(self._validated_registry().entries[object_id].data)

    def of_kind(self, kind: str) -> list[dict[str, Any]]:
        return [deepcopy(entry.data) for entry in self._validated_registry().of_kind(kind)]

    def servers(self) -> list[dict[str, Any]]:
        return self.of_kind("Server")

    def networks(self) -> list[dict[str, Any]]:
        return self.of_kind("Network")

    def effective_capabilities(self, resource_id: str) -> dict[str, Any]:
        registry = self._validated_registry()
        resource = registry.entries[resource_id]
        if resource.kind not in {"Server", "NetworkDevice"}:
            raise ValueError("effective capabilities require a Server or NetworkDevice")
        return registry.effective_capabilities(resource)

    @property
    def documents(self) -> list[dict[str, Any]]:
        return deepcopy(self._validated_registry().documents)

    @property
    def topology(self) -> Topology:
        if self._topology is None:
            self._topology = Topology(self._validated_registry())
        return self._topology
