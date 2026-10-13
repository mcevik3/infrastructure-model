from copy import deepcopy
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from conftest import ROOT
from test_fabric_adapter import cluster, network, networks, node, nodes, render
from test_fabric_fabnet import config, intent, source  # shared four-service fixtures


FIXTURE = ROOT / "tests/fixtures/fabric-fabnet-dedicated-nic.yaml"
CONFIG = ROOT / "adapter-configs/fabric.yaml"


@pytest.fixture(params=["ConnectX-5", "ConnectX-6"])
def nic_model(request):
    return request.param


@pytest.fixture
def dedicated_source(source, nic_model):
    for item in cluster(source)["spec"]["nodes"]:
        attachment = item["networkAttachments"][0]
        attachment["adapterGroup"] = "ports"
        attachment["interfaceRequirements"]["adapter"] = {"class": "standard", "model": nic_model}
    return source


def add_l2(source):
    source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Network",
                   "metadata": {"name": "storage"}, "spec": {
                       "layer": "layer2", "prefixes": ["192.0.2.0/24"],
                       "defaultGateways": [{"prefix": "192.0.2.0/24", "address": "192.0.2.1"}],
                       "dns": {"servers": ["192.0.2.53"]}}})
    for index, item in enumerate(cluster(source)["spec"]["nodes"], 10):
        item["networkAttachments"].append({
            "name": "storage", "networkRef": "network/storage", "adapterGroup": "ports",
            "interfaceRequirements": {"type": "ethernet"}, "addresses": [f"192.0.2.{index}"]})


@pytest.mark.parametrize("l2_service", ["L2Bridge", "L2STS"])
@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_ports_keep_service_addressing_and_order(dedicated_source, config, intent, nic_model, l2_service, reverse):
    add_l2(dedicated_source)
    if l2_service == "L2STS":
        dedicated_source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Site",
                                 "metadata": {"name": "metro"}, "spec": {}})
        peer = deepcopy(node(dedicated_source))
        peer.update(name="remote-storage", placement={"siteRef": "site/metro"})
        peer["networkAttachments"] = [{"name": "storage", "networkRef": "network/storage",
                                      "interfaceRequirements": {"type": "ethernet"}}]
        cluster(dedicated_source)["spec"]["nodes"].append(peer)
    for item in cluster(dedicated_source)["spec"]["nodes"][:2]:
        if reverse:
            item["networkAttachments"].reverse()
    before = deepcopy(dedicated_source)
    model = InfrastructureModel.from_documents(dedicated_source)
    first, second = [yaml.safe_dump(render_fabric(model, config=config), sort_keys=False) for _ in range(2)]
    assert first == second
    assert "vlan" not in first.lower()
    result = yaml.safe_load(first)
    assert [net["type"] for net in networks(result).values()] == (
        [l2_service, intent[2]] if reverse else [intent[2], l2_service])
    for index, key in enumerate(["node1", "node2"], 10):
        expected = [
            {"binding": "research-network", "ipv4": {}, "ipv6": {}},
            {"binding": "storage", "ipv4": {
                "address": f"192.0.2.{index}/24", "gateway": "192.0.2.1", "dns": "192.0.2.53"}},
        ]
        if reverse:
            expected.reverse()
        assert nodes(result)[key]["pci"]["network"] == {"nic1": {
            "name": "nic1", "model": f"NIC_{nic_model.replace('-', '_')}", "interfaces": {
                f"iface{i}": {"device": f"eth{i}", "connection": f"conn-eth{i}", **data}
                for i, data in enumerate(expected, 1)}}}
    fabnet = next(net for net in networks(result).values() if net["name"] == "research-network")
    assert fabnet == {"name": "research-network", "type": intent[2], "subnet": {}}
    assert dedicated_source == before


@pytest.mark.parametrize("grouped", [False, True])
def test_interleaved_basic_and_dedicated_naming(dedicated_source, config, nic_model, grouped):
    add_l2(dedicated_source)
    attachments = node(dedicated_source)["networkAttachments"]
    if not grouped:
        for attachment in attachments:
            attachment.pop("adapterGroup")
            attachment["interfaceRequirements"]["adapter"] = {"class": "standard", "model": nic_model}
    attachments.insert(1, {"name": "basic", "networkRef": "network/storage",
                           "interfaceRequirements": {"type": "ethernet"}})
    components = nodes(render(dedicated_source, config))["node1"]["pci"]["network"]
    assert list(components) == (["nic1", "nic2"] if grouped else ["nic1", "nic2", "nic3"])
    target = f"NIC_{nic_model.replace('-', '_')}"
    assert [c["model"] for c in components.values()] == ([target, "NIC_Basic"] if grouped else [target, "NIC_Basic", target])
    assert [list(c["interfaces"]) for c in components.values()] == (
        [["iface1", "iface2"], ["iface1"]] if grouped else [["iface1"]] * 3)
    assert [[i["device"] for i in c["interfaces"].values()] for c in components.values()] == (
        [["eth1", "eth3"], ["eth2"]] if grouped else [["eth1"], ["eth2"], ["eth3"]])
    assert all(key == c["name"] for key, c in components.items())


def test_fabnet_counts_toward_two_port_limit(dedicated_source, config):
    add_l2(dedicated_source)
    attachments = node(dedicated_source)["networkAttachments"]
    attachments.append({**deepcopy(attachments[1]), "name": "third", "addresses": []})
    InfrastructureModel.from_documents(dedicated_source).validate()
    with pytest.raises(FabricTranslationError, match="3 attachments.*at most two interfaces") as caught:
        render(dedicated_source, config)
    assert caught.value.path == "/spec/nodes/0/networkAttachments/0/adapterGroup"


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("same_network", [False, True])
def test_one_fabnet_per_vm_across_dedicated_ports(dedicated_source, config, grouped, same_network):
    attachments = node(dedicated_source)["networkAttachments"]
    second = deepcopy(attachments[0])
    second["name"] = "second"
    if not same_network:
        net = deepcopy(network(dedicated_source))
        net["metadata"]["name"] = "second"
        dedicated_source.append(net)
        second["networkRef"] = "network/second"
    attachments.append(second)
    if not grouped:
        for attachment in attachments:
            attachment.pop("adapterGroup")
    with pytest.raises(FabricTranslationError, match="at most one FABNet attachment per VM") as caught:
        render(dedicated_source, config)
    assert caught.value.path == "/spec/nodes/0/networkAttachments"


@pytest.mark.parametrize("addresses", [[], ["192.0.2.10"], ["2001:db8::10"]])
def test_dedicated_fabnet_rejects_authored_addresses(dedicated_source, config, addresses):
    node(dedicated_source)["networkAttachments"][0]["addresses"] = addresses
    with pytest.raises(FabricTranslationError, match="provider_managed_addressing"):
        render(dedicated_source, config)


@pytest.mark.parametrize("dpu_model", ["ConnectX-7-100", "ConnectX-7-400"])
@pytest.mark.parametrize("allocation", ["none", "single", "count", "separate"])
def test_dpu_selection_is_never_inferred(source, config, dpu_model, allocation):
    required = {"name": "ports", "type": "dpu", "constraints": {"model": dpu_model}}
    devices = [] if allocation == "none" else [required]
    if allocation == "count":
        required["count"] = 2
    elif allocation == "separate":
        devices.append({**deepcopy(required), "name": "other"})
    node(source)["requirements"]["devices"] = devices
    attachment = node(source)["networkAttachments"][0]
    attachment["adapterGroup"] = "ports"  # Same spelling as a requirement is not a reference.
    attachment["interfaceRequirements"]["adapter"] = {"class": "dpu", "model": dpu_model}
    InfrastructureModel.from_documents(source).validate()
    with pytest.raises(FabricTranslationError, match="only standard adapter class") as caught:
        render(source, config)
    assert caught.value.path == "/spec/nodes/0/networkAttachments/0/interfaceRequirements/adapter/class"


def test_dedicated_fabnet_rendering_is_offline(dedicated_source, config, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("offline rendering attempted a network operation")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    render(dedicated_source, config)


def test_historical_dedicated_binding_semantics():
    path = Path(os.environ.get("FABRIC_COMPLEX_MODEL", str(Path.home() / "Downloads/m5-complex.yml")))
    if not path.is_file():
        pytest.skip("read-only m5-complex.yml unavailable; set FABRIC_COMPLEX_MODEL")
    historical = yaml.safe_load(path.read_text())
    result = render_fabric(InfrastructureModel.load(FIXTURE), config=FabricAdapterConfig.from_file(CONFIG))
    # Only the dedicated-NIC subset is representable. Do not claim DPU/FPGA realization.
    for generated_key, original_key in [("node1", "node3"), ("node2", "node4")]:
        assert nodes(result)[generated_key]["pci"]["network"] == nodes(historical)[original_key]["pci"]["network"]
    original_networks = {net["name"]: net for net in networks(historical).values()}
    assert all(net == original_networks[net["name"]] for net in networks(result).values())
    dpu = nodes(historical)["node1"]["pci"]["dpu"]["dpu1"]
    assert dpu["name"] == "dpu1" and dpu["model"] == "NIC_ConnectX_7_100"
    assert [i["binding"] for i in dpu["interfaces"].values()] == ["l3-1", "l2-1"]


def test_example_cli_matches_generated_yaml(tmp_path):
    command = [sys.executable, "-m", "infra_model", "render", "fabric", str(FIXTURE), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout
    expected = ROOT / "tests/fixtures/fabric-fabnet-dedicated-nic-rendered.yaml"
    assert first.stdout == expected.read_bytes()
