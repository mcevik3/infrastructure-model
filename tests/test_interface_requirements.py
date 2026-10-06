from copy import deepcopy
from decimal import Inexact, Rounded, localcontext

import pytest

from infra_model import InfrastructureModel, ValidationError
from test_device_requirements import document, error


@pytest.fixture
def interface_model():
    interface = {"name": "p1", "type": "ethernet", "capabilities": {
        "bandwidth": {"value": 100, "unit": "Gbps"}, "features": ["rdma", "sriov"]}}
    adapter = {"name": "nic", "class": "smartnic", "vendor": "NVIDIA", "model": "ConnectX-6",
               "interfaces": [interface]}
    server = {"siteRef": "site/lab", "capabilities": {"nodeTypes": ["baremetal"]},
              "networkAdapters": [adapter]}
    required = {"type": "ethernet"}
    attachment = {"name": "data", "networkRef": "network/data", "interfaceRequirements": required}
    node = {"name": "worker", "realization": {"type": "baremetal"},
            "placement": {"resourceRef": "server/host"}, "networkAttachments": [attachment]}
    docs = [document("Site", "lab", {}), document("Server", "host", server),
            document("Network", "data", {"layer": "layer2"}),
            document("Cluster", "interfaces", {"nodes": [node]})]
    return docs, server, node, adapter, interface, required


@pytest.mark.parametrize("requirement", [
    {"type": "ethernet"},
    {"type": "ethernet", "minSpeed": {"value": 100, "unit": "Gbps"}},
    {"type": "ethernet", "features": ["rdma"]},
    {"type": "ethernet", "features": ["sriov"]},
    {"type": "ethernet", "adapter": {"class": "smartnic"}},
    {"type": "ethernet", "adapter": {"class": "smartnic", "vendor": "NVIDIA", "model": "ConnectX-6"},
     "features": ["rdma", "sriov"], "minSpeed": {"value": 100, "unit": "Gbps"}},
])
def test_interface_requirements_satisfied(interface_model, requirement):
    docs, _, _, _, _, required = interface_model
    required.update(requirement)
    assert not InfrastructureModel.from_documents(docs).validate().warnings


@pytest.mark.parametrize("kind", ["ethernet", "infiniband", "other"])
def test_interface_types(interface_model, kind):
    docs, _, _, _, interface, required = interface_model
    interface["type"] = required["type"] = kind
    assert not InfrastructureModel.from_documents(docs).validate().warnings


@pytest.mark.parametrize("kind", ["standard", "smartnic", "dpu", "other"])
def test_adapter_classes(interface_model, kind):
    docs, _, _, adapter, _, required = interface_model
    adapter["class"] = kind
    required["adapter"] = {"class": kind}
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_ordinary_interface_needs_no_adapter_class(interface_model):
    docs, _, _, adapter, _, _ = interface_model
    adapter.pop("class")
    adapter.pop("vendor")
    adapter.pop("model")
    assert not InfrastructureModel.from_documents(docs).validate().warnings


@pytest.mark.parametrize("requirement", [
    {"type": "infiniband"},
    {"minSpeed": {"value": 101, "unit": "Gbps"}},
    {"features": ["unsupported-feature"]},
    {"adapter": {"class": "dpu"}},
    {"adapter": {"class": "smartnic", "vendor": "Different"}},
    {"adapter": {"class": "smartnic", "model": "Different"}},
])
def test_interface_known_unsatisfied(interface_model, requirement):
    docs, _, _, _, _, required = interface_model
    required.update(requirement)
    assert "UNSATISFIED" in str(error(docs, "incompatible_resource", "/interfaceRequirements"))


def test_every_eligible_interface_too_slow(interface_model):
    docs, _, _, adapter, interface, required = interface_model
    interface["capabilities"]["bandwidth"] = {"value": 25, "unit": "Gbps"}
    second = deepcopy(interface)
    second["name"] = "p2"
    adapter["interfaces"].append(second)
    required["minSpeed"] = {"value": 100, "unit": "Gbps"}
    error(docs, "incompatible_resource", "/interfaceRequirements")


@pytest.mark.parametrize("missing", ["type", "bandwidth", "features", "class", "vendor", "model"])
def test_incomplete_interface_evidence_is_unknown(interface_model, missing):
    docs, _, _, adapter, interface, required = interface_model
    required.update(minSpeed={"value": 100, "unit": "Gbps"}, features=["rdma"],
                    adapter={"class": "smartnic", "vendor": "NVIDIA", "model": "ConnectX-6"})
    container = interface if missing == "type" else interface["capabilities"] if missing in {"bandwidth", "features"} else adapter
    container.pop(missing)
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 1 and "UNKNOWN" in warnings[0].message
    assert warnings[0].path.endswith("/interfaceRequirements")


def test_explicit_no_rdma_is_unsatisfied_missing_is_unknown(interface_model):
    docs, _, _, _, interface, required = interface_model
    required["features"] = ["rdma"]
    interface["capabilities"]["features"] = []
    error(docs, "incompatible_resource", "/interfaceRequirements")
    interface["capabilities"].pop("features")
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1


def test_omitted_adapter_inventory_versus_explicit_empty(interface_model):
    docs, server, _, _, _, _ = interface_model
    server.pop("networkAdapters")
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    server["networkAdapters"] = []
    error(docs, "incompatible_resource", "/interfaceRequirements")


def test_candidate_properties_cannot_be_combined_across_ports_or_adapters(interface_model):
    docs, server, _, adapter, interface, required = interface_model
    required.update(minSpeed={"value": 100, "unit": "Gbps"}, features=["rdma"], adapter={"class": "smartnic"})
    second_adapter = deepcopy(adapter)
    second_adapter.update(name="standard", **{"class": "standard"})
    server["networkAdapters"].append(second_adapter)
    interface["capabilities"]["features"] = []
    second_port = deepcopy(interface)
    second_port["name"] = "p2"
    second_port["capabilities"] = {"bandwidth": {"value": 25, "unit": "Gbps"}, "features": ["rdma"]}
    adapter["interfaces"].append(second_port)
    error(docs, "incompatible_resource", "/interfaceRequirements")


def test_suitable_interface_wins_over_unknown_and_unsuitable_candidates(interface_model):
    docs, _, _, adapter, _, required = interface_model
    required["features"] = ["rdma"]
    adapter["interfaces"].extend([{"name": "unknown"}, {"name": "wrong", "type": "infiniband"}])
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_unknown_candidate_prevents_unproven_negative(interface_model):
    docs, _, _, adapter, interface, required = interface_model
    required["features"] = ["rdma"]
    interface["capabilities"]["features"] = []
    adapter["interfaces"].append({"name": "unknown"})
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1


@pytest.mark.parametrize("realization", ["vm", "baremetal"])
def test_site_only_intent_valid_and_unknown_without_inventory(interface_model, realization):
    docs, _, node, _, _, required = interface_model
    docs.pop(1)
    node.update(realization={"type": realization}, placement={"siteRef": "site/lab"},
                requirements={"devices": [{"name": "gpu", "type": "gpu"}]})
    required.update(features=["rdma"], minSpeed={"value": 100, "unit": "Gbps"}, adapter={"class": "dpu"})
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 1 and "UNKNOWN" in warnings[0].message
    assert warnings[0].path.endswith("/realization")


@pytest.mark.parametrize("requirement", [
    {}, {"type": "NIC_Basic"}, {"type": "Ethernet"},
    {"type": "ethernet", "adapter": {"class": "SharedNIC"}},
    {"type": "ethernet", "adapter": {"vendor": "NVIDIA"}},
    {"type": "ethernet", "adapter": {"class": "smartnic", "pciAddress": "00:01.0"}},
    {"type": "ethernet", "minSpeed": {"value": 100, "unit": "GB"}},
    {"type": "ethernet", "minSpeed": {"value": 0, "unit": "Gbps"}},
    {"type": "ethernet", "features": ["rdma", "rdma"]},
    {"type": "ethernet", "features": ["rdma", "RDMA"]},
    {"type": "ethernet", "features": ["sriov "]},
    {"type": "ethernet", "features": ["rdma\n"]},
    {"type": "ethernet", "deviceRef": "gpu"},
])
def test_interface_requirements_schema(interface_model, requirement):
    docs, _, node, _, _, _ = interface_model
    node["networkAttachments"][0]["interfaceRequirements"] = requirement
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert {i.code for i in caught.value.issues} == {"schema"}


def test_extensible_normalized_feature_vocabulary(interface_model):
    docs, _, _, _, interface, required = interface_model
    required["features"] = ["future-capability.v2"]
    interface["capabilities"]["features"] = ["future-capability.v2"]
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_empty_required_features_need_no_inventory_evidence(interface_model):
    docs, _, _, _, interface, required = interface_model
    required["features"] = []
    interface.pop("capabilities")
    assert not InfrastructureModel.from_documents(docs).validate().warnings


@pytest.mark.parametrize("unit,value", [("bps", 10**11), ("Kbps", 10**8), ("Mbps", 10**5),
                                       ("Gbps", 100), ("Tbps", 0.1)])
def test_bandwidth_units_use_exact_existing_comparison(interface_model, unit, value):
    docs, _, _, _, _, required = interface_model
    required["minSpeed"] = {"value": value, "unit": unit}
    with localcontext() as ctx:
        ctx.prec = 1
        ctx.traps[Inexact] = ctx.traps[Rounded] = True
        assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_requirements_stay_attachment_attributes_without_binding_or_allocation(interface_model):
    docs, _, node, adapter, _, required = interface_model
    adapter["class"] = "dpu"
    required["adapter"] = {"class": "dpu"}
    node["requirements"] = {"devices": [{"name": "independent", "type": "dpu"}]}
    second = deepcopy(node["networkAttachments"][0])
    second["name"] = "second"
    node["networkAttachments"].append(second)
    before = deepcopy(docs)
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    attachments = model.topology.nodes("NetworkAttachment")
    assert all(a["interfaceRequirements"] == required for a in attachments)
    assert len(attachments) == 2
    assert not model.topology.nodes("InterfaceRequirement")
    assert not model.topology.edges("uses_interface")
    assert len(model.of_kind("DeviceRequirement")) == 1
    assert docs == before


def test_adapter_features_and_status_do_not_prove_interface_features(interface_model):
    docs, _, _, adapter, interface, required = interface_model
    interface["capabilities"].pop("features")
    adapter["features"] = ["rdma"]
    interface["status"] = {"extensions": {"example.org/features": ["rdma"]}}
    required["features"] = ["rdma"]
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
