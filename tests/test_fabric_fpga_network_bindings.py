"""Offline FPGA allocation identity and positional binding; no dataplane claims."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import os
import socket
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from infra_model.adapters.fabric import translator
from infra_model.cli import main
from conftest import ROOT
from test_fabric_adapter import cluster, network, networks, node, nodes, render
from test_fabric_dpu_network_bindings import source_for
from test_fabric_pci_devices import pci, requirement


CONFIG = ROOT / "adapter-configs/fabric.yaml"
FIXTURES = {
    "fabric-fpga-l2bridge": ("FPGA_Xilinx_U280", ["L2Bridge"]),
    "fabric-fpga-l2sts": ("FPGA_Xilinx_SN1022", ["L2STS"]),
    "fabric-fpga-fabnet-l2bridge": ("FPGA_Xilinx_SN1022", ["IPv4Ext", "L2Bridge"]),
    "fabric-fpga-two-devices": ("FPGA_Xilinx_U280", ["IPv4", "L2Bridge", "L2Bridge", "L2Bridge"]),
}
MODELS = [("U280", "FPGA_Xilinx_U280"), ("Alveo U280", "FPGA_Xilinx_U280"),
          ("SN1022", "FPGA_Xilinx_SN1022")]
FABNET = [("ipv4", "provider-internal", "IPv4"), ("ipv6", "provider-internal", "IPv6"),
          ("ipv4", "public", "IPv4Ext"), ("ipv6", "public", "IPv6Ext")]


@pytest.fixture
def source():
    return source_for("fabric-fpga-fabnet-l2bridge")


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


def interfaces(result, device="fpga1"):
    return pci(result)["fpga"][device]["interfaces"]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_fixtures_reuse_allocations_bind_every_attachment_once_and_preserve_source(fixture, config):
    source = source_for(fixture)
    before = deepcopy(source)
    result = render(source, config)
    model, services = FIXTURES[fixture]
    assert [net["type"] for net in networks(result).values()] == services
    count = 2 if fixture == "fabric-fpga-two-devices" else 1
    assert list(pci(result)["fpga"]) == [f"fpga{i}" for i in range(1, count + 1)]
    assert pci(result)["network"] == pci(result)["dpu"] == {}
    assert all(d["model"] == model and d["name"] == key for key, d in pci(result)["fpga"].items())
    for authored, rendered in zip(cluster(source)["spec"]["nodes"], nodes(result).values()):
        actual = [i for family in rendered["pci"].values() for d in family.values()
                  for i in d.get("interfaces", {}).values()]
        expected = [(f"eth{i}", f"conn-eth{i}", a["networkRef"].removeprefix("network/"))
                    for i, a in enumerate(authored["networkAttachments"], 1)]
        assert sorted((i["device"], i["connection"], i["binding"]) for i in actual) == sorted(expected)
    assert "vlan" not in yaml.safe_dump(result).lower()
    assert render(source, config) == result
    assert source == before


@pytest.mark.parametrize("model,target", MODELS)
@pytest.mark.parametrize("vendor", [None, "AMD", "Xilinx"])
@pytest.mark.parametrize("count", [None, 1])
def test_models_vendors_local_and_canonical_count_one_identity(source, config, model, target, vendor, count):
    selectors = {} if vendor is None else {"vendor": vendor}
    node(source)["requirements"]["devices"] = [requirement("fpga", model, name="primary", count=count, **selectors)]
    original = deepcopy(source)
    generic = InfrastructureModel.from_documents(source)
    plan = translator._translate(generic, cluster=None, config=config)
    device, = plan.nodes[0].fpgas
    assert (device.name, device.model) == ("fpga1", target)
    assert device.requirement_id == "cluster/fabric-fpga-fabnet-l2bridge/node/vm-a/requirement/device/primary"
    assert [i.attachment_ordinal for i in device.interfaces] == [1, 2]
    assert plan.nodes[0].components == ()
    with pytest.raises(FrozenInstanceError):
        device.name = "changed"
    assert source == original
    for a in node(source)["networkAttachments"]:
        a["deviceRequirementRef"] = device.requirement_id
    assert render(source, config) == render_fabric(generic, config=config)
    assert ("count" in generic.of_kind("DeviceRequirement")[0]) == (count is not None)


@pytest.mark.parametrize("model,target", MODELS)
@pytest.mark.parametrize("family,reachability,service", FABNET)
@pytest.mark.parametrize("reverse", [False, True])
def test_fabnet_services_preserve_authored_l2_addresses(source, config, model, target, family, reachability, service, reverse):
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    network(source)["spec"].update(addressFamily=family, reachability=reachability)
    if reverse:
        node(source)["networkAttachments"].reverse()
    result = render(source, config)
    expected = [
        {"binding": "fabnet", "ipv4": {}, "ipv6": {}},
        {"binding": "data", "ipv4": {"address": "192.0.2.10/24", "gateway": "192.0.2.1", "dns": "192.0.2.53"},
         "ipv6": {"address": "2001:db8:1::10/64", "gateway": "2001:db8:1::1", "dns": "2001:db8:1::53"}},
    ]
    if reverse:
        expected.reverse()
    assert interfaces(result) == {f"iface{i}": {"device": f"eth{i}", "connection": f"conn-eth{i}", **data}
                                  for i, data in enumerate(expected, 1)}
    assert pci(result)["fpga"]["fpga1"]["model"] == target
    assert next(n for n in networks(result).values() if n["name"] == "fabnet") == {
        "name": "fabnet", "type": service, "subnet": {}}


@pytest.mark.parametrize("fixture", ["fabric-fpga-l2bridge", "fabric-fpga-l2sts"])
@pytest.mark.parametrize("model,target", MODELS)
@pytest.mark.parametrize("ports", [1, 2])
def test_addressless_l2_one_or_two_logical_interfaces(config, fixture, model, target, ports):
    source = source_for(fixture)
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    attachments = node(source)["networkAttachments"]
    if ports == 2:
        attachments.append(dict(deepcopy(attachments[0]), name="second"))
    result = render(source, config)
    assert interfaces(result) == {f"iface{i}": {"device": f"eth{i}", "connection": f"conn-eth{i}", "binding": "data"}
                                  for i in range(1, ports + 1)}
    assert all("subnet" not in net for net in networks(result).values())
    assert list(pci(result)["fpga"]) == ["fpga1"]


@pytest.mark.parametrize("model", ["U280", "SN1022"])
def test_third_interface_fails_at_reference(source, config, model):
    node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
    attachments = node(source)["networkAttachments"]
    attachments.append(dict(deepcopy(attachments[1]), name="third", addresses=[]))
    with pytest.raises(FabricTranslationError, match="3 attachments.*at most two interfaces") as caught:
        render(source, config)
    assert caught.value.path == "/spec/nodes/0/networkAttachments/0/deviceRequirementRef"
    assert caught.value.entity.endswith("/network-attachment/fabnet")


def test_identical_devices_follow_requirement_order_with_interleaved_attachments(config):
    source = source_for("fabric-fpga-two-devices")
    result = render(source, config)
    assert [[i["binding"] for i in interfaces(result, d).values()] for d in ("fpga1", "fpga2")] == [
        ["data-a", "data-c"], ["fabnet", "data-b"]]
    assert [[i["device"] for i in interfaces(result, d).values()] for d in ("fpga1", "fpga2")] == [
        ["eth2", "eth4"], ["eth1", "eth3"]]
    node(source)["requirements"]["devices"].reverse()
    reversed_result = render(source, config)
    assert interfaces(reversed_result, "fpga1") == interfaces(result, "fpga2")
    assert interfaces(reversed_result, "fpga2") == interfaces(result, "fpga1")


def test_same_local_requirement_name_on_different_nodes_is_independent(source, config):
    second = deepcopy(node(source))
    second["name"] = "vm-b"
    second["networkAttachments"] = second["networkAttachments"][:1]
    cluster(source)["spec"]["nodes"].append(second)
    result = render(source, config)
    assert pci(result, "node3")["fpga"]["fpga1"]["interfaces"] == {"iface1": interfaces(result)["iface1"]}


@pytest.mark.parametrize("nic_model", ["ConnectX-5", "ConnectX-6"])
@pytest.mark.parametrize("dpu_model", ["ConnectX-7-100", "ConnectX-7-400"])
def test_mixed_family_allocation_and_interface_numbering(source, config, nic_model, dpu_model):
    target = node(source)["requirements"]["devices"][0]
    node(source)["requirements"]["devices"] = [
        requirement("fpga", "U280", name="unused", count=2),
        requirement("gpu", "A40", name="gpu", count=2),
        requirement("dpu", dpu_model, name="dpu"), target,
        requirement("storage", "P4510", name="nvme", protocol="nvme", count=2),
        requirement("fpga", "U280", name="last"),
    ]
    fabnet, data = node(source)["networkAttachments"]
    basic = {"name": "basic", "networkRef": "network/data", "interfaceRequirements": {"type": "ethernet"}}
    dpu = dict(deepcopy(basic), name="dpu-first", deviceRequirementRef="dpu")
    nic = dict(deepcopy(basic), name="nic-first", adapterGroup="primary",
               interfaceRequirements={"type": "ethernet", "adapter": {"class": "standard", "model": nic_model}})
    node(source)["networkAttachments"] = [dpu, basic, fabnet, nic, data,
                                          dict(deepcopy(dpu), name="dpu-second"),
                                          dict(deepcopy(nic), name="nic-second")]
    allocation_source = deepcopy(source)
    node(allocation_source)["networkAttachments"] = []
    before = pci(render(allocation_source, config))
    after = pci(render(source, config))
    assert list(after["fpga"]) == ["fpga1", "fpga2", "fpga3", "fpga4"]
    assert [i["device"] for i in after["fpga"]["fpga3"].pop("interfaces").values()] == ["eth3", "eth5"]
    assert [i["device"] for i in after["dpu"]["dpu1"].pop("interfaces").values()] == ["eth1", "eth6"]
    nics = after.pop("network")
    assert [d["model"] for d in nics.values()] == ["NIC_Basic", "NIC_" + nic_model.replace("-", "_")]
    assert [[i["device"] for i in d["interfaces"].values()] for d in nics.values()] == [["eth2"], ["eth4", "eth7"]]
    before.pop("network")
    assert after == before


@pytest.mark.parametrize("case,reason", [
    ("invalid", "schema"), ("missing", "unresolved_reference"), ("cross-node", "reference_scope"),
    ("wrong-kind", "schema"), ("gpu", "device_requirement_type"), ("count", "effective count one"),
    ("no-interface", "schema"), ("adapter-group", "schema"),
])
def test_invalid_references_fail(source, config, case, reason):
    first = node(source)
    attachment = first["networkAttachments"][0]
    if case == "invalid":
        attachment["deviceRequirementRef"] = "invalid reference!"
    elif case == "missing":
        attachment["deviceRequirementRef"] = "missing"
    elif case == "cross-node":
        peer = cluster(source)["spec"]["nodes"][1]
        peer["requirements"]["devices"] = deepcopy(first["requirements"]["devices"])
        attachment["deviceRequirementRef"] = "cluster/fabric-fpga-fabnet-l2bridge/node/peer/requirement/device/primary"
    elif case == "wrong-kind":
        attachment["deviceRequirementRef"] = "network/data"
    elif case == "gpu":
        first["requirements"]["devices"] = [requirement("gpu", "A40", name="primary")]
    elif case == "count":
        first["requirements"]["devices"][0]["count"] = 2
    elif case == "no-interface":
        del attachment["interfaceRequirements"]
    else:
        attachment["adapterGroup"] = "group"
    with pytest.raises(FabricTranslationError, match=reason):
        render(source, config)


@pytest.mark.parametrize("copies", [0, 2])
def test_missing_or_ambiguous_plan_has_no_model_fallback(source, config, monkeypatch, copies):
    node(source)["requirements"]["devices"].append(requirement("fpga", "SN1022", name="same-model"))
    allocate = translator._devices
    def broken_allocation(*args):
        devices = allocate(*args)
        first, other = devices["fpga"]
        devices["fpga"] = (first,) * copies + (other,)
        return devices
    monkeypatch.setattr(translator, "_devices", broken_allocation)
    with pytest.raises(FabricTranslationError, match=f"expected one already-planned FABRIC FPGA component; found {copies}") as caught:
        render(source, config)
    assert caught.value.path.endswith("/deviceRequirementRef")


def test_unapproved_planned_model_rejected(source, config, monkeypatch):
    allocate = translator._devices
    def broken_allocation(*args):
        devices = allocate(*args)
        devices["fpga"] = tuple(replace(d, model="FPGA_Xilinx_U250") for d in devices["fpga"])
        return devices
    monkeypatch.setattr(translator, "_devices", broken_allocation)
    with pytest.raises(FabricTranslationError, match="unsupported FABRIC FPGA network-interface realization"):
        render(source, config)


@pytest.mark.parametrize("model", [None, "U250", "u280", "sn1022", "Alveo U280 card", "FPGA_Xilinx_U280", "FPGA_Xilinx_SN1022"])
def test_no_arbitrary_model_aliases(source, config, model):
    node(source)["requirements"]["devices"] = [requirement("fpga", model, name="primary")]
    with pytest.raises(FabricTranslationError, match="exact supported model"):
        render(source, config)


@pytest.mark.parametrize("change,path,reason", [
    ({"vendor": "NVIDIA"}, "vendor", "incompatible"), ({"vendor": "amd"}, "vendor", "incompatible"),
    ({"features": ["rdma"]}, "features", "device feature requirements"),
    ({"minCapacity": {"value": 1, "unit": "GiB"}}, "", "minCapacity"),
])
def test_fpga_allocation_constraints_not_inferred(source, config, change, path, reason):
    node(source)["requirements"]["devices"][0]["constraints"].update(change)
    with pytest.raises(FabricTranslationError, match=reason) as caught:
        render(source, config)
    assert caught.value.path.endswith("/constraints" + ("/" + path if path else ""))


@pytest.mark.parametrize("change,path,reason", [
    ({"type": "infiniband"}, "", "explicit Ethernet"),
    ({"minSpeed": {"value": 100, "unit": "Gbps"}}, "/minSpeed", "minSpeed"),
    ({"features": ["rdma"]}, "/features", "feature interface requirements"),
    *[({"adapter": {"class": c}}, "/adapter/class", "unsupported for FPGA|device_requirement_conflict")
      for c in ("standard", "smartnic", "dpu", "other")],
    ({"adapter": {"class": "standard", "model": "U280"}}, "/adapter/model", "device_requirement_conflict"),
    ({"adapter": {"class": "standard", "vendor": "AMD"}}, "/adapter/vendor", "device_requirement_conflict"),
])
def test_unsupported_interface_constraints(source, config, change, path, reason):
    node(source)["networkAttachments"][0]["interfaceRequirements"].update(change)
    with pytest.raises(FabricTranslationError, match=reason) as caught:
        render(source, config)
    assert caught.value.path.endswith("/interfaceRequirements" + path)


def test_empty_features_are_not_a_constraint(source, config):
    expected = render(source, config)
    node(source)["requirements"]["devices"][0]["constraints"]["features"] = []
    for a in node(source)["networkAttachments"]:
        a["interfaceRequirements"]["features"] = []
    assert render(source, config) == expected


@pytest.mark.parametrize("second_kind", ["same-fpga", "other-fpga", "dpu", "basic", "dedicated"])
@pytest.mark.parametrize("same_network", [False, True])
def test_one_fabnet_per_vm_across_families(source, config, second_kind, same_network):
    first = node(source)["networkAttachments"][0]
    second = dict(deepcopy(first), name="second-fabnet")
    node(source)["networkAttachments"] = [first, second]
    if not same_network:
        other = deepcopy(network(source))
        other["metadata"]["name"] = "other-fabnet"
        source.append(other)
        second["networkRef"] = "network/other-fabnet"
    if second_kind in {"other-fpga", "dpu"}:
        kind, model = ("fpga", "SN1022") if second_kind == "other-fpga" else ("dpu", "ConnectX-7-100")
        node(source)["requirements"]["devices"].append(requirement(kind, model, name="other"))
        second["deviceRequirementRef"] = "other"
    elif second_kind in {"basic", "dedicated"}:
        del second["deviceRequirementRef"]
        if second_kind == "dedicated":
            second["interfaceRequirements"]["adapter"] = {"class": "standard", "model": "ConnectX-6"}
    with pytest.raises(FabricTranslationError, match="at most one FABNet attachment per VM"):
        render(source, config)


@pytest.mark.parametrize("model", ["U280", "SN1022"])
def test_fpga_never_qualifies_for_l2ptp(config, model):
    source = source_for("fabric-l2ptp")
    for n in cluster(source)["spec"]["nodes"]:
        n["requirements"]["devices"] = [requirement("fpga", model, name="primary")]
        for a in n["networkAttachments"]:
            a.pop("adapterGroup", None)
            a.update(deviceRequirementRef="primary", interfaceRequirements={"type": "ethernet"})
    with pytest.raises(FabricTranslationError, match="L2PTP requires a supported dedicated interface.*NIC_ConnectX_6"):
        render(source, config)


@pytest.mark.parametrize("case,reason", [
    ("addresses", "provider_managed_addressing"), ("prefixes", "provider_managed_addressing"),
    ("dns", "requires Network dns to be omitted"), ("gateways", "requires Network defaultGateways to be omitted"),
    ("multisite", "exactly one distinct effective FABRIC"),
    ("cross-cluster", "does not contain all FABNet endpoints"), ("partial", "explicit addressFamily"),
    ("allocation", "explicit addressAllocation"), ("connectivity", "multipoint connectivity"),
])
def test_existing_network_service_restrictions(source, config, case, reason):
    spec = network(source)["spec"]
    if case == "addresses":
        node(source)["networkAttachments"][0]["addresses"] = []
    elif case == "prefixes":
        spec["prefixes"] = []
    elif case == "dns":
        spec["dns"] = {"servers": []}
    elif case == "gateways":
        spec["defaultGateways"] = []
    elif case == "multisite":
        spec["siteRefs"].append("site/site-b")
        peer = deepcopy(node(source))
        peer.update(name="remote", placement={"siteRef": "site/site-b"})
        peer["networkAttachments"] = peer["networkAttachments"][:1]
        cluster(source)["spec"]["nodes"].append(peer)
    elif case == "cross-cluster":
        other = deepcopy(cluster(source))
        other["metadata"]["name"] = "other"
        source.append(other)
    elif case == "partial":
        del spec["addressFamily"]
        del spec["addressAllocation"]
    elif case == "allocation":
        del spec["addressAllocation"]
    else:
        spec["connectivity"] = "point-to-point"
    with pytest.raises(FabricTranslationError, match=reason):
        render(source, config, cluster="fabric-fpga-fabnet-l2bridge")


@pytest.mark.parametrize("fixture", ["fabric-fpga-allocation-only", "fabric-dpu", "fabric-fabnet-dedicated-nic"])
def test_preserved_golden_bytes(fixture, tmp_path):
    golden = ROOT / f"tests/fixtures/{fixture}-rendered.yaml"
    if fixture == "fabric-fpga-allocation-only":
        # Captured BEFORE implementation from committed 0bb7149e07c858c60ea83d487c826f36c79199fb.
        assert hashlib.sha256(golden.read_bytes()).hexdigest() == "258c93862fffc02fd368a156eb97c9cfc8ae265bc34b7897e6c814e0cb251cb8"
    result = subprocess.run([sys.executable, "-m", "infra_model", "render", "fabric",
                             str(ROOT / f"tests/fixtures/{fixture}.yaml"), "--config", str(CONFIG)],
                            cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == golden.read_bytes()


@pytest.mark.parametrize("fixture", FIXTURES)
def test_deterministic_cli_bytes(fixture, tmp_path):
    command = [sys.executable, "-m", "infra_model", "render", "fabric",
               str(ROOT / f"tests/fixtures/{fixture}.yaml"), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout


@pytest.mark.parametrize("invalid", ["ports", "model", "fabnet", "missing", "class", "speed"])
def test_invalid_translation_emits_no_partial_yaml(source, tmp_path, capsys, invalid):
    attachments = node(source)["networkAttachments"]
    if invalid == "ports":
        attachments.append(dict(deepcopy(attachments[1]), name="third", addresses=[]))
    elif invalid == "model":
        node(source)["requirements"]["devices"][0]["constraints"]["model"] = "U250"
    elif invalid == "fabnet":
        attachments[1] = dict(deepcopy(attachments[0]), name="second")
    elif invalid == "missing":
        attachments[0]["deviceRequirementRef"] = "unknown"
    elif invalid == "class":
        attachments[0]["interfaceRequirements"]["adapter"] = {"class": "standard"}
    else:
        attachments[0]["interfaceRequirements"]["minSpeed"] = {"value": 1, "unit": "Gbps"}
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump_all(source, sort_keys=False))
    assert main(["render", "fabric", str(path), "--config", str(CONFIG)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert str(path) in output.err


def test_fpga_translation_is_offline(source, config, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("offline FPGA realization attempted a network operation")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    render(source, config)


@pytest.mark.parametrize("case,reason", [
    ("missing", "node-local DeviceRequirement"), ("wrong-kind", "node-local DeviceRequirement"),
    ("cross-node", "node-local DeviceRequirement"), ("wrong-family", "unsupported provider realization"),
    ("count", "effective count one"),
])
def test_planned_lookup_defensively_checks_reference_identity(source, config, case, reason):
    registry = InfrastructureModel.from_documents(source)._validated_registry()
    owner = registry.of_kind("ClusterNode")[0]
    attachment = registry.entries[owner.data["networkAttachments"][0]["id"]]
    reference = attachment.data["deviceRequirementRef"]
    required = registry.entries[reference]
    devices = translator._devices(owner, registry)["fpga"]
    if case == "missing":
        del registry.entries[reference]
    elif case == "wrong-kind":
        required.data["kind"] = "NetworkAttachment"
    elif case == "cross-node":
        registry.entries[reference] = replace(required, parent="cluster/other/node/other")
    elif case == "wrong-family":
        required.data["type"] = "gpu"
    else:
        required.data["count"] = 2
    with pytest.raises(FabricTranslationError, match=reason):
        translator._fpga_component(owner, registry, [(1, attachment)], devices)


def test_installed_downstream_parsing_and_positional_binding_with_test_doubles(config, tmp_path):
    python = os.environ.get("FABRIC_MODEL_PYTHON", sys._base_executable)
    probe = subprocess.run([python, "-c", "from importlib.metadata import version; print(version('fabric-generic-cluster'))"],
                           capture_output=True, text=True)
    if probe.returncode and "PackageNotFoundError" in probe.stderr:
        pytest.skip("installed fabric-generic-cluster unavailable; set FABRIC_MODEL_PYTHON")
    assert probe.returncode == 0, probe.stderr
    documents = [render(source_for(fixture), config) for fixture in [*FIXTURES, "fabric-fpga-allocation-only"]]
    for model, _ in MODELS:
        for family, reachability, _ in FABNET:
            for reverse in (False, True):
                source = source_for("fabric-fpga-fabnet-l2bridge")
                node(source)["requirements"]["devices"][0]["constraints"]["model"] = model
                network(source)["spec"].update(addressFamily=family, reachability=reachability)
                if reverse:
                    node(source)["networkAttachments"].reverse()
                documents.append(render(source, config))
    paths = []
    for index, document in enumerate(documents):
        path = tmp_path / f"{index}.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        paths.append(str(path))
    # Load the installed Pydantic module and only the two inspected deployment
    # function definitions. No SDK import, manager construction, or live API.
    script = '''
import ast
import importlib.util
from importlib.metadata import distribution
import logging
import socket
import sys
from types import SimpleNamespace
import yaml

def no_network(*args, **kwargs):
    raise AssertionError("downstream offline test attempted network access")
socket.create_connection = socket.getaddrinfo = socket.socket.connect = no_network
dist = distribution("fabric-generic-cluster")
path = dist.locate_file("fabric_generic_cluster/models.py")
spec = importlib.util.spec_from_file_location("installed_fabric_models", path)
models = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = models
spec.loader.exec_module(models)
deployment = dist.locate_file("fabric_generic_cluster/deployment.py")
tree = ast.parse(deployment.read_text())
names = {"add_fpgas_to_node", "create_and_bind_networks"}
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert {n.name for n in functions} == names
namespace = {"Node": models.Node, "SiteTopology": models.SiteTopology,
             "logger": logging.getLogger("offline-fpga-binding")}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(deployment), "exec"), namespace)

class FakeNode:
    def __init__(self, hostname, reverse):
        self.hostname, self.reverse = hostname, reverse
        self.components, self.allocations = {}, []
    def add_component(self, *, name, model):
        assert name not in self.components, "extra component allocation"
        ports = [(self.hostname, name, p) for p in ("p1", "p2")]
        if self.reverse:
            ports.reverse()
        self.components[name] = SimpleNamespace(get_interfaces=lambda: ports)
        self.allocations.append((name, model))
    def get_component(self, *, name):
        return self.components[name]

class FakeNetwork:
    def __init__(self, name, service):
        self.name, self.service, self.members = name, service, []
    def add_interface(self, interface):
        self.members.append(interface)

class FakeSlice:
    def __init__(self, nodes):
        self.nodes, self.networks = nodes, {}
    def get_node(self, *, name):
        return self.nodes[name]
    def add_network(self, *, name, type):
        assert name not in self.networks
        net = FakeNetwork(name, type)
        self.networks[name] = net
        return net
    add_l2network = add_l3network = add_network

for filename in sys.argv[1:]:
    with open(filename) as stream:
        document = yaml.safe_load(stream)
    topology = models.SiteTopology.from_yaml_dict(document)
    for node in topology.site_topology_nodes.nodes.values():
        for key, fpga in node.pci.fpga.items():
            assert key == fpga.name
            assert len(fpga.interfaces) <= 2
            for iface in fpga.interfaces.values():
                assert topology.get_network_by_name(iface.binding) is not None
                assert (key, iface) in node.get_interfaces_for_network(iface.binding)
    for reverse in (False, True):
        fake_nodes = {}
        for node in topology.site_topology_nodes.nodes.values():
            fake = FakeNode(node.hostname, reverse)
            namespace["add_fpgas_to_node"](fake, node)
            assert fake.allocations == [(f.name, f.model) for f in node.pci.fpga.values()]
            for family in (node.pci.network, node.pci.dpu):
                for component in family.values():
                    fake.add_component(name=component.name, model=component.model)
            fake_nodes[node.hostname] = fake
        slice = FakeSlice(fake_nodes)
        networks = namespace["create_and_bind_networks"](topology, slice)
        expected = {n.name: [] for n in topology.site_topology_networks.networks.values()}
        for node in topology.site_topology_nodes.nodes.values():
            for family in (node.pci.network, node.pci.dpu, node.pci.fpga):
                for component in family.values():
                    for index, iface in enumerate(component.interfaces.values()):
                        # iface1 binds the first enumerated provider interface;
                        # reversed enumeration deliberately selects p2 first.
                        port = ("p2", "p1")[index] if reverse else ("p1", "p2")[index]
                        expected[iface.binding].append((node.hostname, component.name, port))
        assert {name: n.members for name, n in networks.items()} == expected
        assert {name: n.service for name, n in networks.items()} == {
            n.name: n.type for n in topology.site_topology_networks.networks.values()}
        for node in topology.site_topology_nodes.nodes.values():
            assert len(fake_nodes[node.hostname].allocations) == len(node.pci.fpga) + len(node.pci.dpu) + len(node.pci.network)
print(f"Validated {len(sys.argv) - 1} documents; normal and reversed positional bindings; fabric-generic-cluster {dist.version}")
'''
    result = subprocess.run([python, "-c", script, *paths], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (f"Validated {len(paths)} documents; normal and reversed positional bindings; "
                                     f"fabric-generic-cluster {probe.stdout.strip()}")
