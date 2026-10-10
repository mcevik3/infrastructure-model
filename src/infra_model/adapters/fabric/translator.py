"""Offline provider reasoning and representability checks for one Cluster."""

from fractions import Fraction
import ipaddress

from ...errors import InfrastructureError, ValidationError
from ...model import InfrastructureModel
from ...registry import Entry, Registry
from .config import FabricAdapterConfig
from .errors import FabricTranslationError
from .plan import (
    _FabricAddressPlan, _FabricGatewayPlan, _FabricInterfacePlan,
    _FabricNetworkComponentPlan, _FabricNetworkPlan, _FabricNodePlan,
    _FabricPrefixPlan, _FabricRenderPlan,
)


_BYTE_FACTORS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
                 "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
_ROLES = {"openstack.control", "openstack.network", "openstack.compute", "openstack.storage"}


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
    if requirements.get("devices"):
        _fail(node, "/requirements/devices", "device requirements (GPU/FPGA/NVMe/DPU/other) are unsupported")
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


def _network(entry: Entry, sites: set[str]) -> _FabricNetworkPlan:
    spec = entry.data["spec"]
    if spec.get("layer") != "layer2":
        _fail(entry, "/spec/layer", "only explicit layer2 Networks are supported")
    if spec.get("connectivity", "multipoint") != "multipoint":
        _fail(entry, "/spec/connectivity", "point-to-point / L2PTP requires dedicated-interface realization and is deferred")
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


def _interface(attachment: Entry, network: _FabricNetworkPlan) -> _FabricInterfacePlan:
    required = attachment.data.get("interfaceRequirements")
    # An empty feature requirement adds no constraint; all other specialization fails.
    if required is None or required.get("type") != "ethernet":
        _fail(attachment, "/interfaceRequirements", "an explicit plain Ethernet interface requirement is required")
    if "minSpeed" in required or "adapter" in required or required.get("features"):
        _fail(attachment, "/interfaceRequirements", "specialized interface requirements cannot fall back to NIC_Basic")
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
    return _FabricInterfacePlan(network.name, tuple(addresses))


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
    network_sites: dict[str, set[str]] = {}
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
        for attachment in node.data.get("networkAttachments", []):
            network_sites.setdefault(attachment["networkRef"], set()).add(sites[node.id])
    networks = {reference: _network(registry.entries[reference], occupied)
                for reference, occupied in network_sites.items()}
    plans = []
    for node in nodes:
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
        components = tuple(
            _FabricNetworkComponentPlan("NIC_Basic", (_interface(registry.entries[attachment["id"]],
                                                               networks[attachment["networkRef"]]),))
            for attachment in node.data.get("networkAttachments", [])
        )
        plans.append(_FabricNodePlan(node.data["name"], sites[node.id], cpu, ram, disk, config.images[identity],
                                     tuple(role.removeprefix("openstack.") for role in roles), components, worker=worker))
    return _FabricRenderPlan(tuple(plans), tuple(networks.values()))
