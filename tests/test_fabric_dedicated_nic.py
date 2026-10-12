from copy import deepcopy
from dataclasses import FrozenInstanceError
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
from test_fabric_adapter import cluster, networks, node, nodes, render


FIXTURE = ROOT / "tests/fixtures/fabric-dedicated-nic.yaml"
CONFIG = ROOT / "adapter-configs/fabric.yaml"


@pytest.fixture(params=[("ConnectX-5", "NIC_ConnectX_5"), ("ConnectX-6", "NIC_ConnectX_6")])
def dedicated_model(request):
    return request.param


@pytest.fixture
def source(dedicated_model):
    documents = [doc.data for doc in load_documents(FIXTURE)]
    node(documents)["networkAttachments"][1]["interfaceRequirements"]["adapter"]["model"] = dedicated_model[0]
    return documents


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


def components(document, key="node1"):
    return nodes(document)[key]["pci"]["network"]


def dedicated_attachment(name, group=None, adapter=None, *, model="ConnectX-6"):
    item = {"name": name, "networkRef": "network/data", "interfaceRequirements": {
        "type": "ethernet", "adapter": adapter if adapter is not None else {
            "class": "standard", "vendor": "NVIDIA", "model": model},
    }}
    if group is not None:
        item["adapterGroup"] = group
    return item


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("group", [None, "hs1"])
@pytest.mark.parametrize("vendor", [None, "NVIDIA"])
def test_exact_dedicated_models_grouped_and_ungrouped(source, config, dedicated_model, count, group, vendor):
    identity = {"class": "standard", "model": dedicated_model[0]}
    if vendor is not None:
        identity["vendor"] = vendor
    node(source)["networkAttachments"] = [
        dedicated_attachment(f"data-{i}", group, identity) for i in range(count)]
    result = components(render(source, config))
    assert list(result) == (["nic1"] if group else [f"nic{i + 1}" for i in range(count)])
    assert all(item["model"] == dedicated_model[1] for item in result.values())
    assert all(key == item["name"] for key, item in result.items())
    assert [list(item["interfaces"]) for item in result.values()] == (
        [[f"iface{i + 1}" for i in range(count)]] if group else [["iface1"]] * count)
    interfaces = [interface for component in result.values() for interface in component["interfaces"].values()]
    assert interfaces == [{"device": f"eth{i + 1}", "connection": f"conn-eth{i + 1}", "binding": "data"}
                          for i in range(count)]


@pytest.mark.parametrize("partial", [
    {"class": "standard", "vendor": "NVIDIA"}, {"class": "standard"}, None,
    "same-model-with-vendor",
])
@pytest.mark.parametrize("reverse", [False, True])
def test_group_merges_partial_identity_before_resolving(source, config, dedicated_model, partial, reverse):
    first = dedicated_attachment("first", "hs1", {"class": "standard", "model": dedicated_model[0]})
    second = dedicated_attachment("second", "hs1")
    if partial == "same-model-with-vendor":
        partial = {"class": "standard", "vendor": "NVIDIA", "model": dedicated_model[0]}
    if partial is None:
        del second["interfaceRequirements"]["adapter"]
    else:
        second["interfaceRequirements"]["adapter"] = partial
    node(source)["networkAttachments"] = [second, first] if reverse else [first, second]
    before = deepcopy(source)
    result = components(render(source, config))
    assert list(result) == ["nic1"]
    assert result["nic1"]["model"] == dedicated_model[1]
    assert list(result["nic1"]["interfaces"]) == ["iface1", "iface2"]
    assert source == before


def test_distinct_groups_and_same_key_on_other_nodes_are_independent(source, config, dedicated_model):
    node(source)["networkAttachments"] = [
        dedicated_attachment(name, group, model=dedicated_model[0])
        for name, group in [("data-a", "hs1"), ("storage-a", "hs2"), ("data-b", "hs1"), ("storage-b", "hs2")]
    ]
    second = deepcopy(node(source))
    second["name"] = "vm-b"
    cluster(source)["spec"]["nodes"].append(second)
    result = render(source, config)
    assert components(result, "node1") == components(result, "node2")
    assert list(components(result)) == ["nic1", "nic2"]
    assert all(item["model"] == dedicated_model[1] for item in components(result).values())
    assert all(list(item["interfaces"]) == ["iface1", "iface2"] for item in components(result).values())
    assert [[interface["device"] for interface in component["interfaces"].values()]
            for component in components(result).values()] == [["eth1", "eth3"], ["eth2", "eth4"]]


@pytest.mark.parametrize("interleaved", [False, True])
def test_mixed_components_follow_first_creation_and_guest_names_follow_attachment_order(source, config, dedicated_model, interleaved):
    attachments = node(source)["networkAttachments"]
    if interleaved:
        # data, management, storage: group interfaces get eth1 and eth3.
        attachments[:] = [attachments[1], attachments[0], attachments[2]]
    before = deepcopy(source)
    model = InfrastructureModel.from_documents(source)
    plan = _translate(model, cluster=None, config=config)
    assert [[item.attachment_ordinal for item in component.interfaces] for component in plan.nodes[0].components] == (
        [[1, 3], [2]] if interleaved else [[1], [2, 3]])
    with pytest.raises(FrozenInstanceError):
        plan.nodes[0].components[0].interfaces[0].attachment_ordinal = 9
    result = render_fabric(model, config=config)
    nics = components(result)
    assert list(nics) == ["nic1", "nic2"]
    assert [nic["name"] for nic in nics.values()] == ["nic1", "nic2"]
    assert [nic["model"] for nic in nics.values()] == (
        [dedicated_model[1], "NIC_Basic"] if interleaved else ["NIC_Basic", dedicated_model[1]])
    dedicated = nics["nic1" if interleaved else "nic2"]["interfaces"]
    assert list(dedicated) == ["iface1", "iface2"]
    assert dedicated["iface1"]["device"] == ("eth1" if interleaved else "eth2")
    assert dedicated["iface1"]["connection"] == ("conn-eth1" if interleaved else "conn-eth2")
    assert dedicated["iface2"]["device"] == "eth3"
    assert dedicated["iface2"]["connection"] == "conn-eth3"
    assert [net["name"] for net in networks(result).values()] == (
        ["data", "management", "storage"] if interleaved else ["management", "data", "storage"])
    assert render_fabric(model, config=config) == result
    assert source == before


def test_shared_component_interfaces_keep_independent_addressing(source, config):
    document = render(source, config)
    interfaces = components(document)["nic2"]["interfaces"]
    assert interfaces["iface1"] == {
        "device": "eth2", "connection": "conn-eth2", "binding": "data",
        "ipv4": {"address": "198.51.100.11/24", "gateway": "198.51.100.1", "dns": "198.51.100.53"},
        "ipv6": {"address": "2001:db8:1::11/64", "gateway": "fe80::1", "dns": "2001:db8:1::53"},
    }
    assert interfaces["iface2"] == {
        "device": "eth3", "connection": "conn-eth3", "binding": "storage",
        "ipv4": {"address": "203.0.113.11/24", "dns": "203.0.113.53"},
        "ipv6": {"address": "2001:db8:2::11/64", "dns": "2001:db8:2::53"},
    }
    assert all(net["type"] == "L2Bridge" for net in networks(document).values())


def test_dedicated_interfaces_support_two_site_l2sts(config, dedicated_model):
    source = [doc.data for doc in load_documents(ROOT / "examples/multi-site")]
    for item in cluster(source)["spec"]["nodes"]:
        item["networkAttachments"][0]["interfaceRequirements"]["adapter"] = {"class": "standard", "model": dedicated_model[0]}
        item["networkAttachments"][0]["adapterGroup"] = "hs1"
    document = render(source, config)
    assert networks(document)["net1"]["type"] == "L2STS"
    assert [item["site"] for item in nodes(document).values()] == ["SRI", "UKY"]
    assert all(components(document, key)["nic1"]["model"] == dedicated_model[1] for key in nodes(document))


@pytest.mark.parametrize("identity", [
    {"class": "standard", "vendor": "Intel", "model": "ConnectX-6"},
    {"class": "standard", "vendor": "Mellanox", "model": "ConnectX-6"},
    {"class": "standard", "vendor": "nvidia", "model": "ConnectX-6"},
    {"class": "standard", "vendor": "NVIDIA"},
    {"class": "standard", "vendor": "Intel", "model": "ConnectX-5"},
    {"class": "standard", "vendor": "Mellanox", "model": "ConnectX-5"},
    {"class": "standard", "vendor": "nvidia", "model": "ConnectX-5"},
    {"class": "standard", "model": "connectx-5"},
    {"class": "standard", "model": "ConnectX-5 "},
    {"class": "standard", "model": "ConnectX_5"},
    {"class": "standard", "model": "NIC_ConnectX_5"},
    {"class": "smartnic", "model": "ConnectX-5"},
    {"class": "dpu", "model": "ConnectX-5"},
    {"class": "standard", "model": "ConnectX-7"},
    {"class": "standard", "model": "connectx-6"},
    {"class": "standard", "model": "Other"},
    {"class": "smartnic", "model": "ConnectX-6"},
    {"class": "dpu", "model": "ConnectX-6"},
    {"class": "other", "model": "ConnectX-6"},
])
@pytest.mark.parametrize("group", [None, "hs1"])
def test_unsupported_identity_fails_exactly(source, config, identity, group):
    node(source)["networkAttachments"] = [dedicated_attachment("data", group, identity)]
    with pytest.raises(FabricTranslationError) as caught:
        render(source, config)
    assert caught.value.entity == "cluster/dedicated-nic/node/vm-a/network-attachment/data"
    assert caught.value.path.startswith("/spec/nodes/0/networkAttachments/0/")
    assert "core validation failed" not in caught.value.reason


@pytest.mark.parametrize("count", [1, 2])
def test_group_without_dedicated_identity_cannot_be_rendered_as_basic(source, config, count):
    node(source)["networkAttachments"] = [{
        "name": f"data-{i}", "networkRef": "network/data", "adapterGroup": "hs1",
        "interfaceRequirements": {"type": "ethernet"},
    } for i in range(count)]
    with pytest.raises(FabricTranslationError, match="adapterGroup 'hs1'.*requires exact model ConnectX-5 or ConnectX-6"):
        render(source, config)


def test_provider_port_limit_does_not_restrict_core_or_split_group(source, config, dedicated_model):
    node(source)["networkAttachments"] = [
        dedicated_attachment(f"data-{i}", "hs1", model=dedicated_model[0]) for i in range(3)]
    model = InfrastructureModel.from_documents(source)
    assert len(model.validate().warnings) == 1
    with pytest.raises(FabricTranslationError, match="3 attachments.*at most two interfaces") as caught:
        render_fabric(model, config=config)
    assert caught.value.path.endswith("/adapterGroup")
    assert "hs1" in caught.value.reason
    assert dedicated_model[1] in caught.value.reason


@pytest.mark.parametrize("requirement", [
    {"minSpeed": {"value": 1, "unit": "Gbps"}},
    {"features": ["rdma"]}, {"features": ["sriov"]}, {"features": ["other-feature"]},
    {"type": "infiniband"},
])
@pytest.mark.parametrize("group", [None, "hs1"])
def test_specialization_on_any_member_still_fails(source, config, dedicated_model, requirement, group):
    items = [dedicated_attachment(name, group, model=dedicated_model[0]) for name in ("data", "storage")]
    items[1]["interfaceRequirements"].update(requirement)
    node(source)["networkAttachments"] = items
    with pytest.raises(FabricTranslationError, match="interface requirement") as caught:
        render(source, config)
    assert caught.value.entity.endswith("/network-attachment/storage")


def test_empty_features_add_no_constraint_to_dedicated_identity(source, config, dedicated_model):
    node(source)["networkAttachments"][1]["interfaceRequirements"]["features"] = []
    assert components(render(source, config))["nic2"]["model"] == dedicated_model[1]


@pytest.mark.parametrize("group", [None, "hs1"])
def test_dedicated_selector_still_requires_class(source, config, dedicated_model, group):
    node(source)["networkAttachments"] = [dedicated_attachment(
        "data", group, {"model": dedicated_model[0]})]
    with pytest.raises(FabricTranslationError, match="core validation failed.*class") as caught:
        render(source, config)
    assert caught.value.path.endswith("/interfaceRequirements/adapter")


@pytest.mark.parametrize("field,conflict", [("model", "other-model"), ("vendor", "Intel")])
@pytest.mark.parametrize("reverse", [False, True])
def test_group_identity_conflicts_are_rejected_by_core(source, config, dedicated_model, field, conflict, reverse):
    if field == "model":
        conflict = "ConnectX-6" if dedicated_model[0] == "ConnectX-5" else "ConnectX-5"
    first = dedicated_attachment("data", "hs1", model=dedicated_model[0])
    second = deepcopy(first)
    second["name"] = "storage"
    second["interfaceRequirements"]["adapter"][field] = conflict
    node(source)["networkAttachments"] = [second, first] if reverse else [first, second]
    with pytest.raises(FabricTranslationError, match="core validation failed.*adapter_group_conflict") as caught:
        render(source, config)
    assert caught.value.path.endswith(f"/interfaceRequirements/adapter/{field}")
    assert "hs1" in caught.value.reason


@pytest.mark.parametrize("group", [None, "hs1"])
def test_mixed_dedicated_models_keep_creation_and_guest_order(source, config, dedicated_model, group):
    other_model, other_target = (("ConnectX-6", "NIC_ConnectX_6") if dedicated_model[0] == "ConnectX-5"
                                 else ("ConnectX-5", "NIC_ConnectX_5"))
    node(source)["networkAttachments"] = [
        dedicated_attachment("data", group, model=dedicated_model[0]),
        dedicated_attachment("other", model=other_model),
        dedicated_attachment("storage", group, model=dedicated_model[0]),
    ]
    result = components(render(source, config))
    assert list(result) == (["nic1", "nic2"] if group else ["nic1", "nic2", "nic3"])
    assert [item["model"] for item in result.values()] == (
        [dedicated_model[1], other_target] if group else [dedicated_model[1], other_target, dedicated_model[1]])
    assert all(key == item["name"] for key, item in result.items())
    assert [[(iface["device"], iface["connection"]) for iface in item["interfaces"].values()]
            for item in result.values()] == (
        [[("eth1", "conn-eth1"), ("eth3", "conn-eth3")], [("eth2", "conn-eth2")]] if group else
        [[("eth1", "conn-eth1")], [("eth2", "conn-eth2")], [("eth3", "conn-eth3")]])


def test_connectx5_fixture_changes_only_dedicated_provider_model(config):
    baseline = render([doc.data for doc in load_documents(FIXTURE)], config)
    source = [doc.data for doc in load_documents(ROOT / "tests/fixtures/fabric-connectx5.yaml")]
    actual = render(source, config)
    assert components(actual)["nic2"]["model"] == "NIC_ConnectX_5"
    components(baseline)["nic2"]["model"] = "NIC_ConnectX_5"
    assert actual == baseline


@pytest.mark.parametrize("override,reason", [
    ({"connectivity": "point-to-point"}, "point-to-point"), ({"layer": "layer3"}, "layer2"),
])
def test_dedicated_identity_does_not_enable_layer3_or_incomplete_point_to_point(source, config, override, reason):
    next(doc for doc in source if doc["kind"] == "Network" and doc["metadata"]["name"] == "data")["spec"].update(override)
    with pytest.raises(FabricTranslationError, match=reason):
        render(source, config)


@pytest.mark.parametrize("fixture,target", [
    ("fabric-dedicated-nic.yaml", "NIC_ConnectX_6"), ("fabric-connectx5.yaml", "NIC_ConnectX_5"),
])
def test_fixture_cli_deterministic(tmp_path, fixture, target):
    path = ROOT / "tests/fixtures" / fixture
    command = [sys.executable, "-m", "infra_model", "render", "fabric", str(path), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout
    assert first.stderr.count(b"UNKNOWN") == second.stderr.count(b"UNKNOWN") == 1
    assert b"UNKNOWN" not in first.stdout
    nics = components(yaml.safe_load(first.stdout))
    assert list(nics) == ["nic1", "nic2"]
    assert [nic["model"] for nic in nics.values()] == ["NIC_Basic", target]
    assert all(key == nic["name"] for key, nic in nics.items())
    assert [list(nic["interfaces"]) for nic in nics.values()] == [["iface1"], ["iface1", "iface2"]]


def test_invalid_group_cli_does_not_emit_partial_yaml(source, dedicated_model, tmp_path, capsys):
    node(source)["networkAttachments"] = [
        dedicated_attachment(f"data-{i}", "hs1", model=dedicated_model[0]) for i in range(3)]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump_all(source))
    assert main(["render", "fabric", str(path), "--config", str(CONFIG)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "at most two interfaces" in captured.err


def test_nic_model_resolution_does_not_expand_config_surface():
    data = yaml.safe_load(CONFIG.read_text())
    data["nicModels"] = {"ConnectX-6": "NIC_ConnectX_6"}
    with pytest.raises(FabricTranslationError):
        FabricAdapterConfig.from_dict(data)
