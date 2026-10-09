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
    "bps": 1, "Kbps": 1000, "Mbps": 1000**2, "Gbps": 1000**3, "Tbps": 1000**4,
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
    actual = compute.get("cpu", {}).get(field)
    if actual is None:
        return _Compatibility.UNKNOWN
    return _Compatibility.SATISFIED if actual >= required["count"] else _Compatibility.UNSATISFIED


def _all_requirements(results: list[_Compatibility]) -> _Compatibility:
    """A known mismatch rules out a candidate even if other fields are unknown."""
    if _Compatibility.UNSATISFIED in results:
        return _Compatibility.UNSATISFIED
    if _Compatibility.UNKNOWN in results:
        return _Compatibility.UNKNOWN
    return _Compatibility.SATISFIED


def _compare_features(required: list[str], actual: list[str] | None) -> _Compatibility:
    if not required:
        return _Compatibility.SATISFIED
    if actual is None:
        return _Compatibility.UNKNOWN
    return (_Compatibility.SATISFIED if set(required) <= set(actual)
            else _Compatibility.UNSATISFIED)


def _matching_count(results: list[_Compatibility], count: int) -> _Compatibility:
    """Count distinct candidates within one request; never reserve inventory."""
    known = results.count(_Compatibility.SATISFIED)
    if known >= count:
        return _Compatibility.SATISFIED
    if known + results.count(_Compatibility.UNKNOWN) >= count:
        return _Compatibility.UNKNOWN
    return _Compatibility.UNSATISFIED


def _device_match(required: dict, actual: dict) -> _Compatibility:
    results = []
    if required["type"] == "dpu":
        results.append(_compare("dpu", actual.get("class")))
    elif required["type"] in {"gpu", "fpga"}:
        results.append(_compare(required["type"], actual.get("type")))
    for field, value in required.get("constraints", {}).items():
        inventory_field = "capacity" if field == "minCapacity" else field
        evidence = actual.get("capabilities", {}) if field == "features" and required["type"] in {"gpu", "fpga"} else actual
        compare = _compare_features if field == "features" else _compare
        results.append(compare(value, evidence.get(inventory_field)))
    if required.get("extensions"):
        results.append(_Compatibility.UNKNOWN)
    return _all_requirements(results)


def _interface_match(required: dict, interface: dict, adapter: dict) -> _Compatibility:
    capabilities = interface.get("capabilities", {})
    results = [_compare(required["type"], interface.get("type"))]
    if "minSpeed" in required:
        results.append(_compare(required["minSpeed"], capabilities.get("bandwidth")))
    if "features" in required:
        results.append(_compare_features(required["features"], capabilities.get("features")))
    for field, value in required.get("adapter", {}).items():
        results.append(_compare(value, adapter.get(field)))
    return _all_requirements(results)


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
            elif entry.kind == "Network":
                self._network(entry)
            elif entry.kind == "ClusterNode":
                self._storage_configuration(entry)
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
            elif entry.kind in {"Server", "HardwareProfile", "NetworkDevice"}:
                self._cpu_inventory(entry)
            elif entry.kind in {"StorageDevice", "StorageVolume"}:
                self._storage_hardware(entry)
        if self.issues:
            raise ValidationError(self.issues)
        return tuple(self.warnings)

    def _cpu_inventory(self, entry: Entry) -> None:
        spec = entry.data["spec"]
        capabilities = (spec.get("capabilities", {}) if entry.kind == "HardwareProfile"
                        else self.registry.effective_capabilities(entry))
        cpu = capabilities.get("compute", {}).get("cpu", {})
        path = "/spec/capabilities/compute/cpu"
        topology = cpu.get("topology", {})
        for aggregate, count, per in [("cores", "sockets", "coresPerSocket"),
                                       ("threads", "cores", "threadsPerCore")]:
            if aggregate in cpu and count in cpu and per in topology:
                if cpu[count] * topology[per] != cpu[aggregate]:
                    self._issue(entry, "cpu_topology", f"{count} * {per} must equal {aggregate} in effective CPU inventory", path + "/topology/" + per)
        smt = cpu.get("smt", {})
        if smt.get("enabled") is True:
            result = (_Compatibility.UNKNOWN if "supported" not in smt else
                      _Compatibility.SATISFIED if smt["supported"] else _Compatibility.UNSATISFIED)
            self._result(entry, result, "SMT enabled requires SMT support", path + "/smt/enabled")
        numa = cpu.get("numa", {})
        selected = numa.get("nodesPerSocket")
        supported = numa.get("supportedNodesPerSocket")
        if selected is not None:
            result = (_Compatibility.UNKNOWN if supported is None else
                      _Compatibility.SATISFIED if selected in supported else _Compatibility.UNSATISFIED)
            self._result(entry, result, "NUMA nodesPerSocket must be supported by the platform", path + "/numa/nodesPerSocket")
        # Instance values override defaults, but cannot erase explicit profile
        # support restrictions. Check these against the original profile too.
        profile = self.registry.entries.get(spec.get("profileRef"))
        if profile and profile.kind == "HardwareProfile":
            profile_cpu = profile.data["spec"]["capabilities"].get("compute", {}).get("cpu", {})
            if profile_cpu.get("smt", {}).get("supported") is False and smt.get("supported") is True:
                self._result(entry, _Compatibility.UNSATISFIED, "Server SMT support contradicts HardwareProfile", path + "/smt/supported")
            profile_nodes = profile_cpu.get("numa", {}).get("supportedNodesPerSocket")
            if profile_nodes is not None and supported is not None and any(n not in profile_nodes for n in supported):
                self._result(entry, _Compatibility.UNSATISFIED, "Server NUMA support contradicts HardwareProfile", path + "/numa/supportedNodesPerSocket")
            if profile_nodes is not None and selected is not None and selected not in profile_nodes and supported != profile_nodes:
                self._result(entry, _Compatibility.UNSATISFIED, "NUMA configuration contradicts HardwareProfile support", path + "/numa/nodesPerSocket")
        nodes = numa.get("nodes")
        if nodes is None:
            return
        ids = set()
        sockets = cpu.get("sockets")
        for index, node in enumerate(nodes):
            node_path = f"{path}/numa/nodes/{index}"
            if node["id"] in ids:
                self._issue(entry, "duplicate_numa_id", "NUMA node IDs must be unique within the Server", node_path + "/id")
            ids.add(node["id"])
            if sockets is None:
                self._result(entry, _Compatibility.UNKNOWN, "socket count is unknown; cannot verify NUMA socket index", node_path + "/socket")
            elif node["socket"] >= sockets:
                self._issue(entry, "numa_socket", "NUMA socket index must be less than physical socket count", node_path + "/socket")
        if numa.get("mode") == "numa" and sockets is not None and selected is not None:
            if len(nodes) != sockets * selected:
                self._issue(entry, "numa_node_count", "complete NUMA node count must equal sockets * nodesPerSocket", path + "/numa/nodes")
        if "cores" in cpu and all("cores" in node for node in nodes):
            if sum(node["cores"] for node in nodes) != cpu["cores"]:
                self._issue(entry, "numa_core_count", "NUMA node cores must sum to aggregate physical cores", path + "/numa/nodes")

    def _same_parent(self, entry: Entry, reference: str, kind: str, path: str) -> Entry | None:
        target = self._resolve(entry, reference, kind, path)
        if target and target.parent != entry.parent:
            self._issue(entry, "reference_scope", f"{reference!r} must belong to {entry.parent}", path)
            return None
        return target

    def _unique_refs(self, entry: Entry, field: str) -> None:
        seen = set()
        for index, reference in enumerate(entry.data.get(field, [])):
            if reference in seen:
                self._issue(entry, "duplicate_reference", f"duplicate reference {reference!r}", f"/{field}/{index}")
            seen.add(reference)

    def _storage_hardware(self, entry: Entry) -> None:
        data = entry.data
        if "controllerRef" in data:
            self._same_parent(entry, data["controllerRef"], "StorageController", "/controllerRef")
        if entry.kind != "StorageVolume":
            return
        self._unique_refs(entry, "deviceRefs")
        for index, reference in enumerate(data.get("deviceRefs", [])):
            path = f"/deviceRefs/{index}"
            device = self._same_parent(entry, reference, "StorageDevice", path)
            if device:
                controller = device.data.get("controllerRef")
                if controller is None:
                    self._result(entry, _Compatibility.UNKNOWN, f"controller of {reference} is not recorded", path)
                elif controller != data["controllerRef"]:
                    self._issue(entry, "storage_controller_conflict", "volume and member device must use the same controller", path)

    def _block_source(self, entry: Entry, node: Entry) -> Entry | None:
        reference = entry.data["sourceRef"]
        target = self.registry.entries.get(reference)
        kinds = {"StorageDevice", "StorageVolume"}
        if entry.kind == "PhysicalVolume":
            kinds.add("Partition")
        if target is None:
            self._issue(entry, "unresolved_reference", f"{reference!r} does not resolve to a block-device source", "/sourceRef")
            return None
        if target.kind not in kinds:
            self._issue(entry, "reference_kind", f"{reference!r} is not a supported block-device source", "/sourceRef")
            return None
        if target.kind == "Partition":
            if self.registry.entries[target.parent].parent != node.id:
                self._issue(entry, "reference_scope", "partition source must belong to this ClusterNode", "/sourceRef")
        else:
            selected = node.data.get("placement", {}).get("resourceRef")
            if selected is None:
                self._result(entry, _Compatibility.UNKNOWN, "no exact Server placement; block-device availability cannot be proven", "/sourceRef")
            elif target.parent != selected:
                self._issue(entry, "storage_placement", f"{reference!r} does not belong to selected Server {selected}", "/sourceRef")
        return target

    def _storage_configuration(self, node: Entry) -> None:
        storage = node.data.get("configuration", {}).get("storage")
        if storage is None:
            return
        if node.data["realization"]["type"] != "baremetal":
            self._issue(node, "invalid_storage_configuration", "physical block-device configuration requires baremetal realization", "/configuration/storage")
        for table_data in storage.get("partitionTables", []):
            table = self.registry.entries[table_data["id"]]
            self._block_source(table, node)
            numbers = set()
            growing = False
            for index, partition in enumerate(table_data["partitions"]):
                if "number" in partition:
                    if partition["number"] in numbers:
                        self._issue(table, "duplicate_partition_number", "partition numbers must be unique within a table", f"/partitions/{index}/number")
                    numbers.add(partition["number"])
                if partition.get("grow") is True:
                    if growing:
                        self._issue(table, "multiple_grow", "at most one grow partition per table", f"/partitions/{index}/grow")
                    growing = True
        lvm = storage.get("lvm", {})
        sources = set()
        for data in lvm.get("physicalVolumes", []):
            pv = self.registry.entries[data["id"]]
            self._block_source(pv, node)
            if data["sourceRef"] in sources:
                self._issue(pv, "duplicate_pv_source", "a block-device source may be assigned to only one PV per node", "/sourceRef")
            sources.add(data["sourceRef"])
        assigned = {}
        for data in lvm.get("volumeGroups", []):
            vg = self.registry.entries[data["id"]]
            self._unique_refs(vg, "physicalVolumeRefs")
            for index, reference in enumerate(data["physicalVolumeRefs"]):
                path = f"/physicalVolumeRefs/{index}"
                self._same_parent(vg, reference, "PhysicalVolume", path)
                if reference in assigned and assigned[reference] != vg.id:
                    self._issue(vg, "pv_multiple_vgs", f"PV already assigned to {assigned[reference]}", path)
                assigned[reference] = vg.id
        growing = set()
        for data in lvm.get("logicalVolumes", []):
            lv = self.registry.entries[data["id"]]
            reference = data["volumeGroupRef"]
            self._same_parent(lv, reference, "VolumeGroup", "/volumeGroupRef")
            if data.get("grow") is True:
                if reference in growing:
                    self._issue(lv, "multiple_grow", "at most one grow LV per VG", "/grow")
                growing.add(reference)

    def _network(self, entry: Entry) -> None:
        spec = entry.data["spec"]
        prefixes = {ipaddress.ip_network(prefix) for prefix in spec.get("prefixes", [])}
        gateway_prefixes = set()
        for index, gateway in enumerate(spec.get("defaultGateways", [])):
            path = f"/spec/defaultGateways/{index}"
            prefix = ipaddress.ip_network(gateway["prefix"])
            address = ipaddress.ip_address(gateway["address"])
            if prefix not in prefixes:
                self._issue(entry, "gateway_prefix_not_declared",
                            f"{gateway['prefix']} is not declared in this Network's prefixes", path + "/prefix")
            if address.version != prefix.version:
                self._issue(entry, "gateway_address_family",
                            "default gateway address family must match its prefix", path + "/address")
            if prefix in gateway_prefixes:
                self._issue(entry, "duplicate_default_gateway",
                            f"at most one default gateway is allowed for prefix {prefix}", path + "/prefix")
            gateway_prefixes.add(prefix)
            # Gateways need not be on-prefix, including IPv6 link-local gateways.

        servers = set()
        for index, value in enumerate(spec.get("dns", {}).get("servers", [])):
            address = ipaddress.ip_address(value)
            if address in servers:
                self._issue(entry, "duplicate_dns_server", f"DNS server {value} repeats an equivalent address",
                            f"/spec/dns/servers/{index}")
            servers.add(address)

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
            if group == "devices":
                self._devices(fields, resource)
                continue
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
        self._interfaces(node, resource)

    def _devices(self, requirements: list[dict], resource: Entry) -> None:
        spec = resource.data["spec"]
        for required in requirements:
            entry = self.registry.entries[required["id"]]
            if required["type"] == "storage":
                inventory = spec.get("storage", {}).get("devices")
            elif required["type"] == "dpu":
                inventory = spec.get("networkAdapters")
            elif required["type"] in {"gpu", "fpga"}:
                inventory = spec.get("accelerators")
            else:
                # The broad "other" requirement has no generic inventory mapping.
                # Opaque provider observations cannot prove these requirements.
                inventory = None
            count = required.get("count", 1)
            result = (_Compatibility.UNKNOWN if inventory is None else
                      _matching_count([_device_match(required, item) for item in inventory], count))
            self._result(entry, result,
                         f"{resource.id} device inventory must provide the requested count of matching {required['type']} components", "")

    def _interfaces(self, node: Entry, resource: Entry) -> None:
        adapters = resource.data["spec"].get("networkAdapters")
        for attachment in node.data.get("networkAttachments", []):
            required = attachment.get("interfaceRequirements")
            if required is None:
                continue
            results = ([] if adapters is None else
                       [_interface_match(required, interface, adapter)
                        for adapter in adapters for interface in adapter["interfaces"]])
            result = (_Compatibility.UNKNOWN if adapters is None else _matching_count(results, 1))
            self._result(self.registry.entries[attachment["id"]], result,
                         f"{resource.id} must provide an interface meeting the requested type, speed, features, and adapter constraints",
                         "/interfaceRequirements")

    def _result(self, entry: Entry, result: _Compatibility, message: str, path: str) -> None:
        if result is _Compatibility.SATISFIED:
            return
        unknown = result is _Compatibility.UNKNOWN
        self._issue(entry, "unevaluated_requirement" if unknown else "incompatible_resource",
                    f"{result.value}: {message}", path, warning=unknown)


def validate_registry(registry: Registry) -> tuple[Issue, ...]:
    return SemanticValidator(registry).validate()
