"""Draft 2020-12 JSON Schema validation, using only bundled local resources."""

from datetime import datetime
from functools import lru_cache
from importlib.resources import files
import ipaddress
import json
import re

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry as SchemaRegistry, Resource

from .errors import Issue, ValidationError
from .loader import SourceDocument


KIND_PATHS = {
    "Site": "site", "HardwareProfile": "hardware-profile", "Server": "server",
    "NetworkDevice": "network-device", "Network": "network", "Link": "link",
    "Cluster": "cluster",
}
API_VERSION = "infra.model/v0alpha1"
_FORMATS = FormatChecker()


@_FORMATS.checks("date-time", raises=ValueError)
def _date_time(value: object) -> bool:
    # jsonschema's optional RFC 3339 dependency is not required by this package.
    # Validate timezone-qualified observation times consistently offline.
    if not isinstance(value, str):
        return True
    pattern = (r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
               r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-2][0-9]:[0-5][0-9])")
    return (re.fullmatch(pattern, value) is not None
            and bool(datetime.fromisoformat(value.upper())))


@_FORMATS.checks("ip-address", raises=ValueError)
def _ip_address(value: object) -> bool:
    if not isinstance(value, str):
        return True  # The schema's type constraint handles this.
    return "%" not in value and bool(ipaddress.ip_address(value))


@_FORMATS.checks("ip-prefix", raises=ValueError)
def _ip_prefix(value: object) -> bool:
    if not isinstance(value, str):
        return True
    return (re.fullmatch(r"[^/]+/(?:0|[1-9][0-9]*)", value) is not None
            and "%" not in value and bool(ipaddress.ip_network(value, strict=True)))


def json_pointer(parts) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    bundle = files("infra_model._schemas")
    schemas = {
        name: json.loads(bundle.joinpath(name + ".json").read_text(encoding="utf-8"))
        for name in ["common", *KIND_PATHS.values()]
    }
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    registry = SchemaRegistry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return {
        kind: Draft202012Validator(schemas[name], registry=registry, format_checker=_FORMATS)
        for kind, name in KIND_PATHS.items()
    }


def validate_documents(documents: list[SourceDocument]) -> None:
    """Reject all structural errors before constructing a semantic registry."""
    validators = _validators()
    issues = []
    for document in documents:
        kind = document.data.get("kind")
        if not isinstance(kind, str) or kind not in validators:
            issues.append(Issue("schema", f"unsupported document kind {kind!r}", document.source, "/kind"))
            continue
        errors = sorted(validators[kind].iter_errors(document.data), key=lambda e: json_pointer(e.absolute_path))
        for error in errors:
            issues.append(Issue("schema", error.message, document.source, json_pointer(error.absolute_path)))
    if issues:
        raise ValidationError(issues)
