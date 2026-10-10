"""Strict provider identity mappings, with no placement policy or discovery."""

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from jsonschema import Draft202012Validator

from ...errors import LoadError
from ...loader import _json_copy, load_documents
from .errors import FabricTranslationError


_TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
_REFERENCE_DEFINITIONS = json.loads(files("infra_model._schemas").joinpath("common.json").read_text(encoding="utf-8"))["$defs"]
_CONFIG_SCHEMA = {
    "type": "object",
    "required": ["apiVersion", "kind", "sites", "images"],
    "additionalProperties": False,
    "properties": {
        "apiVersion": {"const": "infra.model.fabric/v0alpha1"},
        "kind": {"const": "FabricAdapterConfig"},
        "sites": {
            "type": "object",
            "propertyNames": _REFERENCE_DEFINITIONS["siteRef"],
            "additionalProperties": _TEXT,
        },
        "workers": {
            "type": "object",
            "propertyNames": _REFERENCE_DEFINITIONS["resourceRef"],
            "additionalProperties": _TEXT,
        },
        "images": {
            "type": "array",
            "items": {
                "type": "object", "required": ["name", "version", "fabricImage"],
                "additionalProperties": False,
                "properties": {field: _TEXT for field in ("name", "version", "fabricImage")},
            },
        },
    },
}


@dataclass(frozen=True, init=False)
class FabricAdapterConfig:
    """Immutable, validated mappings. Construct from_file() or from_dict()."""

    sites: Mapping[str, str]
    images: Mapping[tuple[str, str], str]
    workers: Mapping[str, str]

    def __init__(self, document: dict, *, source: str = "fabric-config"):
        try:
            document = _json_copy(document)
        except (ValueError, RecursionError) as exc:
            raise FabricTranslationError(str(exc), source=source) from exc
        error = next(Draft202012Validator(_CONFIG_SCHEMA).iter_errors(document), None)
        if error is not None:
            path = "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path)
            raise FabricTranslationError(error.message, entity="FabricAdapterConfig", source=source, path=path)
        images = {}
        for index, image in enumerate(document["images"]):
            identity = (image["name"], image["version"])
            if identity in images:
                raise FabricTranslationError(f"duplicate logical image mapping {identity!r}",
                                             source=source, path=f"/images/{index}")
            images[identity] = image["fabricImage"]
        object.__setattr__(self, "sites", MappingProxyType(document["sites"]))
        object.__setattr__(self, "images", MappingProxyType(images))
        object.__setattr__(self, "workers", MappingProxyType(document.get("workers", {})))

    @classmethod
    def from_dict(cls, document: dict) -> "FabricAdapterConfig":
        return cls(document)

    @classmethod
    def from_file(cls, path: str | Path) -> "FabricAdapterConfig":
        try:
            if not Path(path).is_file():
                raise LoadError(f"{path}: expected one adapter configuration file")
            documents = load_documents(path)
        except LoadError as exc:
            raise FabricTranslationError(str(exc), entity="FabricAdapterConfig") from exc
        if len(documents) != 1:
            raise FabricTranslationError("expected exactly one adapter configuration document", source=str(path))
        return cls(documents[0].data, source=documents[0].source)
