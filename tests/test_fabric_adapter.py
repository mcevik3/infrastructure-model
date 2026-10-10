from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Inexact, localcontext

import pytest
import yaml

from infra_model import InfrastructureModel
from infra_model.adapters import fabric
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from infra_model.adapters.fabric.renderer import _render
from infra_model.adapters.fabric.translator import _translate
from infra_model.loader import load_documents
from conftest import ROOT


CONFIG = ROOT / "adapter-configs/fabric.yaml"


@pytest.fixture
def config_data():
    return yaml.safe_load(CONFIG.read_text())


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


@pytest.fixture
def source():
    return [doc.data for doc in load_documents(ROOT / "examples/fabric-like")]


def cluster(source):
    return next(doc for doc in source if doc["kind"] == "Cluster")


def node(source):
    return cluster(source)["spec"]["nodes"][0]


def network(source):
    return next(doc for doc in source if doc["kind"] == "Network")


def render(source, config, **kwargs):
    return render_fabric(InfrastructureModel.from_documents(source), config=config, **kwargs)


def nodes(document):
    return document["site_topology_nodes"]["nodes"]


def networks(document):
    return document["site_topology_networks"]["networks"]


def interface(document):
    return nodes(document)["node1"]["pci"]["network"]["nic1"]["interfaces"]["iface1"]


def test_config_and_public_api(config, config_data):
    assert fabric.__all__ == ["FabricAdapterConfig", "FabricTranslationError", "render_fabric"]
    assert not hasattr(fabric, "FabricRenderPlan")
    assert config.sites["site/site-a"] == "SRI"
    assert config.images[("Rocky Linux", "9")] == "default_rocky_9"
    assert config.workers == {}
    from_dict = FabricAdapterConfig.from_dict(config_data)
    config_data["sites"].clear()
    assert from_dict == config
    with pytest.raises(TypeError):
        config.sites["site/new"] = "NEW"
    with pytest.raises(FrozenInstanceError):
        config.sites = {}


@pytest.mark.parametrize("field,value", [
    ("apiVersion", "infra.model/v0alpha1"), ("kind", "Network"),
    ("sites", []), ("sites", {"lab-site": "SRI"}), ("sites", {"site/lab-site\n": "SRI"}),
    ("sites", {"site/lab-site": ""}), ("sites", {"site/lab-site": "  "}),
    ("sites", {"site/lab-site": 1}), ("sites", {1: "SRI"}),
    ("images", {}), ("images", [{"name": "Rocky Linux", "version": "9"}]),
    ("images", [{"name": "Rocky Linux", "fabricImage": "default_rocky_9"}]),
    ("images", [{"version": "9", "fabricImage": "default_rocky_9"}]),
    ("images", [{"name": "Rocky Linux", "version": 9, "fabricImage": "default_rocky_9"}]),
    ("images", [{"name": "Rocky Linux", "version": "9", "fabricImage": ""}]),
    ("images", [{"name": "Rocky Linux", "version": "9", "fabricImage": "image", "extra": True}]),
    ("defaultSite", "SRI"), ("defaultWorker", "some-worker"), ("extra", True),
])
def test_invalid_config(config_data, field, value):
    config_data[field] = value
    with pytest.raises(FabricTranslationError):
        FabricAdapterConfig.from_dict(config_data)


@pytest.mark.parametrize("field", ["apiVersion", "kind", "sites", "images"])
def test_config_required_fields(config_data, field):
    config_data.pop(field)
    with pytest.raises(FabricTranslationError):
        FabricAdapterConfig.from_dict(config_data)


def test_duplicate_image_mapping(config_data):
    config_data["images"].append({"name": "Rocky Linux", "version": "9", "fabricImage": "different"})
    with pytest.raises(FabricTranslationError, match="duplicate logical image"):
        FabricAdapterConfig.from_dict(config_data)


@pytest.mark.parametrize("content", ["kind: [", "sites: {}\nsites: {}", "---\na: 1\n---\nb: 2", "", "- invalid"])
def test_bad_config_files(tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content)
    with pytest.raises(FabricTranslationError):
        FabricAdapterConfig.from_file(path)


def test_missing_config_file_and_directory(tmp_path):
    for path in (tmp_path, tmp_path / "missing"):
        with pytest.raises(FabricTranslationError):
            FabricAdapterConfig.from_file(path)


@pytest.mark.parametrize("selection", [None, "openstack-lab", "cluster/openstack-lab"])
def test_cluster_selection(source, config, selection):
    assert nodes(render(source, config, cluster=selection))["node1"]["name"] == "controller-1"


def test_cluster_selection_errors_and_unrelated_intent(source, config):
    with pytest.raises(FabricTranslationError, match="unknown Cluster"):
        render(source, config, cluster="missing")
    other = deepcopy(cluster(source))
    other["metadata"]["name"] = "other"
    other["spec"]["nodes"][0]["roles"] = ["unsupported.role"]
    source.append(other)
    with pytest.raises(FabricTranslationError, match="--cluster"):
        render(source, config)
    assert len(nodes(render(source, config, cluster="openstack-lab"))) == 2
    with pytest.raises(FabricTranslationError, match="found 0"):
        render([doc for doc in source if doc["kind"] != "Cluster"], config)


def test_fabric_like_historical_document(source, config):
    path = ROOT / "examples/fabric-like/cluster.yaml"
    source_bytes = path.read_bytes()
    before = deepcopy(source)
    assert all(item["requirements"]["compute"]["architecture"] == "x86_64" for item in cluster(source)["spec"]["nodes"])
    assert cluster(source)["spec"]["extensions"] == {"fabric.example/intent": {"experimentName": "openstack-lab"}}
    document = render(source, config)
    expected = yaml.safe_load((ROOT / "tests/fixtures/fabric-like-rendered.yaml").read_text())
    assert document == expected
    assert list(nodes(document)) == ["node1", "node2"]
    assert list(networks(document)) == ["net1", "net2"]
    assert all(item["site"] == "SRI" for item in nodes(document).values())
    assert source == before and path.read_bytes() == source_bytes


@pytest.mark.parametrize("architecture", [None, "x86_64"])
def test_architecture_compatibility_is_not_an_output_field(source, config, architecture):
    for item in cluster(source)["spec"]["nodes"]:
        if architecture is None:
            item["requirements"]["compute"].pop("architecture")
        else:
            item["requirements"]["compute"]["architecture"] = architecture
    before = deepcopy(source)
    document = render(source, config)
    assert "architecture" not in yaml.safe_dump(document)
    assert "x86_64" not in yaml.safe_dump(document)
    assert source == before


def test_multi_site_and_node_overrides(config):
    model = InfrastructureModel.load(ROOT / "examples/multi-site")
    document = render_fabric(model, config=config)
    assert [item["site"] for item in nodes(document).values()] == ["SRI", "UKY"]
    assert networks(document) == {"net1": {"name": "multi-site-data", "type": "L2STS"}}
    assert interface(document) == {"device": "eth1", "connection": "conn-eth1", "binding": "multi-site-data"}


def test_allowed_scope_is_not_occupancy_and_unused_site_needs_no_mapping(config_data):
    source = [doc.data for doc in load_documents(ROOT / "examples/multi-site")]
    cluster(source)["spec"]["nodes"][1].pop("placement")
    config_data["sites"].pop("site/site-b")
    document = render(source, FabricAdapterConfig.from_dict(config_data))
    assert networks(document)["net1"]["type"] == "L2Bridge"


def test_generic_site_aliases_map_to_one_provider_site(config_data):
    config_data["sites"]["site/site-b"] = "SRI"
    document = render_fabric(InfrastructureModel.load(ROOT / "examples/multi-site"),
                             config=FabricAdapterConfig.from_dict(config_data))
    assert networks(document)["net1"]["type"] == "L2Bridge"


def test_only_used_networks_in_first_use_order(source, config):
    unused = deepcopy(network(source))
    unused["metadata"]["name"] = "unused"
    unused["spec"] = {"layer": "layer3", "connectivity": "point-to-point"}
    source.insert(0, unused)
    node(source)["networkAttachments"].reverse()
    document = render(source, config)
    assert [item["name"] for item in networks(document).values()] == ["lab-storage", "lab-management"]
    assert interface(document)["binding"] == "lab-storage"
    assert list(nodes(document)["node1"]["pci"]["network"]) == ["nic1", "nic2"]


@pytest.mark.parametrize("field,value,expected", [
    ("memory", {"value": 4096, "unit": "MiB"}, 4),
    ("memory", {"value": 4294967296, "unit": "B"}, 4),
    ("storage", {"value": 1.5, "unit": "TiB"}, 1536),
    ("storage", {"value": 1073741824, "unit": "GB"}, 1000000000),
])
def test_exact_capacities(source, config, field, value, expected):
    node(source)["requirements"][field]["capacity"] = value
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        capacity = nodes(render(source, config))["node1"]["capacity"]
    assert capacity["ram" if field == "memory" else "disk"] == expected
    assert type(capacity["cpu"]) is int


def test_image_override_is_exact_and_not_merged(source, config_data):
    node(source)["image"] = {"name": "Other", "version": "1"}
    config_data["images"].append({"name": "Other", "version": "1", "fabricImage": "other_1"})
    config = FabricAdapterConfig.from_dict(config_data)
    assert nodes(render(source, config))["node1"]["capacity"]["os"] == "other_1"
    node(source)["image"].pop("version")
    with pytest.raises(FabricTranslationError, match="name and version"):
        render(source, config)


def test_dual_stack_addresses_gateways_dns(source, config):
    net = network(source)["spec"]
    net["prefixes"].append("2001:db8::/64")
    net["defaultGateways"].append({"prefix": "2001:0db8::/64", "address": "fe80::1"})
    net["dns"]["servers"].append("2001:db8::53")
    node(source)["networkAttachments"][0]["addresses"].append("2001:db8::11")
    document = render(source, config)
    assert interface(document)["ipv4"] == {"address": "192.0.2.11/24", "gateway": "192.0.2.1", "dns": "192.0.2.53"}
    assert interface(document)["ipv6"] == {"address": "2001:db8::11/64", "gateway": "fe80::1", "dns": "2001:db8::53"}
    assert networks(document)["net1"]["subnet"]["ipv6"] == {"address": "2001:db8::/64", "gateway": "fe80::1"}


def test_ipv6_only_interface_and_unspecified_connectivity(source, config):
    spec = network(source)["spec"]
    spec["prefixes"] = ["2001:db8::/64"]
    spec["defaultGateways"] = [{"prefix": "2001:db8::/64", "address": "fe80::1"}]
    spec["dns"]["servers"] = ["2001:db8::53"]
    for index, item in enumerate(cluster(source)["spec"]["nodes"], 1):
        item["networkAttachments"][0]["addresses"] = [f"2001:db8::{index}"]
    document = render(source, config)
    assert "ipv4" not in interface(document)
    assert interface(document)["ipv6"] == {"address": "2001:db8::1/64", "gateway": "fe80::1", "dns": "2001:db8::53"}
    assert networks(document)["net1"]["type"] == "L2Bridge"


def test_plan_retains_configured_gateway_dns_values(source, config):
    plan = _translate(InfrastructureModel.from_documents(source), cluster=None, config=config)
    assert plan.networks[0].default_gateways[0].prefix == "192.0.2.0/24"
    assert plan.networks[0].default_gateways[0].address == "192.0.2.1"
    assert plan.networks[0].dns_servers == ("192.0.2.53",)


@pytest.mark.parametrize("explicit_empty", [False, True])
def test_gateway_dns_omission_and_empty_preserved_in_plan(source, config, explicit_empty):
    net = network(source)["spec"]
    net.pop("defaultGateways")
    net.pop("dns")
    if explicit_empty:
        net.update(defaultGateways=[], dns={"servers": []})
    plan = _translate(InfrastructureModel.from_documents(source), cluster=None, config=config)
    expected = () if explicit_empty else None
    assert plan.networks[0].default_gateways == expected
    assert plan.networks[0].dns_servers == expected
    assert interface(_render(plan))["ipv4"] == {"address": "192.0.2.11/24"}


def test_render_deterministic_and_inputs_unchanged(source, config):
    before = deepcopy(source)
    model = InfrastructureModel.from_documents(source)
    first = render_fabric(model, config=config)
    second = render_fabric(model, config=config)
    assert first == second and source == before
    assert yaml.safe_dump(first, sort_keys=False) == yaml.safe_dump(second, sort_keys=False)
    nodes(first).clear()
    assert len(nodes(render_fabric(model, config=config))) == 2


def test_plan_supports_multiple_interfaces_per_component_without_provider_resolution(source, config):
    plan = _translate(InfrastructureModel.from_documents(source), cluster=None, config=config)
    first = plan.nodes[0]
    component = replace(first.components[0], interfaces=(first.components[0].interfaces[0], first.components[1].interfaces[0]))
    changed = replace(plan, nodes=(replace(first, components=(component, first.components[1])),))
    rendered = nodes(_render(changed))["node1"]["pci"]["network"]
    assert rendered["nic1"]["interfaces"]["iface2"]["device"] == "eth2"
    assert rendered["nic2"]["interfaces"]["iface1"]["device"] == "eth3"
    with pytest.raises(FrozenInstanceError):
        plan.nodes = ()


@pytest.mark.parametrize("field", ["sites", "images"])
def test_missing_mapping(source, config_data, field):
    config_data[field] = {} if field == "sites" else []
    with pytest.raises(FabricTranslationError, match="mapping") as caught:
        render(source, FabricAdapterConfig.from_dict(config_data))
    assert caught.value.entity.startswith("cluster/")
    assert caught.value.path.startswith("/spec/")
    assert caught.value.source.startswith("memory#document=")


def test_missing_effective_site(source, config):
    cluster(source)["spec"].pop("siteRef")
    with pytest.raises(FabricTranslationError, match="no effective site"):
        render(source, config)


@pytest.mark.parametrize("image", [None, {"name": "Rocky Linux"}, {"name": "rocky linux", "version": "9"},
                                  {"name": "Rocky Linux", "version": "09"}])
def test_missing_or_inexact_image_fails(source, config, image):
    if image is None:
        cluster(source)["spec"].pop("image")
    else:
        cluster(source)["spec"]["image"] = image
    with pytest.raises(FabricTranslationError, match="image"):
        render(source, config)


@pytest.mark.parametrize("owner", [cluster, node, network,
                                  lambda docs: node(docs)["requirements"]["memory"],
                                  lambda docs: node(docs).setdefault("placement", {}),
                                  lambda docs: node(docs)["networkAttachments"][0],
                                  lambda docs: cluster(docs)["spec"]["image"]])
def test_opaque_extensions_are_preserved_in_source_and_ignored_in_output(source, config, owner):
    expected = render(source, config)
    entity = owner(source)
    fields = entity.get("spec", entity)
    fields["extensions"] = {"example.org/opaque": {"required": True, "nested": [1, None]}}
    before = deepcopy(source)
    assert render(source, config) == expected
    assert source == before


def test_opaque_extension_does_not_suppress_unsupported_compute_constraint(source, config):
    node(source)["requirements"]["extensions"] = {"example.org/opaque": {"frequency": "ignored"}}
    node(source)["requirements"]["compute"]["frequency"] = {"value": 3, "unit": "GHz"}
    with pytest.raises(FabricTranslationError, match="explicit frequency"):
        render(source, config)


def test_descriptions_and_observations_are_not_rendering_requests(source, config):
    expected = render(source, config)
    cluster(source)["metadata"]["description"] = "Informational label"
    node(source)["status"] = {"extensions": {"example.org/observation": {"state": "unknown"}}}
    assert render(source, config) == expected


@pytest.mark.parametrize("kind", ["gpu", "fpga", "storage", "dpu", "other"])
def test_device_requirements_fail(source, config, kind):
    requirement = {"name": "device", "type": kind}
    if kind == "storage":
        requirement["constraints"] = {"protocol": "nvme"}
    node(source)["requirements"]["devices"] = [requirement]
    with pytest.raises(FabricTranslationError, match="device requirements"):
        render(source, config)


@pytest.mark.parametrize("required", [
    None, {"type": "infiniband"}, {"type": "ethernet", "minSpeed": {"value": 100, "unit": "Gbps"}},
    {"type": "ethernet", "features": ["rdma"]}, {"type": "ethernet", "features": ["sriov"]},
    {"type": "ethernet", "adapter": {"class": "smartnic", "vendor": "Mellanox", "model": "ConnectX-6"}},
    {"type": "ethernet", "adapter": {"class": "dpu"}},
    {"type": "ethernet", "adapter": {"class": "standard"}},
])
def test_specialized_or_unspecified_interfaces_fail(source, config, required):
    attachment = node(source)["networkAttachments"][0]
    if required is None:
        attachment.pop("interfaceRequirements")
    else:
        attachment["interfaceRequirements"] = required
    with pytest.raises(FabricTranslationError, match="interface requirement"):
        render(source, config)


def test_empty_device_and_feature_requirements_add_no_constraint(source, config):
    node(source)["requirements"]["devices"] = []
    node(source)["networkAttachments"][0]["interfaceRequirements"]["features"] = []
    assert nodes(render(source, config))["node1"]["pci"]["network"]["nic1"]["model"] == "NIC_Basic"


@pytest.mark.parametrize("unit", ["core", "thread"])
def test_physical_cpu_units_fail(source, config, unit):
    node(source)["requirements"]["compute"]["cpu"]["unit"] = unit
    with pytest.raises(FabricTranslationError, match="vcpu"):
        render(source, config)


@pytest.mark.parametrize("group", ["memory", "storage"])
@pytest.mark.parametrize("quantity", [{"value": 1, "unit": "GB"}, {"value": 1.5, "unit": "GiB"}])
def test_fractional_target_capacities_fail(source, config, group, quantity):
    node(source)["requirements"][group]["capacity"] = quantity
    with pytest.raises(FabricTranslationError, match="integral GiB"):
        render(source, config)


@pytest.mark.parametrize("group", ["compute", "memory", "storage"])
def test_missing_capacity_fails(source, config, group):
    node(source)["requirements"].pop(group)
    with pytest.raises(FabricTranslationError, match="capacity is required"):
        render(source, config)


@pytest.mark.parametrize("group,field,value", [
    ("compute", "architecture", "aarch64"), ("compute", "frequency", {"value": 3, "unit": "GHz"}),
    ("storage", "medium", "ssd"), ("storage", "protocol", "nvme"),
])
def test_unrepresentable_additional_requirements_fail(source, config, group, field, value):
    node(source)["requirements"][group][field] = value
    with pytest.raises(FabricTranslationError):
        render(source, config)


def test_unsupported_role_and_realization(source, config):
    node(source)["roles"] = ["database.primary"]
    with pytest.raises(FabricTranslationError, match="unsupported semantic roles"):
        render(source, config)
    node(source)["roles"] = []
    node(source)["realization"]["type"] = "baremetal"
    with pytest.raises(FabricTranslationError, match="only vm"):
        render(source, config)


def test_persistent_storage_intent_fails(source, config):
    # VM block-storage intent already fails core validation; it must never produce degraded YAML.
    node(source)["configuration"] = {"storage": {"partitionTables": []}}
    with pytest.raises(FabricTranslationError):
        render(source, config)


@pytest.mark.parametrize("field,value", [("layer", "layer3"), ("connectivity", "point-to-point")])
def test_unsupported_network_types(source, config, field, value):
    network(source)["spec"][field] = value
    with pytest.raises(FabricTranslationError):
        render(source, config)


def test_three_actual_provider_sites_fail(config_data):
    source = [doc.data for doc in load_documents(ROOT / "examples/multi-site")]
    source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Site", "metadata": {"name": "site-c"}, "spec": {}})
    third = deepcopy(node(source))
    third.update(name="vm-c", placement={"siteRef": "site/site-c"})
    cluster(source)["spec"]["nodes"].append(third)
    network(source)["spec"]["siteRefs"].append("site/site-c")
    config_data["sites"]["site/site-c"] = "CLEM"
    with pytest.raises(FabricTranslationError, match="found 3"):
        render(source, FabricAdapterConfig.from_dict(config_data))


@pytest.mark.parametrize("prefixes", [
    ["192.0.2.0/24", "198.51.100.0/24"], ["192.0.2.0/24", "192.0.2.0/25"],
    ["192.0.2.0/24", "2001:db8::/64", "2001:db8:1::/64"],
])
def test_multiple_or_ambiguous_prefixes_fail(source, config, prefixes):
    network(source)["spec"]["prefixes"] = prefixes
    with pytest.raises(FabricTranslationError, match="at most one IPv"):
        render(source, config)


@pytest.mark.parametrize("addresses", [
    ["192.0.2.11", "192.0.2.12"], ["2001:db8::11", "2001:db8::12"],
])
def test_multiple_family_addresses_fail(source, config, addresses):
    network(source)["spec"]["prefixes"].append("2001:db8::/64")
    node(source)["networkAttachments"][0]["addresses"] = addresses
    with pytest.raises(FabricTranslationError, match="attachment address"):
        render(source, config)


def test_address_outside_prefix_fails_without_rendering(source, config):
    node(source)["networkAttachments"][0]["addresses"] = ["203.0.113.10"]
    with pytest.raises(FabricTranslationError, match="address_outside_prefix") as caught:
        render(source, config)
    assert caught.value.path.endswith("/addresses/0")


@pytest.mark.parametrize("servers", [["192.0.2.53", "192.0.2.54"], ["2001:db8::53", "2001:db8::54"]])
def test_multiple_dns_servers_per_family_fail(source, config, servers):
    network(source)["spec"]["dns"]["servers"] = servers
    with pytest.raises(FabricTranslationError, match="DNS server"):
        render(source, config)


@pytest.mark.parametrize("workers", [
    [], None, {"host-a": "worker-1"}, {"site/site-a": "worker-1"},
    {"server/host-a\n": "worker-1"}, {"server/host-a/interface/eth0": "worker-1"},
    {"server/host-a": ""}, {"server/host-a": "  "}, {"server/host-a": 1},
])
def test_invalid_worker_config(config_data, workers):
    config_data["workers"] = workers
    with pytest.raises(FabricTranslationError):
        FabricAdapterConfig.from_dict(config_data)


def test_worker_config_is_optional_immutable_and_loaded_from_file(config_data, tmp_path):
    config_data["workers"] = {"server/requested-host": "provider-worker-7", "server/unused": "unused-worker"}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config_data))
    config = FabricAdapterConfig.from_file(path)
    assert dict(config.workers) == config_data["workers"]
    config_data["workers"].clear()
    assert config.workers["server/requested-host"] == "provider-worker-7"
    with pytest.raises(TypeError):
        config.workers["server/requested-host"] = "changed"
    with pytest.raises(FrozenInstanceError):
        config.workers = {}


@pytest.fixture
def host_source(source):
    source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Server",
                   "metadata": {"name": "requested-host"}, "spec": {"siteRef": "site/lab-site",
                   "capabilities": {"nodeTypes": ["baremetal"], "compute": {"cpu": {"cores": 1}}}}})
    node(source)["placement"] = {"hostRef": "server/requested-host"}
    return source


def test_worker_mapping_without_host_ref_does_not_select_host(source, config_data):
    config_data["workers"] = {"server/unused": "unused-worker"}
    config = FabricAdapterConfig.from_dict(config_data)
    plan = _translate(InfrastructureModel.from_documents(source), cluster=None, config=config)
    assert all(item.worker is None for item in plan.nodes)
    assert all(item["worker"] == "" for item in nodes(_render(plan)).values())


@pytest.mark.parametrize("override", [None, "site/site-b"])
def test_host_ref_maps_worker_independently_of_site(host_source, config_data, override):
    if override is not None:
        host_source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Site", "metadata": {"name": "site-b"}, "spec": {}})
        node(host_source)["placement"]["siteRef"] = override
        next(doc for doc in host_source if doc["kind"] == "Server")["spec"]["siteRef"] = override
        for doc in host_source:
            if doc["kind"] == "Network":
                doc["spec"]["siteRefs"].append(override)
    config_data["workers"] = {"server/requested-host": "provider-worker-7"}
    config = FabricAdapterConfig.from_dict(config_data)
    before = deepcopy(host_source)
    model = InfrastructureModel.from_documents(host_source)
    plan = _translate(model, cluster=None, config=config)
    assert plan.nodes[0].worker == "provider-worker-7"
    assert plan.nodes[1].worker is None
    first = render_fabric(model, config=config)
    second = render_fabric(model, config=config)
    assert first == second
    assert yaml.safe_dump(first, sort_keys=False) == yaml.safe_dump(second, sort_keys=False)
    assert nodes(first)["node1"]["worker"] == "provider-worker-7"
    assert nodes(first)["node1"]["site"] == ("UKY" if override else "SRI")
    assert nodes(first)["node2"]["worker"] == ""
    assert host_source == before


@pytest.mark.parametrize("workers", [None, {}, {"server/unrelated": "provider-worker-7"}])
def test_explicit_host_without_worker_mapping_fails(host_source, config_data, workers):
    if workers is not None:
        config_data["workers"] = workers
    node(host_source)["placement"]["extensions"] = {"fabric.example/worker": "provider-worker-7"}
    with pytest.raises(FabricTranslationError, match="no adapter worker mapping for server/requested-host") as caught:
        render(host_source, FabricAdapterConfig.from_dict(config_data))
    assert caught.value.entity.endswith("/node/controller-1")
    assert caught.value.path.endswith("/placement/hostRef")


def test_worker_and_server_inventory_do_not_supply_effective_site(host_source, config_data):
    config_data["workers"] = {"server/requested-host": "sri-worker-17"}
    cluster(host_source)["spec"].pop("siteRef")
    model = InfrastructureModel.from_documents(host_source)
    model.validate()
    assert model.topology.neighbors("cluster/openstack-lab/node/controller-1", "targets_site") == []
    with pytest.raises(FabricTranslationError, match="no effective site"):
        render_fabric(model, config=FabricAdapterConfig.from_dict(config_data))


def test_worker_mapping_does_not_supply_site_mapping(host_source, config_data):
    config_data.update(workers={"server/requested-host": "sri-worker-17"}, sites={})
    with pytest.raises(FabricTranslationError, match="no adapter site mapping"):
        render(host_source, FabricAdapterConfig.from_dict(config_data))


def test_host_site_conflict_is_rejected_by_core_before_translation(host_source, config_data):
    host_source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Site", "metadata": {"name": "site-b"}, "spec": {}})
    next(doc for doc in host_source if doc["kind"] == "Server")["spec"]["siteRef"] = "site/site-b"
    config_data["workers"] = {"server/requested-host": "provider-worker-7"}
    with pytest.raises(FabricTranslationError, match="core validation failed") as caught:
        render(host_source, FabricAdapterConfig.from_dict(config_data))
    assert caught.value.path.endswith("/placement/hostRef")
