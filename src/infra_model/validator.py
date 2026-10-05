"""Provider-neutral semantic validation over normalized dictionaries."""

from enum import Enum
from fractions import Fraction
import ipaddress

from .errors import Issue, ValidationError
from .registry import Entry, Registry


_UNIT_FACTORS = {
    "B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
    "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
    "Hz": 1, "kHz": 1000, "MHz": 1000**2, "GHz": 1000**3,
    "core": 1, "thread": 1,
}


def _quantity(quantity: dict) -> Fraction:
    """Scale exactly, without Decimal context or converting large ints to text.

    Non-integer JSON numbers use their decimal spelling, as in the source
    model's existing numeric convention. Parsing precision is unchanged.
    """
    value = quantity["value"]
    return Fraction(value if isinstance(value, int) else str(value)) * _UNIT_FACTORS[quantity["unit"]]


class _Compatibility(Enum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    UNKNOWN = "UNKNOWN"


def _compare(required, actual) -> _Compatibility:
    """Known capability proves a result; absent capability proves neither."""
    if actual is None:
        return _Compatibility.UNKNOWN
    satisfied = _quantity(actual) >= _quantity(required) if isinstance(required, dict) else actual == required
    return _Compatibility.SATISFIED if satisfied else _Compatibility.UNSATISFIED


def _compare_cpu(required: dict, compute: dict, realization: str) -> _Compatibility:
    # v0alpha1 has no generic VM allocation or overcommit evidence. In
    # particular, physical cores/threads are never evidence of vCPU capacity.
    if realization != "baremetal" or required["unit"] == "vcpu":
        return _Compatibility.UNKNOWN
    field = {"core": "cores", "thread": "threads"}[required["unit"]]
    return _compare({"value": required["count"], "unit": required["unit"]}, compute.get(field))


class SemanticValidator:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.issues: list[Issue] = []
        self.warnings: list[Issue] = []

    def _issue(self, entry: Entry, code: str, message: str, path: str = "", *, warning: bool = False) -> None:
        target = self.warnings if warning else self.issues
        target.append(Issue(code, message, entry.source, entry.path + path))

    def _resolve(self, entry: Entry, reference: str, kind: str, path: str) -> Entry | None:
        target = self.registry.entries.get(reference)
        if target is None:
            self._issue(entry, "unresolved_reference", f"{reference!r} does not resolve to {kind}", path)
        elif target.kind != kind:
            self._issue(entry, "reference_kind", f"{reference!r} must refer to {kind}, found {target.kind}", path)
        else:
            return target
        return None

    def validate(self) -> tuple[Issue, ...]:
        macs: dict[str, str] = {}
        pci_addresses: dict[tuple[str, str], str] = {}
        for entry in self.registry.entries.values():
            data = entry.data
            fields = data["spec"] if entry.parent is None else data
            field_path = "/spec" if entry.parent is None else ""
            for field, kind in [("siteRef", "Site"), ("profileRef", "HardwareProfile")]:
                if field in fields:
                    self._resolve(entry, fields[field], kind, f"{field_path}/{field}")
            if entry.kind == "Interface" and "macAddress" in data:
                mac = data["macAddress"].lower()
                if mac in macs:
                    self._issue(entry, "duplicate_mac", f"MAC {mac} is also used by {macs[mac]}", "/macAddress")
                else:
                    macs[mac] = entry.id
            if "pciAddress" in data:
                pci = data["pciAddress"].lower()
                if pci.count(":") == 1:
                    pci = "0000:" + pci
                owner = self.registry.owner(entry).id
                key = (owner, pci)
                if key in pci_addresses:
                    self._issue(entry, "duplicate_pci", f"PCI {pci} is also used by {pci_addresses[key]} within {owner}", "/pciAddress")
                else:
                    pci_addresses[key] = entry.id
            if entry.kind == "Link":
                endpoints = fields["endpoints"]
                # Also enforce the invariant at the semantic boundary.
                if len(endpoints) != 2 or endpoints[0] == endpoints[1]:
                    self._issue(entry, "link_endpoints", "Link requires exactly two distinct interfaces", "/spec/endpoints")
                for index, endpoint in enumerate(endpoints):
                    self._resolve(entry, endpoint, "Interface", f"/spec/endpoints/{index}")
            elif entry.kind == "NetworkAttachment":
                self._attachment(entry)
            elif entry.kind == "ClusterNode":
                placement = data.get("placement", {})
                if "siteRef" in placement:
                    self._resolve(entry, placement["siteRef"], "Site", "/placement/siteRef")
                if "resourceRef" in placement:
                    if data["realization"]["type"] != "baremetal":
                        self._issue(entry, "invalid_placement", "placement.resourceRef selects an existing Server only for baremetal realization", "/placement/resourceRef")
                        continue
                    resource = self._resolve(entry, placement["resourceRef"], "Server", "/placement/resourceRef")
                    if resource:
                        self._placement(entry, resource)
                else:
                    self._result(entry, _Compatibility.UNKNOWN,
                                 "no exact inventory resource selected; site-only or unplaced intent does not prove compatibility",
                                 "/realization")
        if self.issues:
            raise ValidationError(self.issues)
        return tuple(self.warnings)

    def _attachment(self, entry: Entry) -> None:
        network = self._resolve(entry, entry.data["networkRef"], "Network", "/networkRef")
        if network is None:
            return
        prefixes = [ipaddress.ip_network(prefix) for prefix in network.data["spec"].get("prefixes", [])]
        for index, value in enumerate(entry.data.get("addresses", [])):
            address = ipaddress.ip_address(value)
            if not any(address.version == prefix.version and address in prefix for prefix in prefixes):
                self._issue(entry, "address_outside_prefix", f"{value} is not within any prefix of {network.id}", f"/addresses/{index}")

    def _placement(self, node: Entry, resource: Entry) -> None:
        site = self.registry.node_site(node)
        if site and site != resource.data["spec"]["siteRef"]:
            self._result(node, _Compatibility.UNSATISFIED, f"{resource.id} is at {resource.data['spec']['siteRef']}, requested {site}", "/placement/resourceRef")
        capabilities = self.registry.effective_capabilities(resource)
        node_types = capabilities.get("nodeTypes")
        realization = node.data["realization"]["type"]
        result = (_Compatibility.UNKNOWN if node_types is None else
                  _Compatibility.SATISFIED if realization in node_types else _Compatibility.UNSATISFIED)
        self._result(node, result, f"{resource.id} declares nodeTypes={node_types!r}; requires {realization}", "/realization/type")
        for group, fields in node.data.get("requirements", {}).items():
            if group == "extensions":
                if fields:
                    self._result(node, _Compatibility.UNKNOWN, "extension requirements have no generic evaluator", "/requirements/extensions")
                continue
            available = capabilities.get(group, {})
            for field, required in fields.items():
                path = f"/requirements/{group}/{field}"
                if field == "extensions":
                    if required:
                        self._result(node, _Compatibility.UNKNOWN, "extension requirements have no generic evaluator", path)
                    continue
                if group == "compute" and field == "cpu":
                    result = _compare_cpu(required, available, realization)
                    detail = "lacks generic evidence for" if result is _Compatibility.UNKNOWN else "falls below"
                    message = f"{resource.id} compute.cpu {detail} the requested {required['unit']} count"
                else:
                    result = _compare(required, available.get(field))
                    detail = "has no declared" if result is _Compatibility.UNKNOWN else "does not meet the required"
                    message = f"{resource.id} {detail} {group}.{field} capability"
                self._result(node, result, message, path)

    def _result(self, entry: Entry, result: _Compatibility, message: str, path: str) -> None:
        if result is _Compatibility.SATISFIED:
            return
        unknown = result is _Compatibility.UNKNOWN
        self._issue(entry, "unevaluated_requirement" if unknown else "incompatible_resource",
                    f"{result.value}: {message}", path, warning=unknown)


def validate_registry(registry: Registry) -> tuple[Issue, ...]:
    return SemanticValidator(registry).validate()
