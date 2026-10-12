"""Private immutable render data, not a public infrastructure schema."""

from dataclasses import dataclass


@dataclass(frozen=True)
class _FabricGatewayPlan:
    prefix: str
    address: str


@dataclass(frozen=True)
class _FabricPrefixPlan:
    family: int
    address: str
    gateway: str | None


@dataclass(frozen=True)
class _FabricNetworkPlan:
    name: str
    service: str
    prefixes: tuple[_FabricPrefixPlan, ...]
    default_gateways: tuple[_FabricGatewayPlan, ...] | None
    dns_servers: tuple[str, ...] | None
    emit_empty_subnet: bool = False


@dataclass(frozen=True)
class _FabricAddressPlan:
    family: int
    address: str
    prefix_length: int
    gateway: str | None
    dns: str | None


@dataclass(frozen=True)
class _FabricInterfacePlan:
    network_name: str
    addresses: tuple[_FabricAddressPlan, ...]
    attachment_ordinal: int
    emit_empty_address_families: bool = False


@dataclass(frozen=True)
class _FabricNetworkComponentPlan:
    model: str
    interfaces: tuple[_FabricInterfacePlan, ...]


@dataclass(frozen=True)
class _FabricDevicePlan:
    name: str
    model: str


@dataclass(frozen=True)
class _FabricNodePlan:
    name: str
    site: str
    cpu: int
    ram: int
    disk: int
    image: str
    roles: tuple[str, ...]
    components: tuple[_FabricNetworkComponentPlan, ...]
    worker: str | None
    gpus: tuple[_FabricDevicePlan, ...]
    fpgas: tuple[_FabricDevicePlan, ...]
    nvmes: tuple[_FabricDevicePlan, ...]
    dpus: tuple[_FabricDevicePlan, ...]


@dataclass(frozen=True)
class _FabricRenderPlan:
    nodes: tuple[_FabricNodePlan, ...]
    networks: tuple[_FabricNetworkPlan, ...]
