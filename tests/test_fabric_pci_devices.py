from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

from infra_model import InfrastructureModel
from infra_model.adapters import fabric
from infra_model.adapters.fabric import FabricAdapterConfig, FabricTranslationError, render_fabric
from infra_model.adapters.fabric.translator import _translate
from infra_model.cli import main
from infra_model.loader import load_documents
from conftest import ROOT
from test_fabric_adapter import cluster, node, nodes, render


FIXTURE = ROOT / "tests/fixtures/fabric-pci-devices.yaml"
CONFIG = ROOT / "adapter-configs/fabric.yaml"


@pytest.fixture
def source():
    return [doc.data for doc in load_documents(FIXTURE)]


@pytest.fixture
def config():
    return FabricAdapterConfig.from_file(CONFIG)


def requirement(kind, model=None, *, name="device", count=None, **constraints):
    if model is not None:
        constraints["model"] = model
    data = {"name": name, "type": kind, "constraints": constraints}
    if count is not None:
        data["count"] = count
    return data


def pci(document, key="node1"):
    return nodes(document)[key]["pci"]


@pytest.mark.parametrize("model,target", [
    ("Tesla T4", "GPU_TeslaT4"), ("RTX6000", "GPU_RTX6000"), ("A30", "GPU_A30"), ("A40", "GPU_A40"),
])
@pytest.mark.parametrize("vendor", [None, "NVIDIA"])
def test_exact_gpu_models(source, config, model, target, vendor):
    constraints = {} if vendor is None else {"vendor": vendor}
    node(source)["requirements"]["devices"] = [requirement("gpu", model, **constraints)]
    result = render(source, config)
    assert pci(result)["gpu"] == {"gpu1": {"name": "gpu1", "model": target}}
    assert "GPU_Tesla_T4" not in yaml.safe_dump(result)


@pytest.mark.parametrize("model,target", [
    ("U280", "FPGA_Xilinx_U280"), ("Alveo U280", "FPGA_Xilinx_U280"), ("SN1022", "FPGA_Xilinx_SN1022"),
])
@pytest.mark.parametrize("vendor", [None, "AMD", "Xilinx"])
def test_exact_fpga_models_and_explicit_aliases_allocate_without_interfaces(source, config, model, target, vendor):
    constraints = {} if vendor is None else {"vendor": vendor}
    node(source)["requirements"]["devices"] = [requirement("fpga", model, **constraints)]
    assert pci(render(source, config))["fpga"] == {"fpga1": {"name": "fpga1", "model": target}}


@pytest.mark.parametrize("vendor", [None, "Intel"])
def test_exact_nvme_requires_storage_protocol(source, config, vendor):
    constraints = {} if vendor is None else {"vendor": vendor}
    node(source)["requirements"]["devices"] = [requirement("storage", "P4510", protocol="nvme", **constraints)]
    result = render(source, config)
    assert pci(result)["nvme"] == {"nvme1": {"name": "nvme1", "model": "NVME_P4510"}}
    assert nodes(result)["node1"]["persistent_storage"] == {"volume": {}}
    assert nodes(result)["node1"]["capacity"]["disk"] == 20


@pytest.mark.parametrize("kind,model,family,target,constraints", [
    ("gpu", "A40", "gpu", "GPU_A40", {}),
    ("fpga", "U280", "fpga", "FPGA_Xilinx_U280", {}),
    ("storage", "P4510", "nvme", "NVME_P4510", {"protocol": "nvme"}),
])
@pytest.mark.parametrize("count", [None, 1, 2])
def test_count_expands_to_distinct_named_entries_without_materializing_source_default(source, config, kind, model, family, target, constraints, count):
    requested = requirement(kind, model, count=count, **constraints)
    node(source)["requirements"]["devices"] = [requested]
    before = deepcopy(source)
    normalized = InfrastructureModel.from_documents(source)
    result = render_fabric(normalized, config=config)
    assert pci(result)[family] == {f"{family}{i}": {"name": f"{family}{i}", "model": target}
                                   for i in range(1, (count or 1) + 1)}
    assert ("count" in normalized.of_kind("DeviceRequirement")[0]) == (count is not None)
    assert "count:" not in yaml.safe_dump(result)
    assert source == before


def test_interleaved_requests_expand_in_order_with_independent_family_counters(source, config):
    node(source)["requirements"]["devices"] = [
        requirement("gpu", "A40", name="nic1", count=2),
        requirement("fpga", "U280", name="gpu1"),
        requirement("gpu", "A30", name="later-gpu"),
        requirement("storage", "P4510", name="fpga1", count=2, protocol="nvme"),
        requirement("fpga", "SN1022", name="later-fpga", count=2),
        requirement("gpu", "RTX6000", name="last-gpu"),
        requirement("storage", "P4510", name="last-nvme", protocol="nvme"),
    ]
    result = render(source, config)
    devices = pci(result)
    for family, expected in {
        "gpu": ["GPU_A40", "GPU_A40", "GPU_A30", "GPU_RTX6000"],
        "fpga": ["FPGA_Xilinx_U280", "FPGA_Xilinx_SN1022", "FPGA_Xilinx_SN1022"],
        "nvme": ["NVME_P4510"] * 3,
    }.items():
        assert list(devices[family]) == [f"{family}{i}" for i in range(1, len(expected) + 1)]
        assert [entry["model"] for entry in devices[family].values()] == expected
        assert all(key == entry["name"] and set(entry) == {"name", "model"} for key, entry in devices[family].items())
    names = [name for entries in devices.values() for name in entries]
    assert len(names) == len(set(names))
    assert devices["network"]["nic1"]["model"] == "NIC_Basic"
    assert "count:" not in yaml.safe_dump(result)


def test_generated_device_names_are_local_to_each_node(source, config):
    second = deepcopy(node(source))
    second["name"] = "vm-b"
    cluster(source)["spec"]["nodes"].append(second)
    result = render(source, config)
    assert pci(result, "node1") == pci(result, "node2")


def test_mixed_fixture_adds_only_requested_pci_devices_and_keeps_network_rendering(source, config):
    baseline_source = deepcopy(source)
    node(baseline_source)["requirements"].pop("devices")
    baseline = render(baseline_source, config)
    result = render(source, config)
    assert pci(result)["gpu"] == {"gpu1": {"name": "gpu1", "model": "GPU_A40"}, "gpu2": {"name": "gpu2", "model": "GPU_A40"}}
    assert pci(result)["fpga"] == {"fpga1": {"name": "fpga1", "model": "FPGA_Xilinx_U280"}}
    assert pci(result)["nvme"] == {"nvme1": {"name": "nvme1", "model": "NVME_P4510"}}
    assert pci(result)["dpu"] == {}
    for family in ("gpu", "fpga", "nvme"):
        pci(result)[family] = {}
    assert result == baseline


def test_device_plan_is_private_immutable_and_source_extensions_remain_opaque(source, config):
    for device in node(source)["requirements"]["devices"]:
        device["extensions"] = {"example.org/opaque": {"model": "uninterpreted"}}
        device["status"] = {"extensions": {"example.org/observation": "uninterpreted"}}
    before = deepcopy(source)
    model = InfrastructureModel.from_documents(source)
    plan = _translate(model, cluster=None, config=config)
    assert [(device.name, device.model) for device in plan.nodes[0].gpus] == [("gpu1", "GPU_A40"), ("gpu2", "GPU_A40")]
    assert isinstance(plan.nodes[0].gpus, tuple)
    with pytest.raises(FrozenInstanceError):
        plan.nodes[0].gpus[0].name = "changed"
    assert fabric.__all__ == ["FabricAdapterConfig", "FabricTranslationError", "render_fabric"]
    assert not hasattr(fabric, "_FabricDevicePlan")
    first, second = [render_fabric(model, config=config) for _ in range(2)]
    assert first == second
    assert "uninterpreted" not in yaml.safe_dump(first)
    assert source == before


@pytest.mark.parametrize("requested,path", [
    (requirement("gpu"), "/constraints/model"),
    (requirement("gpu", vendor="NVIDIA"), "/constraints/model"),
    (requirement("gpu", "A100"), "/constraints/model"),
    (requirement("gpu", "a40"), "/constraints/model"),
    (requirement("gpu", "A40 "), "/constraints/model"),
    (requirement("gpu", "TeslaT4"), "/constraints/model"),
    (requirement("gpu", "GPU_A40"), "/constraints/model"),
    (requirement("gpu", "GPU_Tesla_T4"), "/constraints/model"),
    (requirement("gpu", "U280"), "/constraints/model"),
    (requirement("gpu", "A40", vendor="AMD"), "/constraints/vendor"),
    (requirement("gpu", "A40", vendor="nvidia"), "/constraints/vendor"),
    (requirement("fpga"), "/constraints/model"),
    (requirement("fpga", vendor="Xilinx"), "/constraints/model"),
    (requirement("fpga", "U250"), "/constraints/model"),
    (requirement("fpga", "Alveo U280 card"), "/constraints/model"),
    (requirement("fpga", "sn1022"), "/constraints/model"),
    (requirement("fpga", "A40"), "/constraints/model"),
    (requirement("fpga", "U280", vendor="Intel"), "/constraints/vendor"),
    (requirement("fpga", "SN1022", vendor="NVIDIA"), "/constraints/vendor"),
    (requirement("storage", "P4510"), "/constraints/protocol"),
    (requirement("storage", "P4510", protocol="sata"), "/constraints/protocol"),
    (requirement("storage", protocol="nvme"), "/constraints/model"),
    (requirement("storage", "P4610", protocol="nvme"), "/constraints/model"),
    (requirement("storage", "NVME_P4510", protocol="nvme"), "/constraints/model"),
    (requirement("storage", "P4510", protocol="nvme", vendor="Samsung"), "/constraints/vendor"),
    (requirement("storage", "P4510", protocol="nvme", vendor="intel"), "/constraints/vendor"),
    (requirement("dpu", "BlueField"), "/constraints/model"),
    (requirement("other", "A40"), "/type"),
])
def test_unsupported_requests_fail_per_requirement_without_fallback(source, config, requested, path):
    node(source)["requirements"]["devices"] = [requirement("gpu", "A40", name="supported"), requested]
    model = InfrastructureModel.from_documents(source)
    model.validate()
    with pytest.raises(FabricTranslationError) as caught:
        render_fabric(model, config=config)
    assert caught.value.entity == "cluster/pci-devices/node/vm-a/requirement/device/device"
    assert caught.value.source == "memory#document=3"
    assert caught.value.path == "/spec/nodes/0/requirements/devices/1" + path


@pytest.mark.parametrize("kind,model,constraints", [
    ("gpu", "A40", {}), ("fpga", "U280", {}), ("storage", "P4510", {"protocol": "nvme"}),
])
@pytest.mark.parametrize("features", [[], ["rdma"]])
def test_exact_model_does_not_silently_satisfy_feature_constraints(source, config, kind, model, constraints, features):
    node(source)["requirements"]["devices"] = [requirement(kind, model, features=features, **constraints)]
    if features:
        with pytest.raises(FabricTranslationError, match="device feature requirements"):
            render(source, config)
    else:
        render(source, config)


def test_nvme_min_capacity_is_not_discarded(source, config):
    node(source)["requirements"]["devices"] = [requirement("storage", "P4510", protocol="nvme", minCapacity={"value": 1, "unit": "TB"})]
    with pytest.raises(FabricTranslationError, match="minCapacity"):
        render(source, config)


@pytest.mark.parametrize("mutation", [
    {"type": "nvme"}, {"count": 0}, {"count": 1.5}, {"vendor": "NVIDIA"},
    {"constraints": {"protocol": "nvme", "model": "P4510", "medium": "ssd"}},
])
def test_core_invalid_device_shapes_still_fail(source, config, mutation):
    node(source)["requirements"]["devices"] = [requirement("storage", "P4510", protocol="nvme")]
    node(source)["requirements"]["devices"][0].update(mutation)
    with pytest.raises(FabricTranslationError, match="core validation failed"):
        render(source, config)


def test_devices_are_resolved_only_for_selected_cluster(source, config):
    other = deepcopy(cluster(source))
    other["metadata"]["name"] = "other"
    other["spec"]["nodes"][0]["requirements"]["devices"] = [requirement("dpu")]
    source.append(other)
    assert pci(render(source, config, cluster="pci-devices"))["gpu"]["gpu1"]["model"] == "GPU_A40"
    with pytest.raises(FabricTranslationError, match="dpu device requirements"):
        render(source, config, cluster="other")


def test_offline_devices_do_not_probe_inventory_or_permissions_and_keep_host_mapping(source, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("offline rendering attempted a network operation")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.delenv("FABRIC_PROJECT_ID", raising=False)
    source.append({"apiVersion": "infra.model/v0alpha1", "kind": "Server", "metadata": {"name": "host"},
                   "spec": {"siteRef": "site/lab-site", "accelerators": [], "storage": {"devices": []}}})
    node(source)["placement"] = {"hostRef": "server/host"}
    config_data = yaml.safe_load(CONFIG.read_text())
    config_data["workers"] = {"server/host": "provider-host"}
    result = render(source, FabricAdapterConfig.from_dict(config_data))
    assert nodes(result)["node1"]["worker"] == "provider-host"
    assert pci(result)["gpu"]["gpu1"]["model"] == "GPU_A40"


@pytest.mark.parametrize("entrypoint", ["module", "script"])
def test_pci_fixture_cli_renders_byte_identically_with_matching_names_and_no_count(tmp_path, entrypoint):
    command = ([sys.executable, "-m", "infra_model"] if entrypoint == "module"
               else [str(Path(sys.executable).with_name("infra-model"))])
    command += ["render", "fabric", str(FIXTURE), "--config", str(CONFIG)]
    first, second = [subprocess.run(command, cwd=tmp_path, capture_output=True) for _ in range(2)]
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert first.stdout == second.stdout
    assert b"count:" not in first.stdout and b"UNKNOWN" not in first.stdout
    assert first.stderr.count(b"UNKNOWN") == second.stderr.count(b"UNKNOWN") == 1
    devices = pci(yaml.safe_load(first.stdout))
    assert list(devices["gpu"]) == ["gpu1", "gpu2"]
    for family in ("gpu", "fpga", "nvme", "network"):
        assert all(key == value["name"] for key, value in devices[family].items())


def test_invalid_device_cli_does_not_emit_partial_yaml(source, tmp_path, capsys):
    node(source)["requirements"]["devices"].append(requirement("dpu", name="unsupported"))
    path = tmp_path / "unsupported.yaml"
    path.write_text(yaml.safe_dump_all(source))
    assert main(["render", "fabric", str(path), "--config", str(CONFIG)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "/requirements/devices/3/constraints/model" in captured.err
    assert "dpu device requirements need an exact supported model" in captured.err
