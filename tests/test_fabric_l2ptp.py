from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel, ValidationError
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from infra_model.adapters.fabric.translator import _translate
from infra_model.cli import main
from infra_model.loader import load_documents
from conftest import ROOT
from test_fabric_adapter import cluster, network, networks, nodes, render


FIXTURE = ROOT / "tests/fixtures/fabric-l2ptp.yaml"
CONFIG = ROOT / "adapter-configs/fabric.yaml"


@pytest.fixture
def source():
    return [doc.data for doc in load_documents(FIXTURE)]


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


def endpoints(source):
    return [node["networkAttachments"][0] for node in cluster(source)["spec"]["nodes"]]


def test_l2ptp_resolves_complete_dedicated_endpoints_without_l3_or_vlan_output(source, config):
    before = deepcopy(source)
    model = InfrastructureModel.from_documents(source)
    assert len(model.validate().warnings) == 2
    plan = _translate(model, cluster=None, config=config)
    assert plan.networks[0].service == "L2PTP"
    assert plan.networks[0].prefixes == ()
    assert plan.networks[0].default_gateways is None
    assert plan.networks[0].dns_servers is None
    document = render_fabric(model, config=config)
    assert networks(document) == {"net1": {"name": "ptp-data", "type": "L2PTP"}}
    assert [node["site"] for node in nodes(document).values()] == ["SRI", "UKY"]
    for node in nodes(document).values():
        assert node["pci"]["network"] == {"nic1": {
            "name": "nic1", "model": "NIC_ConnectX_6", "interfaces": {"iface1": {
                "device": "eth1", "connection": "conn-eth1", "binding": "ptp-data",
            }},
        }}
    assert source == before
    assert document == render_fabric(model, config=config)
    assert yaml.safe_dump(document, sort_keys=False) == yaml.safe_dump(render_fabric(model, config=config), sort_keys=False)


@pytest.mark.parametrize("scope", [None, ["site/site-b", "site/site-a"], ["site/site-a", "site/site-b", "site/unused"]])
def test_scope_is_not_endpoint_site_count(source, config, scope):
    if scope is None:
        network(source)["spec"].pop("siteRefs")
    else:
        network(source)["spec"]["siteRefs"] = scope
        if "site/unused" in scope:
            source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Site", "metadata": {"name": "unused"}, "spec": {}})
    assert networks(render(source, config))["net1"]["type"] == "L2PTP"


def test_grouped_endpoint_uses_resolved_component_and_keeps_other_interface_configuration(source, config):
    storage = deepcopy(network(source))
    storage["metadata"]["name"] = "storage"
    storage["spec"] = {"layer": "layer2", "prefixes": ["192.0.2.0/24"],
                       "defaultGateways": [{"prefix": "192.0.2.0/24", "address": "192.0.2.1"}],
                       "dns": {"servers": ["192.0.2.53"]}}
    source.append(storage)
    for index, node in enumerate(cluster(source)["spec"]["nodes"], 11):
        ptp = node["networkAttachments"][0]
        ptp["adapterGroup"] = "hs1"
        # The PTP attachment has no adapter selector: its dedicated realization
        # comes from the other member, so Ethernet alone cannot decide eligibility.
        ptp["interfaceRequirements"] = {"type": "ethernet"}
        node["networkAttachments"].append({
            "name": "storage", "networkRef": "network/storage", "adapterGroup": "hs1",
            "interfaceRequirements": {"type": "ethernet", "adapter": {"class": "standard", "model": "ConnectX-6"}},
            "addresses": [f"192.0.2.{index}"],
        })
    model = InfrastructureModel.from_documents(source)
    assert len(model.of_kind("NetworkAttachment")) == 4
    document = render_fabric(model, config=config)
    assert [net["type"] for net in networks(document).values()] == ["L2PTP", "L2STS"]
    for index, node in enumerate(nodes(document).values(), 11):
        assert list(node["pci"]["network"]) == ["nic1"]
        component = node["pci"]["network"]["nic1"]
        assert component["model"] == "NIC_ConnectX_6"
        assert component["interfaces"] == {
            "iface1": {"device": "eth1", "connection": "conn-eth1", "binding": "ptp-data"},
            "iface2": {"device": "eth2", "connection": "conn-eth2", "binding": "storage",
                       "ipv4": {"address": f"192.0.2.{index}/24", "gateway": "192.0.2.1", "dns": "192.0.2.53"}},
        }


def test_unattached_point_to_point_is_valid_and_not_selected(source, config):
    for node in cluster(source)["spec"]["nodes"]:
        node["networkAttachments"] = []
    model = InfrastructureModel.from_documents(source)
    assert len(model.validate().warnings) == 2
    assert networks(render_fabric(model, config=config)) == {}


@pytest.mark.parametrize("example", ["fabric-like", "multi-site"])
def test_unattached_point_to_point_does_not_affect_unrelated_cluster_networks(config, example):
    source = [doc.data for doc in load_documents(ROOT / "examples" / example)]
    expected = render(source, config)
    source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Network",
                   "metadata": {"name": "unused-ptp"},
                   "spec": {"layer": "layer2", "connectivity": "point-to-point"}})
    document = render(source, config)
    assert document == expected
    assert "unused-ptp" not in {net["name"] for net in networks(document).values()}


def test_one_endpoint_is_valid_generic_intent_but_provider_requires_two(source, config):
    cluster(source)["spec"]["nodes"][1]["networkAttachments"] = []
    model = InfrastructureModel.from_documents(source)
    assert len(model.validate().warnings) == 2
    with pytest.raises(FabricTranslationError, match="exactly two rendered NetworkAttachments; found 1") as caught:
        render_fabric(model, config=config)
    assert caught.value.entity == "network/ptp-data"
    assert caught.value.path == "/spec/connectivity"


def split_clusters(source):
    selected = cluster(source)
    other = deepcopy(selected)
    other["metadata"]["name"] = "other"
    other["spec"]["nodes"] = [selected["spec"]["nodes"].pop()]
    source.append(other)


@pytest.mark.parametrize("selection", ["l2ptp", "other"])
def test_selected_cluster_cannot_render_half_of_global_ptp_network(source, config, selection):
    split_clusters(source)
    model = InfrastructureModel.from_documents(source)
    assert len(model.of_kind("NetworkAttachment")) == 2
    with pytest.raises(FabricTranslationError, match=f"selected Cluster cluster/{selection} does not contain all L2PTP endpoints") as caught:
        render_fabric(model, cluster=selection, config=config)
    assert "renders 1 of 2 NetworkAttachments" in caught.value.reason


def test_multipoint_cross_cluster_behavior_still_uses_selected_endpoints_only(source, config):
    split_clusters(source)
    network(source)["spec"]["connectivity"] = "multipoint"
    assert networks(render(source, config, cluster="l2ptp"))["net1"]["type"] == "L2Bridge"


def test_ptp_used_only_by_another_cluster_is_not_selected(source, config):
    selected = deepcopy(cluster(source))
    selected["metadata"]["name"] = "selected"
    selected["spec"]["nodes"] = selected["spec"]["nodes"][:1]
    selected["spec"]["nodes"][0]["networkAttachments"] = []
    source.append(selected)
    assert networks(render(source, config, cluster="selected")) == {}


@pytest.mark.parametrize("alias", [False, True])
def test_same_effective_fabric_site_fails_even_with_two_declared_sites(source, config, alias):
    if alias:
        config_data = yaml.safe_load(CONFIG.read_text())
        config_data["sites"]["site/site-b"] = "SRI"
        config = FabricAdapterConfig.from_dict(config_data)
    else:
        cluster(source)["spec"]["nodes"][1].pop("placement")
    with pytest.raises(FabricTranslationError, match="exactly two distinct effective FABRIC sites; found 1"):
        render(source, config)


@pytest.mark.parametrize("basic_indices", [(0,), (1,), (0, 1)])
def test_basic_interfaces_cannot_be_upgraded_for_l2ptp(source, config, basic_indices):
    for index in basic_indices:
        endpoints(source)[index]["interfaceRequirements"] = {"type": "ethernet"}
    with pytest.raises(FabricTranslationError, match="supported dedicated interface.*resolved NIC_Basic"):
        render(source, config)


def test_unsupported_dedicated_model_is_not_a_ptp_endpoint(source, config):
    endpoints(source)[1]["interfaceRequirements"]["adapter"]["model"] = "ConnectX-7"
    with pytest.raises(FabricTranslationError, match="requires exact model ConnectX-5 or ConnectX-6"):
        render(source, config)


@pytest.mark.parametrize("indices", [(0,), (1,), (0, 1)])
@pytest.mark.parametrize("inherited", [False, True])
def test_connectx5_does_not_expand_l2ptp_eligibility(source, config, indices, inherited):
    if inherited:
        source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Network",
                       "metadata": {"name": "other"}, "spec": {"layer": "layer2"}})
    for index in indices:
        endpoint = endpoints(source)[index]
        endpoint["interfaceRequirements"]["adapter"]["model"] = "ConnectX-5"
        if inherited:
            endpoint["adapterGroup"] = "hs1"
            other = deepcopy(endpoint)
            other.update(name="other", networkRef="network/other")
            del endpoint["interfaceRequirements"]["adapter"]
            cluster(source)["spec"]["nodes"][index]["networkAttachments"].append(other)
    with pytest.raises(FabricTranslationError, match="L2PTP requires.*NIC_ConnectX_6.*resolved NIC_ConnectX_5") as caught:
        render(source, config)
    assert caught.value.path.endswith("/networkAttachments/0/interfaceRequirements")


def test_more_than_two_attachments_still_fails_core(source, config):
    third = deepcopy(cluster(source)["spec"]["nodes"][0])
    third["name"] = "vm-c"
    cluster(source)["spec"]["nodes"].append(third)
    model = InfrastructureModel.from_documents(source)
    with pytest.raises(ValidationError, match="network_endpoint_cardinality"):
        model.validate()
    with pytest.raises(FabricTranslationError, match="network_endpoint_cardinality"):
        render_fabric(model, config=config)


@pytest.mark.parametrize("field,value", [
    ("prefixes", []), ("prefixes", ["192.0.2.0/24"]),
    ("defaultGateways", []), ("defaultGateways", [{"prefix": "192.0.2.0/24", "address": "192.0.2.1"}]),
    ("dns", {"servers": []}), ("dns", {"servers": ["192.0.2.53"]}),
    ("dns", {"servers": ["2001:db8::53"]}),
])
def test_ptp_network_l3_fields_including_empty_configuration_fail(source, config, field, value):
    spec = network(source)["spec"]
    spec[field] = value
    if field == "defaultGateways" and value:
        spec["prefixes"] = ["192.0.2.0/24"]
    model = InfrastructureModel.from_documents(source)
    model.validate()
    with pytest.raises(FabricTranslationError, match="L2PTP guest VLAN/IP configuration is not yet supported") as caught:
        render_fabric(model, config=config)
    assert caught.value.entity == "network/ptp-data"


@pytest.mark.parametrize("addresses,prefixes", [
    ([], None), (["192.0.2.11"], ["192.0.2.0/24"]),
    (["2001:db8::11"], ["2001:db8::/64"]),
])
@pytest.mark.parametrize("endpoint", [0, 1])
def test_ptp_attachment_addresses_including_empty_list_fail(source, config, addresses, prefixes, endpoint):
    if prefixes is not None:
        network(source)["spec"]["prefixes"] = prefixes
    endpoints(source)[endpoint]["addresses"] = addresses
    model = InfrastructureModel.from_documents(source)
    model.validate()
    with pytest.raises(FabricTranslationError, match="L2PTP guest VLAN/IP configuration is not yet supported") as caught:
        render_fabric(model, config=config)
    assert caught.value.path == f"/spec/nodes/{endpoint}/networkAttachments/0/addresses"


def test_two_endpoints_on_one_group_are_still_two_logical_endpoints(source, config):
    first, second = cluster(source)["spec"]["nodes"]
    first["networkAttachments"][0]["adapterGroup"] = "hs1"
    other = deepcopy(second["networkAttachments"][0])
    other.update(name="second-port", adapterGroup="hs1")
    first["networkAttachments"].append(other)
    second["networkAttachments"] = []
    model = InfrastructureModel.from_documents(source)
    assert len(model.of_kind("NetworkAttachment")) == 2
    with pytest.raises(FabricTranslationError, match="exactly two distinct effective FABRIC sites; found 1"):
        render_fabric(model, config=config)


@pytest.mark.parametrize("connectivity", [None, "multipoint"])
@pytest.mark.parametrize("same_site,service", [(True, "L2Bridge"), (False, "L2STS")])
def test_two_dedicated_endpoints_do_not_imply_l2ptp(source, config, connectivity, same_site, service):
    if connectivity is None:
        network(source)["spec"].pop("connectivity")
    else:
        network(source)["spec"]["connectivity"] = connectivity
    if same_site:
        cluster(source)["spec"]["nodes"][1].pop("placement")
    assert networks(render(source, config))["net1"]["type"] == service


def test_partial_layer3_still_unsupported(source, config):
    network(source)["spec"]["layer"] = "layer3"
    with pytest.raises(FabricTranslationError, match="requires explicit addressFamily"):
        render(source, config)


@pytest.mark.parametrize("entrypoint", ["module", "script"])
def test_l2ptp_cli_fixture_renders_byte_identically_twice(tmp_path, entrypoint):
    command = ([sys.executable, "-m", "infra_model"] if entrypoint == "module"
               else [str(Path(sys.executable).with_name("infra-model"))])
    command += ["render", "fabric", str(FIXTURE), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout
    assert first.stderr.count(b"UNKNOWN") == second.stderr.count(b"UNKNOWN") == 2
    assert b"UNKNOWN" not in first.stdout
    assert networks(yaml.safe_load(first.stdout)) == {"net1": {"name": "ptp-data", "type": "L2PTP"}}


def test_incomplete_ptp_cli_emits_only_error(source, tmp_path, capsys):
    split_clusters(source)
    path = tmp_path / "incomplete.yaml"
    path.write_text(yaml.safe_dump_all(source))
    assert main(["render", "fabric", str(path), "--config", str(CONFIG), "--cluster", "l2ptp"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not contain all L2PTP endpoints" in captured.err
