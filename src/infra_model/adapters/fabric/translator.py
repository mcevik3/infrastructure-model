"""Offline provider reasoning and representability checks for one Cluster."""

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import ipaddress

from ...errors import InfrastructureError, ValidationError
from ...model import InfrastructureModel
from ...registry import Entry, Registry
from .config import FabricAdapterConfig
from .errors import FabricTranslationError
from .plan import (
    _FabricAddressPlan, _FabricDevicePlan, _FabricGatewayPlan, _FabricInterfacePlan,
    _FabricNetworkComponentPlan, _FabricNetworkPlan, _FabricNodePlan,
    _FabricPrefixPlan, _FabricRenderPlan,
)


_BYTE_FACTORS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
                 "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
_ROLES = {"openstack.control", "openstack.network", "openstack.compute", "openstack.storage"}
# Compatibility context: fabric-generic-cluster 1.0.20, FABlib 1.9.5, FIM 1.9.0.
# These are exact mappings; product aliases must be listed explicitly.
_DEVICE_MODELS = {
    "gpu": {"Tesla T4": "GPU_TeslaT4", "RTX6000": "GPU_RTX6000", "A30": "GPU_A30", "A40": "GPU_A40"},
    "fpga": {"U280": "FPGA_Xilinx_U280", "Alveo U280": "FPGA_Xilinx_U280", "SN1022": "FPGA_Xilinx_SN1022"},
    "storage": {"P4510": "NVME_P4510"},
    "dpu": {"BlueField-2-ConnectX-6": "NIC_BlueField_2_ConnectX_6",
            "ConnectX-7-100": "NIC_ConnectX_7_100", "ConnectX-7-400": "NIC_ConnectX_7_400"},
}
_DEVICE_VENDORS = {"gpu": {"NVIDIA"}, "fpga": {"AMD", "Xilinx"}, "storage": {"Intel"}, "dpu": {"NVIDIA"}}
_DEDICATED_NIC_MODELS = {"ConnectX-5": "NIC_ConnectX_5", "ConnectX-6": "NIC_ConnectX_6"}


@dataclass(frozen=True)
class _ComponentRealization:
    model: str
    members: tuple[tuple[int, Entry], ...]


@dataclass(frozen=True)
class _EndpointRealization:
    attachment: Entry
    site: str
    component: _ComponentRealization


def _fail(entry: Entry, path: str, reason: str):
    raise FabricTranslationError(reason, entity=entry.id, source=entry.source, path=entry.path + path)


def _gib(node: Entry, group: str) -> int:
    path = f"/requirements/{group}/capacity"
    quantity = node.data.get("requirements", {}).get(group, {}).get("capacity")
    if quantity is None:
        _fail(node, path, f"explicit {group} capacity is required; no capacity is invented")
    value = quantity["value"]
    amount = Fraction(value if isinstance(value, int) else str(value)) * _BYTE_FACTORS[quantity["unit"]] / 1024**3
    if amount.denominator != 1:
        _fail(node, path, f"{group} capacity must convert exactly to integral GiB; rounding is unsupported")
    return amount.numerator


def _node_capacity(node: Entry) -> tuple[int, int, int]:
    requirements = node.data.get("requirements", {})
    compute = requirements.get("compute", {})
    if "architecture" in compute and compute["architecture"] != "x86_64":
        _fail(node, "/requirements/compute/architecture", "only x86_64 architecture is supported by the v0.1 FABRIC target")
    if "frequency" in compute:
        _fail(node, "/requirements/compute/frequency", "explicit frequency constraints cannot be represented by this target")
    for field in ("medium", "protocol"):
        if field in requirements.get("storage", {}):
            _fail(node, f"/requirements/storage/{field}", f"storage {field} constraints cannot be represented by this target")
    cpu = compute.get("cpu")
    if cpu is None:
        _fail(node, "/requirements/compute/cpu", "explicit vcpu capacity is required; no capacity is invented")
    if cpu["unit"] != "vcpu":
        _fail(node, "/requirements/compute/cpu/unit", "FABRIC VM CPU capacity requires unit vcpu")
    return int(cpu["count"]), _gib(node, "memory"), _gib(node, "storage")


def _device_model(entry: Entry) -> str:
    required = entry.data
    kind = required["type"]
    if kind not in _DEVICE_MODELS:
        _fail(entry, "/type", f"{kind} device requirements remain unsupported")
    constraints = required.get("constraints", {})
    if constraints.get("features"):
        _fail(entry, "/constraints/features", "device feature requirements are unsupported; model identity does not prove requested capabilities")
    if "minCapacity" in constraints:
        _fail(entry, "/constraints/minCapacity", "device minCapacity requirements are unsupported; no static capacity resolver is implemented")
    if kind == "storage" and constraints.get("protocol") != "nvme":
        _fail(entry, "/constraints/protocol", "storage device requirements require explicit protocol nvme")
    model = constraints.get("model")
    if model not in _DEVICE_MODELS[kind]:
        _fail(entry, "/constraints/model", f"{kind} device requirements need an exact supported model; received {model!r}")
    if "vendor" in constraints and constraints["vendor"] not in _DEVICE_VENDORS[kind]:
        _fail(entry, "/constraints/vendor", f"vendor {constraints['vendor']!r} is incompatible with supported {kind} model {model!r}")
    return _DEVICE_MODELS[kind][model]


def _devices(node: Entry, registry: Registry) -> dict[str, tuple[_FabricDevicePlan, ...]]:
    families: dict[str, list[_FabricDevicePlan]] = {"gpu": [], "fpga": [], "nvme": [], "dpu": []}
    for required in node.data.get("requirements", {}).get("devices", []):
        model = _device_model(registry.entries[required["id"]])
        family = "nvme" if required["type"] == "storage" else required["type"]
        devices = families[family]
        for _ in range(required.get("count", 1)):
            devices.append(_FabricDevicePlan(f"{family}{len(devices) + 1}", model))
    return {family: tuple(devices) for family, devices in families.items()}


def _fabnet(entry: Entry, endpoints: list[_EndpointRealization], selected_cluster: str,
            global_endpoint_count: int) -> _FabricNetworkPlan:
    spec = entry.data["spec"]
    for field, supported in (("addressFamily", {"ipv4", "ipv6"}),
                             ("addressAllocation", {"provider-managed"}),
                             ("reachability", {"provider-internal", "public"})):
        if spec.get(field) not in supported:
            _fail(entry, f"/spec/{field}",
                  f"FABNet requires explicit {field} in {sorted(supported)!r}; partial Layer-3 intent is unsupported")
    if spec.get("connectivity", "multipoint") != "multipoint":
        _fail(entry, "/spec/connectivity", "FABNet requires multipoint connectivity; Layer-3 point-to-point is unsupported")
    if len(endpoints) != global_endpoint_count:
        _fail(entry, "/spec",
              f"selected Cluster {selected_cluster} does not contain all FABNet endpoints: "
              f"renders {len(endpoints)} of {global_endpoint_count} NetworkAttachments; cross-Cluster shared FABNet is unsupported")
    sites = {endpoint.site for endpoint in endpoints}
    if len(sites) != 1:
        _fail(entry, "/spec", f"FABNet requires exactly one distinct effective FABRIC endpoint site; found {len(sites)}")
    for endpoint in endpoints:
        if endpoint.component.model != "NIC_Basic":
            _fail(endpoint.attachment, "/interfaceRequirements",
                  f"FABNet requires NIC_Basic; dedicated-NIC FABNet bindings are deferred; resolved {endpoint.component.model}")
        if "addresses" in endpoint.attachment.data:
            _fail(endpoint.attachment, "/addresses", "FABNet requires attachment addresses to be omitted, including empty lists")
    for field in ("prefixes", "defaultGateways", "dns"):
        if field in spec:
            _fail(entry, f"/spec/{field}", f"FABNet requires Network {field} to be omitted, including empty configuration")
    service = {
        ("ipv4", "provider-internal"): "IPv4",
        ("ipv6", "provider-internal"): "IPv6",
        ("ipv4", "public"): "IPv4Ext",
        ("ipv6", "public"): "IPv6Ext",
    }[spec["addressFamily"], spec["reachability"]]
    return _FabricNetworkPlan(entry.data["metadata"]["name"], service, (), None, None, emit_empty_subnet=True)


def _network(entry: Entry, endpoints: list[_EndpointRealization], selected_cluster: str,
             global_endpoint_count: int) -> _FabricNetworkPlan:
    spec = entry.data["spec"]
    if spec.get("layer") == "layer3":
        return _fabnet(entry, endpoints, selected_cluster, global_endpoint_count)
    if spec.get("layer") != "layer2":
        _fail(entry, "/spec/layer", "only explicit layer2 or supported layer3 Networks are supported")
    sites = {endpoint.site for endpoint in endpoints}
    if spec.get("connectivity", "multipoint") == "point-to-point":
        if len(endpoints) != global_endpoint_count:
            _fail(entry, "/spec/connectivity",
                  f"selected Cluster {selected_cluster} does not contain all L2PTP endpoints: "
                  f"renders {len(endpoints)} of {global_endpoint_count} NetworkAttachments")
        if len(endpoints) != 2:
            _fail(entry, "/spec/connectivity",
                  f"point-to-point / L2PTP requires exactly two rendered NetworkAttachments; found {len(endpoints)}")
        if len(sites) != 2:
            _fail(entry, "/spec/connectivity",
                  f"L2PTP requires exactly two distinct effective FABRIC sites; found {len(sites)}")
        for endpoint in endpoints:
            if endpoint.component.model != "NIC_ConnectX_6":
                _fail(endpoint.attachment, "/interfaceRequirements",
                      f"L2PTP requires a supported dedicated interface (NIC_ConnectX_6); resolved {endpoint.component.model}")
            if "addresses" in endpoint.attachment.data:
                _fail(endpoint.attachment, "/addresses",
                      "L2PTP guest VLAN/IP configuration is not yet supported; omit attachment addresses, including empty lists")
        for field in ("prefixes", "defaultGateways", "dns"):
            if field in spec:
                _fail(entry, f"/spec/{field}",
                      f"L2PTP guest VLAN/IP configuration is not yet supported; omit Network {field}, including empty configuration")
        return _FabricNetworkPlan(entry.data["metadata"]["name"], "L2PTP", (), None, None)
    if len(sites) not in {1, 2}:
        _fail(entry, "/spec", f"multipoint layer2 requires one or two effective FABRIC endpoint sites; found {len(sites)}")
    service = "L2Bridge" if len(sites) == 1 else "L2STS"

    gateways = (tuple(_FabricGatewayPlan(gateway["prefix"], gateway["address"])
                      for gateway in spec["defaultGateways"]) if "defaultGateways" in spec else None)
    gateway_by_prefix = {ipaddress.ip_network(gateway.prefix): gateway.address for gateway in gateways or ()}
    dns = tuple(spec["dns"]["servers"]) if "dns" in spec else None
    families = set()
    for server in dns or ():
        family = ipaddress.ip_address(server).version
        if family in families:
            _fail(entry, "/spec/dns/servers", f"target supports at most one IPv{family} DNS server")
        families.add(family)

    prefixes = []
    families = set()
    for index, value in enumerate(spec.get("prefixes", [])):
        prefix = ipaddress.ip_network(value)
        if prefix.version in families:
            _fail(entry, f"/spec/prefixes/{index}",
                  f"target supports at most one IPv{prefix.version} prefix; ambiguous prefix selection is not supported")
        families.add(prefix.version)
        prefixes.append(_FabricPrefixPlan(prefix.version, value, gateway_by_prefix.get(prefix)))
    return _FabricNetworkPlan(entry.data["metadata"]["name"], service, tuple(prefixes), gateways, dns)


def _component_model(members: list[tuple[int, Entry]]) -> str:
    """Resolve compatible identity selectors, never infer capability from a model."""
    selectors: dict[str, str] = {}
    owners: dict[str, Entry] = {}
    for _, attachment in members:
        required = attachment.data.get("interfaceRequirements")
        if required is None or required.get("type") != "ethernet":
            _fail(attachment, "/interfaceRequirements", "an explicit Ethernet interface requirement is required")
        if "minSpeed" in required:
            _fail(attachment, "/interfaceRequirements/minSpeed", "minSpeed interface requirements remain unsupported")
        # An empty feature list adds no constraint, as with plain Ethernet.
        if required.get("features"):
            _fail(attachment, "/interfaceRequirements/features", "feature interface requirements remain unsupported")
        adapter = required.get("adapter", {})
        if "class" in adapter and adapter["class"] != "standard":
            _fail(attachment, "/interfaceRequirements/adapter/class", "only standard adapter class interface requirements are supported")
        # Core validation already rejects contradictory selectors in a group.
        for field, value in adapter.items():
            selectors[field] = value
            owners[field] = attachment

    first = members[0][1]
    group = first.data.get("adapterGroup")
    if not selectors and group is None:
        return "NIC_Basic"
    if "vendor" in selectors and selectors["vendor"] != "NVIDIA":
        _fail(owners["vendor"], "/interfaceRequirements/adapter/vendor",
              "dedicated interface requirements support only exact vendor NVIDIA")
    model = _DEDICATED_NIC_MODELS.get(selectors.get("model"))
    if model is None:
        context = f"adapterGroup {group!r}" if group is not None else "dedicated interface requirements"
        _fail(owners.get("model", first), "/interfaceRequirements/adapter/model" if group is None or "model" in selectors else "/adapterGroup",
              f"{context} requires exact model ConnectX-5 or ConnectX-6; no dedicated adapter is inferred")
    if len(members) > 2:
        _fail(first, "/adapterGroup",
              f"adapterGroup {group!r} has {len(members)} attachments; FABRIC {model} supports at most two interfaces")
    return model


def _resolve_components(node: Entry, registry: Registry) -> tuple[_ComponentRealization, ...]:
    """Collect node-local groups in first-use order before resolving their intent."""
    components: list[list[tuple[int, Entry]]] = []
    groups: dict[str, int] = {}
    for ordinal, data in enumerate(node.data.get("networkAttachments", []), 1):
        member = (ordinal, registry.entries[data["id"]])
        group = data.get("adapterGroup")
        if group is not None and group in groups:
            components[groups[group]].append(member)
        else:
            if group is not None:
                groups[group] = len(components)
            components.append([member])
    return tuple(_ComponentRealization(_component_model(members), tuple(members)) for members in components)


def _components(realizations: tuple[_ComponentRealization, ...],
                networks: dict[str, _FabricNetworkPlan]) -> tuple[_FabricNetworkComponentPlan, ...]:
    """Complete interface addressing after service selection; do not re-resolve NICs."""
    return tuple(
        _FabricNetworkComponentPlan(
            component.model,
            tuple(_interface(attachment, networks[attachment.data["networkRef"]], ordinal)
                  for ordinal, attachment in component.members),
        )
        for component in realizations
    )


def _interface(attachment: Entry, network: _FabricNetworkPlan, ordinal: int) -> _FabricInterfacePlan:
    if network.emit_empty_subnet:
        return _FabricInterfacePlan(network.name, (), ordinal, emit_empty_address_families=True)
    addresses = []
    families = set()
    dns = {ipaddress.ip_address(server).version: server for server in network.dns_servers or ()}
    prefixes = [(ipaddress.ip_network(prefix.address), prefix) for prefix in network.prefixes]
    for index, value in enumerate(attachment.data.get("addresses", [])):
        address = ipaddress.ip_address(value)
        path = f"/addresses/{index}"
        if address.version in families:
            _fail(attachment, path, f"target supports at most one IPv{address.version} attachment address")
        families.add(address.version)
        matches = [(prefix, plan) for prefix, plan in prefixes
                   if address.version == prefix.version and address in prefix]
        if len(matches) != 1:
            _fail(attachment, path, f"address must match exactly one declared prefix; found {len(matches)} (no longest-prefix fallback)")
        prefix, plan = matches[0]
        addresses.append(_FabricAddressPlan(address.version, value, prefix.prefixlen, plan.gateway, dns.get(address.version)))
    return _FabricInterfacePlan(network.name, tuple(addresses), ordinal)


def _select_cluster(registry: Registry, cluster: str | None) -> Entry:
    clusters = registry.of_kind("Cluster")
    if cluster is None:
        if len(clusters) != 1:
            raise FabricTranslationError(f"expected exactly one Cluster without --cluster; found {len(clusters)}")
        return clusters[0]
    reference = cluster if cluster.startswith("cluster/") else f"cluster/{cluster}"
    selected = registry.entries.get(reference)
    if selected is None or selected.kind != "Cluster":
        raise FabricTranslationError("unknown Cluster", entity=reference)
    return selected


def _translate(model: InfrastructureModel, *, cluster: str | None, config: FabricAdapterConfig) -> _FabricRenderPlan:
    try:
        registry = model._validated_registry()
    except InfrastructureError as exc:
        issue = exc.issues[0] if isinstance(exc, ValidationError) else None
        raise FabricTranslationError(f"core validation failed: {exc}", entity="InfrastructureModel",
                                     path=issue.path if issue else "", source=issue.source if issue else "") from exc
    selected = _select_cluster(registry, cluster)
    # Core validation checks extension namespace syntax. Unconsumed extensions
    # remain opaque metadata: they are neither interpreted nor copied to output.
    spec = selected.data["spec"]
    # Only the selected Cluster's endpoints are part of this rendered topology.
    nodes = [registry.entries[node["id"]] for node in spec["nodes"]]
    sites = {}
    node_components: dict[str, tuple[_ComponentRealization, ...]] = {}
    network_endpoints: dict[str, list[_EndpointRealization]] = {}
    global_endpoint_counts = Counter(attachment.data["networkRef"] for attachment in registry.of_kind("NetworkAttachment"))
    for node in nodes:
        if node.data["realization"]["type"] != "vm":
            _fail(node, "/realization/type", "only vm realization is supported; exact-resource site derivation is deferred")
        if node.data.get("configuration", {}).get("storage"):
            _fail(node, "/configuration/storage", "persistent/block-storage configuration is unsupported")
        generic_site = registry.node_site(node)
        if generic_site is None:
            _fail(node, "/placement/siteRef", "no effective site; specify node placement or a Cluster site default")
        if generic_site not in config.sites:
            _fail(node, "/placement/siteRef", f"no adapter site mapping for {generic_site}")
        sites[node.id] = config.sites[generic_site]
        node_components[node.id] = _resolve_components(node, registry)
        component_by_attachment = {attachment.id: component for component in node_components[node.id]
                                   for _, attachment in component.members}
        # Network first use follows authored attachments, even for interleaved groups.
        for data in node.data.get("networkAttachments", []):
            attachment = registry.entries[data["id"]]
            endpoint = _EndpointRealization(attachment, sites[node.id], component_by_attachment[attachment.id])
            network_endpoints.setdefault(data["networkRef"], []).append(endpoint)
    networks = {reference: _network(registry.entries[reference], endpoints, selected.id, global_endpoint_counts[reference])
                for reference, endpoints in network_endpoints.items()}
    plans = []
    for node in nodes:
        fabnet_attachments = [data for data in node.data.get("networkAttachments", [])
                              if networks[data["networkRef"]].emit_empty_subnet]
        if len(fabnet_attachments) > 1:
            _fail(node, "/networkAttachments",
                  "at most one FABNet attachment per VM is supported by the downstream guest interface/routing workflow")
        devices = _devices(node, registry)
        cpu, ram, disk = _node_capacity(node)
        image = node.data.get("image", spec.get("image"))
        image_path = "/image" if "image" in node.data else "/spec/image"
        image_owner = node if "image" in node.data else selected
        if image is None or "version" not in image:
            _fail(image_owner, image_path, "an explicit image name and version are required; no image is guessed")
        identity = (image["name"], image["version"])
        if identity not in config.images:
            _fail(image_owner, image_path, f"no exact adapter image mapping for {identity!r}")
        roles = node.data.get("roles", [])
        unsupported = [role for role in roles if role not in _ROLES]
        if unsupported:
            _fail(node, "/roles", f"unsupported semantic roles: {unsupported!r}")
        host_ref = node.data.get("placement", {}).get("hostRef")
        worker = None
        if host_ref is not None:
            if host_ref not in config.workers:
                _fail(node, "/placement/hostRef", f"no adapter worker mapping for {host_ref}")
            worker = config.workers[host_ref]
        components = _components(node_components[node.id], networks)
        plans.append(_FabricNodePlan(node.data["name"], sites[node.id], cpu, ram, disk, config.images[identity],
                                     tuple(role.removeprefix("openstack.") for role in roles), components, worker=worker,
                                     gpus=devices["gpu"], fpgas=devices["fpga"], nvmes=devices["nvme"], dpus=devices["dpu"]))
    return _FabricRenderPlan(tuple(plans), tuple(networks.values()))
