from copy import deepcopy
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from infra_model.cli import main
from infra_model.loader import load_documents
from conftest import ROOT
from test_fabric_adapter import cluster, node, nodes, render
from test_fabric_pci_devices import pci, requirement


FIXTURE = ROOT / "tests/fixtures/fabric-dpu.yaml"
CONFIG = ROOT / "adapter-configs/fabric.yaml"
MODELS = [
    ("BlueField-2-ConnectX-6", "NIC_BlueField_2_ConnectX_6"),
    ("ConnectX-7-100", "NIC_ConnectX_7_100"),
    ("ConnectX-7-400", "NIC_ConnectX_7_400"),
]


@pytest.fixture
def source():
    return [doc.data for doc in load_documents(FIXTURE)]


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


@pytest.mark.parametrize("model,target", MODELS)
@pytest.mark.parametrize("vendor", [None, "NVIDIA"])
@pytest.mark.parametrize("features", [None, []])
def test_exact_dpu_mapping_allocates_without_interfaces(source, config, model, target, vendor, features):
    constraints = {} if vendor is None else {"vendor": vendor}
    if features is not None:
        constraints["features"] = features
    node(source)["requirements"]["devices"] = [requirement("dpu", model, **constraints)]
    result = render(source, config)
    assert pci(result)["dpu"] == {"dpu1": {"name": "dpu1", "model": target}}


@pytest.mark.parametrize("count", [None, 1, 2, 3])
def test_dpu_count_is_component_count_without_port_limit_or_source_default(source, config, count):
    node(source)["requirements"]["devices"] = [requirement("dpu", "ConnectX-7-100", count=count)]
    before = deepcopy(source)
    model = InfrastructureModel.from_documents(source)
    result = render_fabric(model, config=config)
    assert pci(result)["dpu"] == {
        f"dpu{i}": {"name": f"dpu{i}", "model": "NIC_ConnectX_7_100"}
        for i in range(1, (count or 1) + 1)
    }
    assert ("count" in model.of_kind("DeviceRequirement")[0]) == (count is not None)
    assert "count:" not in yaml.safe_dump(result)
    assert source == before


def test_mixed_devices_keep_independent_counters_and_authored_order(source, config):
    node(source)["requirements"]["devices"] = [
        requirement("dpu", "ConnectX-7-100", name="gpu1", count=2),
        requirement("gpu", "A40", name="dpu1", count=2),
        requirement("fpga", "U280", name="nic1"),
        requirement("dpu", "BlueField-2-ConnectX-6", name="another-dpu"),
        requirement("storage", "P4510", name="nvme", protocol="nvme"),
        requirement("dpu", "ConnectX-7-400", name="last-dpu"),
        requirement("gpu", "A30", name="last-gpu"),
    ]
    result = render(source, config)
    for family, models in {
        "dpu": ["NIC_ConnectX_7_100", "NIC_ConnectX_7_100", "NIC_BlueField_2_ConnectX_6", "NIC_ConnectX_7_400"],
        "gpu": ["GPU_A40", "GPU_A40", "GPU_A30"],
        "fpga": ["FPGA_Xilinx_U280"],
        "nvme": ["NVME_P4510"],
    }.items():
        entries = pci(result)[family]
        assert list(entries) == [f"{family}{i}" for i in range(1, len(models) + 1)]
        assert [entry["model"] for entry in entries.values()] == models
        assert all(key == entry["name"] and set(entry) == {"name", "model"} for key, entry in entries.items())
    names = [name for entries in pci(result).values() for name in entries]
    assert len(names) == len(set(names))
    assert pci(result)["network"]["nic1"]["model"] == "NIC_Basic"


def test_dpu_numbering_restarts_on_each_node(source, config):
    second = deepcopy(node(source))
    second["name"] = "vm-b"
    cluster(source)["spec"]["nodes"].append(second)
    result = render(source, config)
    assert pci(result, "node1")["dpu"] == pci(result, "node2")["dpu"]


def test_dpu_fixture_changes_only_dpu_allocation_without_implicit_network_binding(source, config):
    baseline_source = deepcopy(source)
    node(baseline_source)["requirements"].pop("devices")
    baseline = render(baseline_source, config)
    result = render(source, config)
    assert pci(baseline)["dpu"] == {}
    assert pci(result)["dpu"] == {
        "dpu1": {"name": "dpu1", "model": "NIC_ConnectX_7_100"},
        "dpu2": {"name": "dpu2", "model": "NIC_ConnectX_7_100"},
    }
    pci(result)["dpu"] = {}
    assert result == baseline


@pytest.mark.parametrize("constraints,path", [
    ({}, "/model"),
    ({"vendor": "NVIDIA"}, "/model"),
    ({"model": "BlueField"}, "/model"),
    ({"model": "BlueField-2"}, "/model"),
    ({"model": "BlueField-3"}, "/model"),
    ({"model": "ConnectX-7"}, "/model"),
    ({"model": "ConnectX-6"}, "/model"),
    ({"model": "A40"}, "/model"),
    ({"model": "NIC_ConnectX_7_100"}, "/model"),
    ({"model": "connectx-7-100"}, "/model"),
    ({"model": "ConnectX-7-100 "}, "/model"),
    ({"model": "ConnectX-7-100", "vendor": "Intel"}, "/vendor"),
    ({"model": "BlueField-2-ConnectX-6", "vendor": "Mellanox"}, "/vendor"),
    ({"model": "ConnectX-7-400", "vendor": "nvidia"}, "/vendor"),
])
def test_dpu_requires_exact_model_and_compatible_vendor(source, config, constraints, path):
    node(source)["requirements"]["devices"] = [requirement("dpu", **constraints)]
    with pytest.raises(FabricTranslationError) as caught:
        render(source, config)
    assert caught.value.entity == "cluster/dpu-allocation/node/vm-a/requirement/device/device"
    assert caught.value.path == "/spec/nodes/0/requirements/devices/0/constraints" + path


@pytest.mark.parametrize("model,target", MODELS)
def test_dpu_nonempty_features_are_not_inferred_from_model(source, config, model, target):
    node(source)["requirements"]["devices"] = [requirement("dpu", model, features=["offload"])]
    with pytest.raises(FabricTranslationError, match="device feature requirements are unsupported") as caught:
        render(source, config)
    assert caught.value.path.endswith("/constraints/features")


@pytest.mark.parametrize("constraints", [{"protocol": "nvme"}, {"minCapacity": {"value": 1, "unit": "TB"}}])
def test_dpu_storage_constraints_still_fail_core_validation(source, config, constraints):
    node(source)["requirements"]["devices"] = [requirement("dpu", "ConnectX-7-100", **constraints)]
    with pytest.raises(FabricTranslationError, match="core validation failed"):
        render(source, config)


@pytest.mark.parametrize("model,target", MODELS)
@pytest.mark.parametrize("with_device", [False, True])
@pytest.mark.parametrize("group", [None, "dpu-ports"])
def test_dpu_attachments_fail_even_with_matching_device_and_group(source, config, model, target, with_device, group):
    node(source)["requirements"]["devices"] = ([requirement("dpu", model, name="management", vendor="NVIDIA")]
                                               if with_device else [])
    attachment = node(source)["networkAttachments"][0]
    attachment["interfaceRequirements"]["adapter"] = {"class": "dpu", "vendor": "NVIDIA", "model": model}
    if group is not None:
        attachment["adapterGroup"] = group
        second = deepcopy(attachment)
        second["name"] = "second"
        node(source)["networkAttachments"].append(second)
    with pytest.raises(FabricTranslationError, match="only standard adapter class") as caught:
        render(source, config)
    assert caught.value.entity.endswith("/network-attachment/management")
    assert caught.value.path.endswith("/interfaceRequirements/adapter/class")


def test_dpu_allocation_remains_offline_and_preserves_worker_site_image(source, config, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("offline DPU rendering attempted a network operation")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.delenv("FABRIC_PROJECT_ID", raising=False)
    expected = nodes(render(source, config))["node1"]
    source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Server", "metadata": {"name": "host"},
                   "spec": {"siteRef": "site/lab-site", "networkAdapters": []}})
    node(source)["placement"] = {"hostRef": "server/host"}
    config_data = yaml.safe_load(CONFIG.read_text())
    config_data["workers"] = {"server/host": "provider-host"}
    actual = nodes(render(source, FabricAdapterConfig.from_dict(config_data)))["node1"]
    expected["worker"] = "provider-host"
    assert actual == expected


@pytest.mark.parametrize("entrypoint", ["module", "script"])
def test_dpu_fixture_cli_is_byte_deterministic_and_allocation_only(tmp_path, entrypoint):
    command = ([sys.executable, "-m", "infra_model"] if entrypoint == "module"
               else [str(Path(sys.executable).with_name("infra-model"))])
    command += ["render", "fabric", str(FIXTURE), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout
    assert b"count:" not in first.stdout
    dpus = pci(yaml.safe_load(first.stdout))["dpu"]
    assert list(dpus) == ["dpu1", "dpu2"]
    assert all(key == value["name"] and set(value) == {"name", "model"} for key, value in dpus.items())


def test_invalid_dpu_cli_emits_no_partial_allocation(source, tmp_path, capsys):
    node(source)["requirements"]["devices"].append(requirement("dpu", "BlueField-3", name="ambiguous"))
    path = tmp_path / "invalid-dpu.yaml"
    path.write_text(yaml.safe_dump_all(source))
    assert main(["render", "fabric", str(path), "--config", str(CONFIG)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dpu device requirements need an exact supported model" in captured.err
