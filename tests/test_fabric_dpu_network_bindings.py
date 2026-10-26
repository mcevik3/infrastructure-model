"""DPU bindings reuse allocations; all tests are offline."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from infra_model.adapters.fabric import translator
from infra_model.cli import main
from infra_model.loader import load_documents
from conftest import ROOT
from test_fabric_adapter import cluster, network, networks, node, nodes, render
from test_fabric_pci_devices import pci, requirement


CONFIG = ROOT / "adapter-configs/fabric.yaml"
FIXTURES = {
    "fabric-dpu-fabnet-ipv4": ("NIC_ConnectX_7_100", ["IPv4"]),
    "fabric-dpu-fabnet-ipv6": ("NIC_ConnectX_7_400", ["IPv6"]),
    "fabric-dpu-fabnet-l2sts": ("NIC_ConnectX_7_100", ["IPv4", "L2STS"]),
    "fabric-dpu-fabnet-l2bridge": ("NIC_ConnectX_7_400", ["IPv4Ext", "L2Bridge"]),
    "fabric-dpu-two-devices": ("NIC_ConnectX_7_100", ["IPv4", "L2Bridge", "L2Bridge", "L2Bridge"]),
}


def source_for(name):
    return [doc.data for doc in load_documents(ROOT / f"tests/fixtures/{name}.yaml")]


@pytest.fixture
def source():
    return source_for("fabric-dpu-fabnet-l2sts")


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


def interfaces(result, device="dpu1"):
    return pci(result)["dpu"][device]["interfaces"]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_acceptance_fixtures_bind_every_attachment_once_without_extra_devices(fixture, config):
    source = source_for(fixture)
    before = deepcopy(source)
    result = render(source, config)
    model, services = FIXTURES[fixture]
    assert [net["type"] for net in networks(result).values()] == services
    expected_count = 2 if fixture == "fabric-dpu-two-devices" else 1
    assert list(pci(result)["dpu"]) == [f"dpu{i}" for i in range(1, expected_count + 1)]
    assert pci(result)["network"] == {}
    assert all(d["model"] == model and d["name"] == key for key, d in pci(result)["dpu"].items())
    for authored_node, rendered_node in zip(cluster(source)["spec"]["nodes"], nodes(result).values()):
        actual = [iface for family in ("network", "dpu") for device in rendered_node["pci"][family].values()
                  for iface in device.get("interfaces", {}).values()]
        expected = [(f"eth{i}", f"conn-eth{i}", a["networkRef"].removeprefix("network/"))
                    for i, a in enumerate(authored_node["networkAttachments"], 1)]
        assert sorted((a["device"], a["connection"], a["binding"]) for a in actual) == sorted(expected)
    assert "vlan" not in yaml.safe_dump(result).lower()
    assert source == before


@pytest.mark.parametrize("model", ["ConnectX-7-100", "ConnectX-7-400"])
@pytest.mark.parametrize("family,reachability,service", [
    ("ipv4", "provider-internal", "IPv4"), ("ipv6", "provider-internal", "IPv6"),
    ("ipv4", "public", "IPv4Ext"), ("ipv6", "public", "IPv6Ext"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_all_fabnet_services_reuse_dpu_and_preserve_l2_addressing(source, config, model, family, reachability, service, reverse):
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    network(source)["spec"].update(addressFamily=family, reachability=reachability)
    if reverse:
        node(source)["networkAttachments"].reverse()
    result = render(source, config)
    assert len(pci(result)["dpu"]) == 1
    expected = [
        {"binding": "fabnet", "ipv4": {}, "ipv6": {}},
        {"binding": "data", "ipv4": {"address": "192.0.2.10/24", "gateway": "192.0.2.1", "dns": "192.0.2.53"},
         "ipv6": {"address": "2001:db8:1::10/64", "gateway": "2001:db8:1::1", "dns": "2001:db8:1::53"}},
    ]
    if reverse:
        expected.reverse()
    assert interfaces(result) == {f"iface{i}": {"device": f"eth{i}", "connection": f"conn-eth{i}", **data}
                                  for i, data in enumerate(expected, 1)}
    assert next(net for net in networks(result).values() if net["name"] == "fabnet") == {
        "name": "fabnet", "type": service, "subnet": {}}


@pytest.mark.parametrize("count", [None, 1])
@pytest.mark.parametrize("selectors", [False, True])
def test_local_and_canonical_refs_resolve_same_immutable_allocation(source, config, count, selectors):
    required = node(source)["requirements"]["devices"][0]
    if count is not None:
        required["count"] = count
    if selectors:
        for a in node(source)["networkAttachments"]:
            a["interfaceRequirements"]["adapter"] = {"class": "dpu", **required["constraints"]}
    model = InfrastructureModel.from_documents(source)
    plan = translator._translate(model, cluster=None, config=config)
    device, = plan.nodes[0].dpus
    assert device.requirement_id == "cluster/fabric-dpu-fabnet-l2sts/node/vm-a/requirement/device/primary"
    assert (device.name, device.model) == ("dpu1", "NIC_ConnectX_7_100")
    assert [i.attachment_ordinal for i in device.interfaces] == [1, 2]
    assert plan.nodes[0].components == ()
    with pytest.raises(FrozenInstanceError):
        device.name = "changed"
    for a in node(source)["networkAttachments"]:
        a["deviceRequirementRef"] = device.requirement_id
    assert render(source, config) == render_fabric(model, config=config)


def test_named_identical_dpus_use_requirement_order_not_attachment_order(config):
    source = source_for("fabric-dpu-two-devices")
    result = render(source, config)
    assert [[i["binding"] for i in interfaces(result, dpu).values()] for dpu in ("dpu1", "dpu2")] == [
        ["data-a", "data-c"], ["fabnet", "data-b"]]
    assert [[i["device"] for i in interfaces(result, dpu).values()] for dpu in ("dpu1", "dpu2")] == [
        ["eth2", "eth4"], ["eth1", "eth3"]]
    node(source)["requirements"]["devices"].reverse()
    reversed_result = render(source, config)
    assert interfaces(reversed_result, "dpu1") == interfaces(result, "dpu2")
    assert interfaces(reversed_result, "dpu2") == interfaces(result, "dpu1")


def test_reused_local_name_binds_separately_on_each_node(source, config):
    second = deepcopy(node(source))
    second["name"] = "vm-b"
    second["networkAttachments"] = second["networkAttachments"][:1]
    cluster(source)["spec"]["nodes"].append(second)
    result = render(source, config)
    first, = pci(result)["dpu"].values()
    last, = pci(result, "node3")["dpu"].values()
    assert first["name"] == last["name"] == "dpu1"
    assert last["interfaces"] == {"iface1": first["interfaces"]["iface1"]}


@pytest.mark.parametrize("model", ["ConnectX-7-100", "ConnectX-7-400"])
def test_two_l2_ports_without_fabnet_or_invented_addressing(source, config, model):
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    network(source)["spec"] = {"layer": "layer2", "siteRefs": ["site/site-a"]}
    result = render(source, config)
    assert [net["type"] for net in networks(result).values()] == ["L2Bridge", "L2STS"]
    assert interfaces(result)["iface1"] == {"device": "eth1", "connection": "conn-eth1", "binding": "fabnet"}
    assert interfaces(result)["iface2"]["binding"] == "data"


@pytest.mark.parametrize("model", ["ConnectX-5", "ConnectX-6"])
def test_interleaved_basic_dedicated_and_dpu_keep_independent_component_counters(source, config, model):
    fabnet, data = node(source)["networkAttachments"]
    basic = {"name": "basic", "networkRef": "network/data", "interfaceRequirements": {"type": "ethernet"}}
    nic = {"name": "nic-first", "networkRef": "network/data", "adapterGroup": "primary",
           "interfaceRequirements": {"type": "ethernet", "adapter": {"class": "standard", "model": model}}}
    node(source)["networkAttachments"] = [fabnet, basic, nic, data, dict(deepcopy(nic), name="nic-second")]
    result = render(source, config)
    assert [i["device"] for i in interfaces(result).values()] == ["eth1", "eth4"]
    nics = pci(result)["network"]
    assert list(nics) == ["nic1", "nic2"]
    assert [d["model"] for d in nics.values()] == ["NIC_Basic", f"NIC_{model.replace('-', '_')}"]
    assert [[i["device"] for i in d["interfaces"].values()] for d in nics.values()] == [["eth2"], ["eth3", "eth5"]]
    assert [list(d["interfaces"]) for d in nics.values()] == [["iface1"], ["iface1", "iface2"]]


def test_binding_later_device_preserves_allocation_count_expansion_and_other_families(source, config):
    target = node(source)["requirements"]["devices"][0]
    node(source)["requirements"]["devices"] = [
        requirement("dpu", "BlueField-2-ConnectX-6", name="unused", count=2),
        requirement("fpga", "U280", name="fpga"), requirement("gpu", "A40", name="gpu"),
        target, requirement("dpu", "ConnectX-7-400", name="last"),
    ]
    allocation_source = deepcopy(source)
    node(allocation_source)["networkAttachments"] = []
    before = pci(render(allocation_source, config))
    after = pci(render(source, config))
    assert list(after["dpu"]) == ["dpu1", "dpu2", "dpu3", "dpu4"]
    assert [i["binding"] for i in after["dpu"]["dpu3"].pop("interfaces").values()] == ["fabnet", "data"]
    assert after == before


def test_allocation_only_dpu_bytes_match_pre_c23b_snapshot(tmp_path):
    result = subprocess.run([sys.executable, "-m", "infra_model", "render", "fabric",
                             str(ROOT / "tests/fixtures/fabric-dpu.yaml"), "--config", str(CONFIG)],
                            cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (ROOT / "tests/fixtures/fabric-dpu-rendered.yaml").read_bytes()


@pytest.mark.parametrize("model", ["BlueField-2-ConnectX-6", "BlueField-3", "ConnectX-7", "NIC_ConnectX_7_100"])
def test_unsupported_dpu_network_models_fail(source, config, model):
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    with pytest.raises(FabricTranslationError, match="unsupported FABRIC DPU network-interface realization|exact supported model"):
        render(source, config)


@pytest.mark.parametrize("model", ["ConnectX-7-100", "ConnectX-7-400"])
def test_excess_ports_fail_at_reference(source, config, model):
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    attachments = node(source)["networkAttachments"]
    attachments.append(dict(deepcopy(attachments[1]), name="third", addresses=[]))
    with pytest.raises(FabricTranslationError, match="3 attachments.*at most two interfaces") as caught:
        render(source, config)
    assert caught.value.path == "/spec/nodes/0/networkAttachments/0/deviceRequirementRef"


@pytest.mark.parametrize("model", [None, "U280", "SN1022"])
@pytest.mark.parametrize("interface_type", ["ethernet", "infiniband"])
def test_fpga_bindings_fail_explicitly_even_without_model(source, config, model, interface_type):
    node(source)["requirements"]["devices"] = [requirement("fpga", model, name="primary")]
    for a in node(source)["networkAttachments"]:
        a["interfaceRequirements"]["type"] = interface_type
    InfrastructureModel.from_documents(source).validate()
    with pytest.raises(FabricTranslationError, match="FPGA-backed.*unsupported provider realization") as caught:
        render(source, config)
    assert caught.value.path.endswith("/deviceRequirementRef")


@pytest.mark.parametrize("second_kind", ["same-dpu", "other-dpu", "basic", "dedicated"])
def test_one_fabnet_per_vm_across_all_component_families(source, config, second_kind):
    second = deepcopy(node(source)["networkAttachments"][0])
    second["name"] = "second-fabnet"
    node(source)["networkAttachments"] = [node(source)["networkAttachments"][0], second]
    if second_kind == "other-dpu":
        node(source)["requirements"]["devices"].append(requirement("dpu", "ConnectX-7-100", name="other"))
        second["deviceRequirementRef"] = "other"
    elif second_kind in {"basic", "dedicated"}:
        del second["deviceRequirementRef"]
        if second_kind == "dedicated":
            second["interfaceRequirements"]["adapter"] = {"class": "standard", "model": "ConnectX-6"}
    with pytest.raises(FabricTranslationError, match="at most one FABNet attachment per VM"):
        render(source, config)


@pytest.mark.parametrize("model", ["ConnectX-7-100", "ConnectX-7-400"])
def test_dpu_does_not_extend_l2ptp_eligibility(config, model):
    source = source_for("fabric-l2ptp")
    for item in cluster(source)["spec"]["nodes"]:
        item["requirements"]["devices"] = [requirement("dpu", model, name="primary")]
        for a in item["networkAttachments"]:
            a.pop("adapterGroup", None)
            a.update(deviceRequirementRef="primary", interfaceRequirements={"type": "ethernet"})
    with pytest.raises(FabricTranslationError, match="L2PTP requires a supported dedicated interface.*NIC_ConnectX_6"):
        render(source, config)


@pytest.mark.parametrize("change,reason", [
    ({"type": "infiniband"}, "explicit Ethernet"),
    ({"minSpeed": {"value": 100, "unit": "Gbps"}}, "minSpeed"),
    ({"features": ["rdma"]}, "feature interface requirements"),
    ({"adapter": {"class": "dpu", "vendor": "AMD"}}, "exact vendor NVIDIA"),
])
def test_dpu_interface_constraints_are_not_inferred(source, config, change, reason):
    node(source)["requirements"]["devices"][0]["constraints"].pop("vendor")
    node(source)["networkAttachments"][0]["interfaceRequirements"].update(change)
    with pytest.raises(FabricTranslationError, match=reason):
        render(source, config)


@pytest.mark.parametrize("case,reason", [
    ("count", "effective count one"), ("missing", "unresolved_reference"),
    ("cross-node", "reference_scope"), ("addresses", "provider_managed_addressing"),
    ("multisite", "exactly one distinct effective FABRIC"), ("cross-cluster", "does not contain all FABNet endpoints"),
])
def test_dpu_preserves_core_and_fabnet_restrictions(source, config, case, reason):
    first = node(source)
    if case == "count":
        first["requirements"]["devices"][0]["count"] = 2
    elif case == "missing":
        first["networkAttachments"][0]["deviceRequirementRef"] = "unknown"
    elif case == "cross-node":
        peer = cluster(source)["spec"]["nodes"][1]
        peer["requirements"]["devices"] = deepcopy(first["requirements"]["devices"])
        first["networkAttachments"][0]["deviceRequirementRef"] = "cluster/fabric-dpu-fabnet-l2sts/node/peer/requirement/device/primary"
    elif case == "addresses":
        first["networkAttachments"][0]["addresses"] = []
    elif case == "multisite":
        network(source)["spec"]["siteRefs"].append("site/site-b")
        peer = deepcopy(first)
        peer.update(name="remote", placement={"siteRef": "site/site-b"})
        peer["networkAttachments"] = peer["networkAttachments"][:1]
        cluster(source)["spec"]["nodes"].append(peer)
    else:
        other = deepcopy(cluster(source))
        other["metadata"]["name"] = "other"
        source.append(other)
    with pytest.raises(FabricTranslationError, match=reason):
        render(source, config, cluster="fabric-dpu-fabnet-l2sts")


@pytest.mark.parametrize("copies", [0, 2])
def test_missing_or_ambiguous_planned_component_fails_without_fallback(source, config, monkeypatch, copies):
    allocate = translator._devices
    def broken_allocation(*args):
        devices = allocate(*args)
        devices["dpu"] *= copies
        return devices
    monkeypatch.setattr(translator, "_devices", broken_allocation)
    with pytest.raises(FabricTranslationError, match="expected one already-planned FABRIC DPU component"):
        render(source, config)


@pytest.mark.parametrize("invalid", ["ports", "fpga", "fabnet", "model"])
def test_invalid_translation_emits_no_partial_yaml(source, tmp_path, capsys, invalid):
    attachments = node(source)["networkAttachments"]
    if invalid == "ports":
        attachments.append(dict(deepcopy(attachments[1]), name="third", addresses=[]))
    elif invalid == "fpga":
        node(source)["requirements"]["devices"] = [requirement("fpga", "U280", name="primary")]
    elif invalid == "fabnet":
        attachments[1] = dict(deepcopy(attachments[0]), name="second")
    else:
        node(source)["requirements"]["devices"][0]["constraints"]["model"] = "BlueField-2-ConnectX-6"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump_all(source, sort_keys=False))
    assert main(["render", "fabric", str(path), "--config", str(CONFIG)]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err


def test_dpu_binding_is_offline(source, config, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("offline DPU realization attempted a network operation")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    render(source, config)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_repeated_cli_rendering_has_identical_bytes(fixture, tmp_path):
    command = [sys.executable, "-m", "infra_model", "render", "fabric",
               str(ROOT / f"tests/fixtures/{fixture}.yaml"), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout


@pytest.mark.parametrize("filename,model,order", [
    ("dpu.yml", "ConnectX-7-400", ["IPv4", "L2STS"]),
    ("dpu4-7.yml", "ConnectX-7-100", ["L2STS", "IPv4"]),
])
def test_historical_dpu_interface_semantics(source, config, filename, model, order):
    directory = Path(os.environ.get("FABRIC_NOTEBOOK_MODELS", str(ROOT.parents[1] / "fabric/fabric-generic-cluster-notebooks/model")))
    path = directory / filename
    if not path.is_file():
        pytest.skip("read-only historical DPU configurations unavailable; set FABRIC_NOTEBOOK_MODELS")
    historical = yaml.safe_load(path.read_text())
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    if order[0] == "L2STS":
        node(source)["networkAttachments"].reverse()
    result = render(source, config)
    actual = pci(result)["dpu"]["dpu1"]
    original = next(iter(next(iter(nodes(historical).values()))["pci"]["dpu"].values()))
    assert actual["model"] == original["model"]
    assert len(actual["interfaces"]) == len(original["interfaces"]) == 2
    # Historical names and cross-site L2Bridge labels are not canonical.
    # Compare ordered dataplane interface shape and FABNet empty-family semantics.
    historical_networks = {net["name"]: net for net in networks(historical).values()}
    for generated, old, service in zip(actual["interfaces"].values(), original["interfaces"].values(), order):
        assert set(generated) == set(old) == {"device", "connection", "binding", "ipv4", "ipv6"}
        assert generated["device"] == old["device"]
        assert generated["connection"] == old["connection"]
        if service == "IPv4":
            assert historical_networks[old["binding"]]["type"] == "IPv4"
            assert generated["ipv4"] == old["ipv4"] == generated["ipv6"] == old["ipv6"] == {}
        else:
            assert historical_networks[old["binding"]]["type"] in {"L2Bridge", "L2STS"}
            for family in ("ipv4", "ipv6"):
                assert set(generated[family]) == set(old[family]) == {"address", "gateway", "dns"}


def test_acceptance_yaml_through_installed_downstream_pydantic(config, tmp_path):
    python = os.environ.get("FABRIC_MODEL_PYTHON", sys._base_executable)
    probe = subprocess.run([python, "-c", "from importlib.metadata import version; print(version('fabric-generic-cluster'))"],
                           capture_output=True, text=True)
    if probe.returncode and "PackageNotFoundError" in probe.stderr:
        pytest.skip("installed fabric-generic-cluster unavailable; set FABRIC_MODEL_PYTHON")
    assert probe.returncode == 0, probe.stderr
    paths = []
    cases = [(ROOT / f"tests/fixtures/{fixture}.yaml", config) for fixture in [
        *FIXTURES, "fabric-dpu", "fabric-pci-devices", "fabric-dedicated-nic",
        "fabric-connectx5", "fabric-l2ptp", "fabric-fabnet-dedicated-nic"]]
    fabnet_config = FabricAdapterConfig.from_file(ROOT / "tests/fixtures/fabric-fabnet-config.yaml")
    cases.extend((ROOT / f"tests/fixtures/fabric-fabnet-{service}.yaml", fabnet_config)
                 for service in ("ipv4", "ipv6", "ipv4ext", "ipv6ext", "multisite"))
    cases.extend((ROOT / f"examples/{name}", config) for name in ("fabric-like", "multi-site"))
    for index, (source_path, adapter_config) in enumerate(cases):
        path = tmp_path / f"{index}.yaml"
        output = render_fabric(InfrastructureModel.load(source_path), config=adapter_config)
        path.write_text(yaml.safe_dump(output, sort_keys=False))
        paths.append(str(path))
    # Load only the installed model module; importing the package also imports
    # deployment/SDK modules, which are unnecessary for offline schema validation.
    script = '''
import importlib.util
from importlib.metadata import distribution
import sys
import yaml
path = distribution("fabric-generic-cluster").locate_file("fabric_generic_cluster/models.py")
spec = importlib.util.spec_from_file_location("installed_fabric_models", path)
models = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = models
spec.loader.exec_module(models)
for filename in sys.argv[1:]:
    with open(filename) as stream:
        document = yaml.safe_load(stream)
    topology = models.SiteTopology.from_yaml_dict(document)
    for node in topology.site_topology_nodes.nodes.values():
        for key, dpu in node.pci.dpu.items():
            assert key == dpu.name
            assert len(dpu.interfaces) <= 2
            for iface in dpu.interfaces.values():
                assert topology.get_network_by_name(iface.binding) is not None
                assert (key, iface) in node.get_interfaces_for_network(iface.binding)
print(f"Validated {len(sys.argv) - 1} generated YAML documents")
'''
    result = subprocess.run([python, "-c", script, *paths], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"Validated {len(paths)} generated YAML documents"
