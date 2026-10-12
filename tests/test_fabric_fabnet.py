from collections import Counter
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from infra_model.adapters.fabric.translator import _translate
from infra_model.cli import main
from infra_model.loader import load_documents
from conftest import ROOT
from test_fabric_adapter import cluster, interface, network, networks, node, nodes, render


FIXTURES = ROOT / "tests/fixtures"
CONFIG = FIXTURES / "fabric-fabnet-config.yaml"
ACCEPTANCE = [("ipv4", "_slice_topology_7-1.yml", "IPv4", 2, 1),
              ("ipv6", "_slice_topology_7-2.yml", "IPv6", 2, 1),
              ("multisite", "m5-v4.yml", "IPv4", 6, 3)]


@pytest.fixture
def config_data():
    return yaml.safe_load(CONFIG.read_text())


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


@pytest.fixture
def source():
    return [doc.data for doc in load_documents(FIXTURES / "fabric-fabnet-ipv4.yaml")]


def add_site(source, name="other"):
    source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Site", "metadata": {"name": name}, "spec": {}})
    network(source)["spec"]["siteRefs"].append(f"site/{name}")


def assert_failure(source, config, reason, path, *, cluster_name=None):
    with pytest.raises(FabricTranslationError, match=reason) as caught:
        render(source, config, cluster=cluster_name)
    assert caught.value.path == path
    assert caught.value.source.startswith("memory#document=")
    assert caught.value.entity


@pytest.mark.parametrize("fixture,historical,service,node_count,network_count", ACCEPTANCE)
def test_acceptance_fixtures(fixture, historical, service, node_count, network_count, config):
    model = InfrastructureModel.load(FIXTURES / f"fabric-fabnet-{fixture}.yaml")
    before = model.documents
    first = render_fabric(model, config=config)
    second = render_fabric(model, config=config)
    assert yaml.safe_dump(first, sort_keys=False) == yaml.safe_dump(second, sort_keys=False)
    assert model.documents == before
    assert len(nodes(first)) == node_count
    assert len(networks(first)) == network_count
    assert list(networks(first)) == [f"net{i}" for i in range(1, network_count + 1)]
    for net in networks(first).values():
        assert net == {"name": net["name"], "type": service, "subnet": {}}
    counts = Counter()
    for item in nodes(first).values():
        components = item["pci"]["network"]
        assert list(components) == ["nic1"]
        component = components["nic1"]
        assert component["name"] == "nic1" and component["model"] == "NIC_Basic"
        assert list(component["interfaces"]) == ["iface1"]
        iface = component["interfaces"]["iface1"]
        assert iface == {"device": "eth1", "connection": "conn-eth1", "binding": iface["binding"], "ipv4": {}, "ipv6": {}}
        counts[iface["binding"]] += 1
    assert counts == Counter({net["name"]: 2 for net in networks(first).values()})
    if fixture == "multisite":
        assert [n["site"] for n in nodes(first).values()] == ["UCSD", "LOSA", "SALT"] * 2
        assert [n["worker"] for n in nodes(first).values()] == [
            "ucsd-w3.fabric-testbed.net", "losa-w1.fabric-testbed.net", "salt-w1.fabric-testbed.net"] * 2
        assert [n["capacity"]["os"] for n in nodes(first).values()] == [
            "default_rocky_9", "default_ubuntu_24", "default_debian_12"] + ["default_rocky_9"] * 3
    else:
        assert all(n["site"] == "SRI" and n["worker"] == "" for n in nodes(first).values())
    plan = _translate(model, cluster=None, config=config)
    assert all(n.emit_empty_subnet and n.prefixes == () and n.default_gateways is None and n.dns_servers is None
               for n in plan.networks)
    assert all(i.emit_empty_address_families and i.addresses == ()
               for n in plan.nodes for c in n.components for i in c.interfaces)


def historical_semantics(document):
    """Compare placement and bindings by service order, independent of generated names."""
    net_list = list(networks(document).values())
    identities = {net["name"]: index for index, net in enumerate(net_list)}
    return {
        "networks": [{"type": net["type"], "subnet": net["subnet"]} for net in net_list],
        "nodes": [{"site": n["site"], "worker": n["worker"], "capacity": n["capacity"],
                   "components": [{"model": c["model"], "interfaces": [
                       {**i, "binding": identities[i["binding"]]} for i in c["interfaces"].values()]}
                       for c in n["pci"]["network"].values()]} for n in nodes(document).values()],
    }


@pytest.mark.parametrize("fixture,historical,service,node_count,network_count", ACCEPTANCE)
def test_actual_historical_yaml(fixture, historical, service, node_count, network_count, config):
    reference_root = Path(os.environ.get("FABRIC_NOTEBOOK_MODELS", str(
        ROOT.parents[1] / "fabric/fabric-generic-cluster-notebooks/model")))
    path = reference_root / historical
    if not path.is_file():
        pytest.skip("read-only historical notebooks unavailable; set FABRIC_NOTEBOOK_MODELS")
    original = yaml.safe_load(path.read_text())
    generated = render_fabric(InfrastructureModel.load(FIXTURES / f"fabric-fabnet-{fixture}.yaml"), config=config)
    assert historical_semantics(generated) == historical_semantics(original)


@pytest.mark.parametrize("fixture,historical,service,node_count,network_count", ACCEPTANCE)
@pytest.mark.parametrize("entrypoint", ["module", "script"])
def test_acceptance_cli_twice(fixture, historical, service, node_count, network_count, entrypoint, tmp_path):
    command = ([sys.executable, "-m", "infra_model"] if entrypoint == "module"
               else [str(Path(sys.executable).with_name("infra-model"))])
    command += ["render", "fabric", str(FIXTURES / f"fabric-fabnet-{fixture}.yaml"), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout
    assert b"UNKNOWN" not in first.stdout
    assert len(nodes(yaml.safe_load(first.stdout))) == node_count


@pytest.mark.parametrize("family,service", [("ipv4", "IPv4"), ("ipv6", "IPv6")])
@pytest.mark.parametrize("count", [1, 3])
def test_no_exactly_two_endpoint_requirement(source, config, family, service, count):
    network(source)["spec"]["addressFamily"] = family
    original = deepcopy(node(source))
    cluster(source)["spec"]["nodes"] = [{**deepcopy(original), "name": f"vm-{i}"} for i in range(count)]
    result = render(source, config)
    assert len(nodes(result)) == count
    assert networks(result)["net1"]["type"] == service


def test_omitted_connectivity_and_renaming_do_not_select_service(source, config):
    network(source)["spec"].pop("connectivity")
    network(source)["metadata"]["name"] = "misleading-ipv6-public-name"
    for n in cluster(source)["spec"]["nodes"]:
        n["networkAttachments"][0]["networkRef"] = "network/misleading-ipv6-public-name"
    before = deepcopy(source)
    result = render(source, config)
    assert networks(result) == {"net1": {"name": "misleading-ipv6-public-name", "type": "IPv4", "subnet": {}}}
    assert interface(result)["binding"] == "misleading-ipv6-public-name"
    assert source == before


@pytest.mark.parametrize("occupied", [False, True])
def test_allowed_scope_does_not_split_services(source, config_data, occupied):
    add_site(source)
    if occupied:
        config_data["sites"]["site/other"] = "SRI"
        cluster(source)["spec"]["nodes"][1]["placement"] = {"siteRef": "site/other"}
    result = render(source, FabricAdapterConfig.from_dict(config_data))
    assert len(networks(result)) == 1
    assert all(n["site"] == "SRI" for n in nodes(result).values())


@pytest.mark.parametrize("l2_first", [False, True])
def test_l2_coexists_with_one_fabnet_preserving_order(source, config, l2_first):
    source.insert(0, {"apiVersion": "infra.model/v0alpha1", "kind": "Network",
                      "metadata": {"name": "management"}, "spec": {"layer": "layer2"}})
    attachment = {"name": "management", "networkRef": "network/management", "interfaceRequirements": {"type": "ethernet"}}
    attachments = node(source)["networkAttachments"]
    attachments.insert(0 if l2_first else 1, attachment)
    result = render(source, config)
    expected = ["L2Bridge", "IPv4"] if l2_first else ["IPv4", "L2Bridge"]
    assert [n["type"] for n in networks(result).values()] == expected
    components = nodes(result)["node1"]["pci"]["network"]
    assert list(components) == ["nic1", "nic2"]
    for index, (component, source_attachment) in enumerate(zip(components.values(), attachments), 1):
        iface = component["interfaces"]["iface1"]
        assert iface["device"] == f"eth{index}" and iface["connection"] == f"conn-eth{index}"
        assert iface["binding"] == source_attachment["networkRef"].removeprefix("network/")
        assert ("ipv4" in iface) == (source_attachment["name"] == "dataplane")


@pytest.mark.parametrize("field", ["addressFamily", "addressAllocation", "reachability"])
def test_partial_intent_fails(source, config, field):
    network(source)["spec"].pop(field)
    # Missing family with provider-managed allocation is rejected by core first.
    path = "/spec/addressAllocation" if field == "addressFamily" else f"/spec/{field}"
    assert_failure(source, config, field, path)


@pytest.mark.parametrize("spec,reason,path", [
    ({"layer": "layer3"}, "addressFamily", "/spec/addressFamily"),
    ({"layer": "layer3", "prefixes": ["192.0.2.0/24"]}, "addressFamily", "/spec/addressFamily"),
    ({"layer": "layer3", "addressFamily": "ipv4"}, "addressAllocation", "/spec/addressAllocation"),
    ({}, "explicit layer2 or supported layer3", "/spec/layer"),
])
def test_ambiguous_intent_fails_without_inference(source, config, spec, reason, path):
    network(source)["spec"] = spec
    assert_failure(source, config, reason, path)


@pytest.mark.parametrize("family", ["ipv4", "ipv6"])
def test_public_is_generic_valid_but_phase_b(source, config, family):
    network(source)["spec"].update(addressFamily=family, reachability="public")
    InfrastructureModel.from_documents(source).validate()
    assert_failure(source, config, "external FABNet realization, deferred to Phase B", "/spec/reachability")


def test_layer3_point_to_point_fails(source, config):
    network(source)["spec"]["connectivity"] = "point-to-point"
    InfrastructureModel.from_documents(source).validate()
    assert_failure(source, config, "Layer-3 point-to-point", "/spec/connectivity")


def test_multisite_network_fails(source, config_data):
    add_site(source)
    config_data["sites"]["site/other"] = "LOSA"
    cluster(source)["spec"]["nodes"][1]["placement"] = {"siteRef": "site/other"}
    assert_failure(source, FabricAdapterConfig.from_dict(config_data), "exactly one.*found 2", "/spec")


def test_shared_network_across_clusters_fails(source, config):
    other = deepcopy(cluster(source))
    other["metadata"]["name"] = "other"
    source.append(other)
    assert_failure(source, config, "does not contain all FABNet endpoints.*renders 2 of 4", "/spec", cluster_name="research")


def test_unselected_network_is_ignored(source, config):
    other = deepcopy(cluster(source))
    other["metadata"]["name"] = "other"
    for n in other["spec"]["nodes"]:
        n["networkAttachments"][0]["networkRef"] = "network/unused"
    unused = deepcopy(network(source))
    unused["metadata"]["name"] = "unused"
    unused["spec"]["reachability"] = "public"
    source.extend([other, unused])
    assert len(networks(render(source, config, cluster="research"))) == 1


@pytest.mark.parametrize("model", ["ConnectX-5", "ConnectX-6"])
@pytest.mark.parametrize("grouped", [False, True, "inherited"])
def test_dedicated_fabnet_fails(source, config, model, grouped):
    a = node(source)["networkAttachments"][0]
    a["interfaceRequirements"]["adapter"] = {"class": "standard", "model": model}
    if grouped:
        a["adapterGroup"] = "ports"
    if grouped == "inherited":
        selector = a["interfaceRequirements"].pop("adapter")
        node(source)["networkAttachments"].append({
            "name": "management", "networkRef": "network/management", "adapterGroup": "ports",
            "interfaceRequirements": {"type": "ethernet", "adapter": selector}})
        source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Network",
                       "metadata": {"name": "management"}, "spec": {"layer": "layer2"}})
    assert_failure(source, config, f"requires NIC_Basic.*resolved NIC_{model.replace('-', '_')}",
                   "/spec/nodes/0/networkAttachments/0/interfaceRequirements")


@pytest.mark.parametrize("different_network", [False, True])
def test_more_than_one_fabnet_attachment_per_vm_fails(source, config, different_network):
    a = deepcopy(node(source)["networkAttachments"][0])
    a["name"] = "second"
    if different_network:
        net = deepcopy(network(source))
        net["metadata"]["name"] = "second"
        net["spec"]["addressFamily"] = "ipv6"
        source.append(net)
        a["networkRef"] = "network/second"
    node(source)["networkAttachments"].append(a)
    InfrastructureModel.from_documents(source).validate()
    assert_failure(source, config, "at most one FABNet attachment per VM", "/spec/nodes/0/networkAttachments")


@pytest.mark.parametrize("field,value", [
    ("prefixes", []), ("prefixes", ["192.0.2.0/24"]),
    ("defaultGateways", []),
    ("defaultGateways", [{"prefix": "192.0.2.0/24", "address": "192.0.2.1"}]),
    ("dns", {"servers": []}), ("dns", {"servers": ["192.0.2.53"]}),
    ("dns", {"servers": ["2001:db8::53"]}),
])
def test_authored_network_addressing_fails(source, config, field, value):
    network(source)["spec"][field] = value
    path = f"/spec/{field}"
    reason = "provider_managed_addressing" if field == "prefixes" else f"Network {field} to be omitted"
    if field == "defaultGateways" and value:
        reason = "gateway_prefix_not_declared"
        path += "/0/prefix"
    assert_failure(source, config, reason, path)


@pytest.mark.parametrize("addresses", [[], ["192.0.2.10"]])
def test_authored_attachment_addressing_fails(source, config, addresses):
    node(source)["networkAttachments"][0]["addresses"] = addresses
    assert_failure(source, config, "provider_managed_addressing", "/spec/nodes/0/networkAttachments/0/addresses")


def test_unresolved_site_mapping_fails(source, config_data):
    config_data["sites"] = {}
    assert_failure(source, FabricAdapterConfig.from_dict(config_data), "no adapter site mapping", "/spec/nodes/0/placement/siteRef")


def test_no_effective_site_inference(source, config):
    cluster(source)["spec"].pop("siteRef")
    assert_failure(source, config, "no effective site", "/spec/nodes/0/placement/siteRef")


@pytest.mark.parametrize("requirements,reason,suffix", [
    ({"type": "ethernet", "minSpeed": {"value": 100, "unit": "Gbps"}}, "minSpeed", "/minSpeed"),
    ({"type": "ethernet", "features": ["rdma"]}, "feature interface", "/features"),
    ({"type": "ethernet", "adapter": {"class": "dpu"}}, "only standard adapter", "/adapter/class"),
    ({"type": "infiniband"}, "explicit Ethernet", ""),
    (None, "explicit Ethernet", ""),
])
def test_unsupported_interface_requirements(source, config, requirements, reason, suffix):
    a = node(source)["networkAttachments"][0]
    if requirements is None:
        a.pop("interfaceRequirements")
    else:
        a["interfaceRequirements"] = requirements
    assert_failure(source, config, reason, "/spec/nodes/0/networkAttachments/0/interfaceRequirements" + suffix)


@pytest.mark.parametrize("failure", ["public", "shared", "multiple", "addressing"])
def test_invalid_cli_emits_no_partial_yaml(source, tmp_path, capsys, failure):
    if failure == "public":
        network(source)["spec"]["reachability"] = "public"
    elif failure == "shared":
        other = deepcopy(cluster(source))
        other["metadata"]["name"] = "other"
        source.append(other)
    elif failure == "multiple":
        node(source)["networkAttachments"].append({**deepcopy(node(source)["networkAttachments"][0]), "name": "second"})
    else:
        network(source)["spec"]["dns"] = {"servers": []}
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump_all(source))
    assert main(["render", "fabric", str(path), "--config", str(CONFIG), "--cluster", "research"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FABRIC translation:" in captured.err
