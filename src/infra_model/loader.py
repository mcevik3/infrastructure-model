"""Load YAML safely without applying inventory or deployment semantics."""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .errors import LoadError


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "mapping keys must be strings", key_node.start_mark,
                )
            if key in result:
                raise ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate key {key!r}", key_node.start_mark,
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


# Keep dates as source strings, including in status and extensions. Never alter
# PyYAML's global resolver table.
_UniqueKeyLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in values
          if tag != "tag:yaml.org,2002:timestamp"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


@dataclass(frozen=True)
class SourceDocument:
    data: dict[str, Any]
    source: str


def _json_copy(value: Any, active: set[int] | None = None) -> Any:
    """Copy aliases and reject cycles/non-JSON YAML values before validation."""
    active = set() if active is None else active
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not supported")
        return value
    if not isinstance(value, (dict, list)):
        raise ValueError(f"unsupported YAML value {type(value).__name__}")
    if id(value) in active:
        raise ValueError("recursive YAML aliases are not supported")
    active.add(id(value))
    try:
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise ValueError("mapping keys must be strings")
            return {key: _json_copy(item, active) for key, item in value.items()}
        return [_json_copy(item, active) for item in value]
    finally:
        active.remove(id(value))


def load_documents(path: str | Path) -> list[SourceDocument]:
    """Load one file or all .yaml/.yml files recursively, in sorted order.

    Empty YAML documents are ignored. Duplicate keys, unsafe tags, non-mapping
    documents and directories containing no model documents are errors.
    """
    root = Path(path)
    try:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = sorted(p for p in root.rglob("*")
                           if p.is_file() and p.suffix.lower() in {".yaml", ".yml"})
        else:
            raise LoadError(f"{root}: path does not exist or is not a file/directory")
        documents = []
        for file in files:
            source = f"{file}#document=1"
            try:
                with file.open(encoding="utf-8") as stream:
                    for number, data in enumerate(yaml.load_all(stream, Loader=_UniqueKeyLoader), 1):
                        source = f"{file}#document={number}"
                        if data is not None:
                            if not isinstance(data, dict):
                                raise LoadError(f"{source}: document must be a mapping")
                            documents.append(SourceDocument(_json_copy(data), source))
                        # A parser failure on the next iteration belongs to the
                        # next document, not the last successfully loaded one.
                        source = f"{file}#document={number + 1}"
            except (yaml.YAMLError, ValueError, RecursionError) as exc:
                raise LoadError(f"{source}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise LoadError(f"{root}: {exc}") from exc
    if not documents:
        raise LoadError(f"{root}: no YAML model documents found")
    return documents
