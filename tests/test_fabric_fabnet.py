from collections import Counter
from copy import deepcopy
import hashlib
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
              ("ipv4ext", "_slice_topology_7-3.yml", "IPv4Ext", 2, 1),
              ("ipv6ext", "_slice_topology_7-4.yml", "IPv6Ext", 2, 1),
              ("multisite", "m5-v4.yml", "IPv4", 6, 3)]
SERVICE_INTENTS = [("ipv4", "provider-internal", "IPv4"),
                   ("ipv6", "provider-internal", "IPv6"),
                   ("ipv4", "public", "IPv4Ext"),
                   ("ipv6", "public", "IPv6Ext")]


@pytest.fixture
def config_data():
    return yaml.safe_load(CONFIG.read_text())


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


@pytest.fixture(params=SERVICE_INTENTS, ids=[service for _, _, service in SERVICE_INTENTS])
def intent(request):
    return request.param


@pytest.fixture
def source(intent):
    documents = [doc.data for doc in load_documents(FIXTURES / "fabric-fabnet-ipv4.yaml")]
    family, reachability, _ = intent
    network(documents)["spec"].update(addressFamily=family, reachability=reachability)
    return documents


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
    document = yaml.safe_load(first.stdout)
    assert len(nodes(document)) == node_count
    assert len(networks(document)) == network_count
    assert {n["type"] for n in networks(document).values()} == {service}


# CLI hashes captured on Phase A before adding external FABNet support.
@pytest.mark.parametrize("fixture,digest", [
    ("ipv4", "29728520d1afef152b7a5ee0de6681ede1199563f4f86708ccfb49ec398c8da5"),
    ("ipv6", "42a3b47782b623b69603d41fede9621913675f973dad7eef71aa1a6469a3cc56"),
    ("multisite", "eb5ce40cdc5f1b5737debeaf7335df5c41d394b7043452be0c8b119116bda358"),
])
def test_internal_fabnet_render_bytes_remain_unchanged(fixture, digest, capsys):
    outputs = []
    for _ in range(2):
        assert main(["render", "fabric", str(FIXTURES / f"fabric-fabnet-{fixture}.yaml"),
                     "--config", str(CONFIG)]) == 0
        outputs.append(capsys.readouterr().out.encode("utf-8"))
    assert outputs[0] == outputs[1]
    assert hashlib.sha256(outputs[0]).hexdigest() == digest


@pytest.mark.parametrize("count", [1, 3])
def test_no_exactly_two_endpoint_requirement(source, config, intent, count):
    original = deepcopy(node(source))
    cluster(source)["spec"]["nodes"] = [{**deepcopy(original), "name": f"vm-{i}"} for i in range(count)]
    result = render(source, config)
    assert len(nodes(result)) == count
    assert networks(result)["net1"]["type"] == intent[2]


@pytest.mark.parametrize("name", ["misleading-ipv6-public-name", "misleading-ipv4-internal-name"])
def test_omitted_connectivity_and_renaming_do_not_select_service(source, config, intent, name):
    network(source)["spec"].pop("connectivity")
    network(source)["metadata"]["name"] = name
    for n in cluster(source)["spec"]["nodes"]:
        n["networkAttachments"][0]["networkRef"] = f"network/{name}"
    before = deepcopy(source)
    result = render(source, config)
    assert networks(result) == {"net1": {"name": name, "type": intent[2], "subnet": {}}}
    assert interface(result)["binding"] == name
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
def test_l2_coexists_with_one_fabnet_preserving_order(source, config, intent, l2_first):
    source.insert(0, {"apiVersion": "infra.model/v0alpha1", "kind": "Network",
                      "metadata": {"name": "management"}, "spec": {"layer": "layer2"}})
    attachment = {"name": "management", "networkRef": "network/management", "interfaceRequirements": {"type": "ethernet"}}
    attachments = node(source)["networkAttachments"]
    attachments.insert(0 if l2_first else 1, attachment)
    result = render(source, config)
    expected = ["L2Bridge", intent[2]] if l2_first else [intent[2], "L2Bridge"]
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
    ({"layer": "layer3", "reachability": "public", "prefixes": ["8.8.8.0/24"]}, "addressFamily", "/spec/addressFamily"),
    ({"layer": "layer3", "addressFamily": "ipv4"}, "addressAllocation", "/spec/addressAllocation"),
    ({}, "explicit layer2 or supported layer3", "/spec/layer"),
])
def test_ambiguous_intent_fails_without_inference(source, config, spec, reason, path):
    network(source)["spec"] = spec
    assert_failure(source, config, reason, path)


def test_explicit_service_selection_is_generic_valid(source, config, intent):
    InfrastructureModel.from_documents(source).validate()
    assert networks(render(source, config))["net1"] == {
        "name": "research-network", "type": intent[2], "subnet": {}}


@pytest.mark.parametrize("layer", [None, "layer2"])
def test_complete_intent_requires_explicit_layer3(source, config, layer):
    if layer is None:
        network(source)["spec"].pop("layer")
    else:
        network(source)["spec"]["layer"] = layer
    assert_failure(source, config, "network_layer3_required", "/spec/addressFamily")


@pytest.mark.parametrize("field,value", [
    ("addressFamily", "dual-stack"), ("addressAllocation", "static"), ("reachability", "private"),
])
def test_unsupported_intent_does_not_fall_back(source, config, field, value):
    network(source)["spec"][field] = value
    assert_failure(source, config, "core validation failed", f"/spec/{field}")


def test_independent_multisite_services(config, intent):
    documents = [doc.data for doc in load_documents(FIXTURES / "fabric-fabnet-multisite.yaml")]
    for doc in documents:
        if doc["kind"] == "Network":
            doc["spec"].update(addressFamily=intent[0], reachability=intent[1])
    result = render(documents, config)
    assert len(networks(result)) == 3
    assert all(net["type"] == intent[2] and net["subnet"] == {} for net in networks(result).values())
    assert [n["site"] for n in nodes(result).values()] == ["UCSD", "LOSA", "SALT"] * 2
    assert [n["worker"] for n in nodes(result).values()] == [
        "ucsd-w3.fabric-testbed.net", "losa-w1.fabric-testbed.net", "salt-w1.fabric-testbed.net"] * 2
    assert all(len({n["site"] for n in nodes(result).values()
                    if any(i["binding"] == net["name"] for c in n["pci"]["network"].values()
                           for i in c["interfaces"].values())}) == 1 for net in networks(result).values())


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
    unused["spec"].pop("addressAllocation")
    source.extend([other, unused])
    assert len(networks(render(source, config, cluster="research"))) == 1


@pytest.mark.parametrize("model", ["ConnectX-5", "ConnectX-6"])
@pytest.mark.parametrize("grouped", [False, True, "inherited"])
def test_dedicated_fabnet_reuses_component_realization(source, config, intent, model, grouped):
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
    before = deepcopy(source)
    result = render(source, config)
    expected_interfaces = {"iface1": {
        "device": "eth1", "connection": "conn-eth1", "binding": "research-network", "ipv4": {}, "ipv6": {}}}
    if grouped == "inherited":
        expected_interfaces["iface2"] = {"device": "eth2", "connection": "conn-eth2", "binding": "management"}
    assert nodes(result)["node1"]["pci"]["network"] == {"nic1": {
        "name": "nic1", "model": f"NIC_{model.replace('-', '_')}", "interfaces": expected_interfaces}}
    assert networks(result)["net1"] == {"name": "research-network", "type": intent[2], "subnet": {}}
    assert nodes(result)["node2"]["pci"]["network"]["nic1"]["model"] == "NIC_Basic"
    assert source == before


@pytest.mark.parametrize("different_network", [False, True])
@pytest.mark.parametrize("second_reachability", ["provider-internal", "public"])
def test_more_than_one_fabnet_attachment_per_vm_fails(source, config, different_network, second_reachability):
    a = deepcopy(node(source)["networkAttachments"][0])
    a["name"] = "second"
    if different_network:
        net = deepcopy(network(source))
        net["metadata"]["name"] = "second"
        net["spec"]["addressFamily"] = "ipv6"
        net["spec"]["reachability"] = second_reachability
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


@pytest.mark.parametrize("addresses", [[], ["192.0.2.10"], ["2001:db8::10"]])
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
    ({"type": "ethernet", "adapter": {"class": "other", "model": "Alveo U280"}}, "only standard adapter", "/adapter/class"),
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


@pytest.mark.parametrize("failure", ["partial", "shared", "multiple", "addressing"])
def test_invalid_cli_emits_no_partial_yaml(source, tmp_path, capsys, failure):
    if failure == "partial":
        network(source)["spec"].pop("addressAllocation")
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
