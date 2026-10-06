from copy import deepcopy

import pytest

from infra_model import InfrastructureModel, ValidationError
from test_device_requirements import document, error


@pytest.fixture(params=["gpu", "fpga"])
def accelerator_model(request):
    kind = request.param
    accelerator = {"name": kind + "0", "type": kind,
                   "vendor": "NVIDIA" if kind == "gpu" else "AMD",
                   "model": "A100" if kind == "gpu" else "Alveo U280",
                   "pciAddress": "0000:81:00.0",
                   "capabilities": {"features": ["example-feature"]}}
    server = {"siteRef": "site/lab", "capabilities": {"nodeTypes": ["baremetal"]},
              "accelerators": [accelerator]}
    required = {"name": "accelerator", "type": kind, "count": 1}
    node = {"name": "worker", "realization": {"type": "baremetal"},
            "placement": {"resourceRef": "server/host"},
            "requirements": {"devices": [required]}}
    docs = [document("Site", "lab", {}), document("Server", "host", server),
            document("Cluster", "devices", {"nodes": [node]})]
    return docs, server, accelerator, required


def test_accelerator_valid_and_satisfied(accelerator_model):
    docs, _, accelerator, required = accelerator_model
    required["constraints"] = {"vendor": accelerator["vendor"], "model": accelerator["model"],
                               "features": ["example-feature"]}
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_accelerator_minimal_inventory(accelerator_model):
    docs, server, accelerator, required = accelerator_model
    server["accelerators"] = [{"name": accelerator["name"], "type": accelerator["type"]}]
    required.pop("count")
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_other_accelerator_inventory_is_valid_but_other_demand_remains_unknown(accelerator_model):
    docs, _, accelerator, required = accelerator_model
    accelerator["type"] = required["type"] = "other"
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 1 and "UNKNOWN" in warnings[0].message


@pytest.mark.parametrize("field", ["name", "type"])
def test_accelerator_required_fields(accelerator_model, field):
    docs, _, accelerator, _ = accelerator_model
    accelerator.pop(field)
    error(docs, "schema", "/accelerators/0")


@pytest.mark.parametrize("kind", ["dpu", "storage", "GPU", "NIC_Basic", ""])
def test_accelerator_invalid_type(accelerator_model, kind):
    docs, _, accelerator, _ = accelerator_model
    accelerator["type"] = kind
    error(docs, "schema", "/accelerators/0/type")


@pytest.mark.parametrize("fields", [
    {"name": "bad/name"}, {"vendor": ""}, {"model": ""}, {"pciAddress": "invalid"},
    {"features": ["example-feature"]}, {"capabilities": {"features": ["RDMA"]}},
    {"capabilities": {"features": ["rdma", "rdma"]}},
    {"capabilities": {"gpuMemory": {"value": 80, "unit": "GB"}}},
    {"status": {"operationalState": "up"}}, {"numaNode": 0},
])
def test_accelerator_fields_are_closed_and_typed(accelerator_model, fields):
    docs, _, accelerator, _ = accelerator_model
    accelerator.update(fields)
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert {i.code for i in caught.value.issues} == {"schema"}


def test_duplicate_accelerator_name_across_types(accelerator_model):
    docs, server, accelerator, _ = accelerator_model
    server["accelerators"].append({"name": accelerator["name"], "type": "other"})
    error(docs, "duplicate_component_name", "/accelerators/1/name")


def test_accelerator_identity_graph_and_source_preservation(accelerator_model):
    docs, _, accelerator, _ = accelerator_model
    before = deepcopy(docs)
    model = InfrastructureModel.from_documents(reversed(docs))
    identity = "server/host/accelerator/" + accelerator["name"]
    actual = model.get(identity)
    assert actual == {**accelerator, "id": identity, "kind": "Accelerator"}
    assert model.get("server/host")["spec"]["accelerators"] == [actual]
    assert model.of_kind("Accelerator") == model.topology.nodes("Accelerator") == [actual]
    assert model.topology.neighbors("server/host", "contains") == [identity]
    assert model.topology.neighbors("server/host", "has_accelerator") == [identity]
    assert model.topology.neighbors(identity, "has_accelerator", "in") == ["server/host"]
    assert not model.topology.edges("uses_device")
    actual["capabilities"]["features"].clear()
    model.topology.nodes("Accelerator")[0].clear()
    assert model.get(identity)["capabilities"]["features"] == ["example-feature"]
    assert not hasattr(model.topology, "graph")
    assert all(e["source"] in model.topology and e["target"] in model.topology for e in model.topology.edges())
    assert docs == before


def test_accelerator_names_and_pci_reuse_on_other_servers(accelerator_model):
    docs, server, accelerator, _ = accelerator_model
    docs.append(document("Server", "second", deepcopy(server)))
    server["storage"] = {"devices": [{"name": accelerator["name"]}]}
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    assert len(model.of_kind("Accelerator")) == 2
    assert model.get("server/host/storage-device/" + accelerator["name"])


@pytest.mark.parametrize("collection", ["accelerators", "networkAdapters", "controllers", "devices"])
def test_accelerator_pci_uniqueness_uses_existing_server_scope(accelerator_model, collection):
    docs, server, accelerator, _ = accelerator_model
    accelerator["pciAddress"] = "0000:AB:00.0"
    other = {"name": "other", "pciAddress": "ab:00.0"}
    if collection == "accelerators":
        other["type"] = "other"
        server[collection].append(other)
    elif collection == "networkAdapters":
        other["interfaces"] = [{"name": "p1"}]
        server[collection] = [other]
    else:
        server["storage"] = {collection: [other]}
    error(docs, "duplicate_pci", "/pciAddress")


def test_accelerator_not_a_top_level_document():
    error([document("Accelerator", "gpu0", {"type": "gpu"})], "schema", "/kind")


@pytest.mark.parametrize("inventory", ["omitted", "empty", "wrong_type"])
def test_accelerator_inventory_evidence(accelerator_model, inventory):
    docs, server, accelerator, required = accelerator_model
    if inventory == "omitted":
        server.pop("accelerators")
        warnings = InfrastructureModel.from_documents(docs).validate().warnings
        assert len(warnings) == 1 and "UNKNOWN" in warnings[0].message
        assert warnings[0].path.endswith("/requirements/devices/0")
    else:
        if inventory == "empty":
            server["accelerators"] = []
        else:
            accelerator["type"] = "fpga" if required["type"] == "gpu" else "gpu"
            accelerator.pop("vendor")
        assert "UNSATISFIED" in str(error(docs, "incompatible_resource", "/requirements/devices/0"))


@pytest.mark.parametrize("field", ["vendor", "model", "features"])
@pytest.mark.parametrize("evidence", ["match", "mismatch", "missing"])
def test_accelerator_constraint_evidence(accelerator_model, field, evidence):
    docs, _, accelerator, required = accelerator_model
    container = accelerator["capabilities"] if field == "features" else accelerator
    required["constraints"] = {field: deepcopy(container[field])}
    if evidence == "mismatch":
        container[field] = [] if field == "features" else "Different"
        assert "UNSATISFIED" in str(error(docs, "incompatible_resource", "/requirements/devices/0"))
    elif evidence == "missing":
        container.pop(field)
        warnings = InfrastructureModel.from_documents(docs).validate().warnings
        assert len(warnings) == 1 and "UNKNOWN" in warnings[0].message
    else:
        assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_accelerator_missing_capabilities_and_empty_requested_features(accelerator_model):
    docs, _, accelerator, required = accelerator_model
    accelerator.pop("capabilities")
    required["constraints"] = {"features": ["example-feature"]}
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    required["constraints"]["features"] = []
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_accelerator_count_uses_distinct_matching_entries(accelerator_model):
    docs, server, accelerator, required = accelerator_model
    required["count"] = 2
    error(docs, "incompatible_resource", "/requirements/devices/0")
    second = deepcopy(accelerator)
    second.update(name="second", pciAddress="0000:82:00.0")
    server["accelerators"].append(second)
    assert not InfrastructureModel.from_documents(docs).validate().warnings
    required["count"] = 3
    error(docs, "incompatible_resource", "/requirements/devices/0")


def test_accelerator_count_with_mixed_constraint_evidence(accelerator_model):
    docs, server, accelerator, required = accelerator_model
    required.update(count=2, constraints={"vendor": accelerator["vendor"]})
    second = {"name": "second", "type": accelerator["type"]}
    server["accelerators"].append(second)
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    required["count"] = 3
    error(docs, "incompatible_resource", "/requirements/devices/0")
    required["count"] = 2
    second["vendor"] = "Different"
    error(docs, "incompatible_resource", "/requirements/devices/0")
    second["vendor"] = accelerator["vendor"]
    assert not InfrastructureModel.from_documents(docs).validate().warnings
    second["vendor"] = "Different"
    required["count"] = 1
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_accelerator_constraints_must_match_same_entry(accelerator_model):
    docs, server, accelerator, required = accelerator_model
    required["constraints"] = {"vendor": accelerator["vendor"], "model": accelerator["model"]}
    server["accelerators"].append({"name": "second", "type": accelerator["type"],
                                   "vendor": "Different", "model": accelerator["model"]})
    accelerator["model"] = "Different"
    error(docs, "incompatible_resource", "/requirements/devices/0")
    accelerator.pop("vendor")
    error(docs, "incompatible_resource", "/requirements/devices/0")


def test_accelerator_requirements_do_not_allocate(accelerator_model):
    docs, _, _, required = accelerator_model
    node = docs[-1]["spec"]["nodes"][0]
    node["requirements"]["devices"].append({**required, "name": "second"})
    docs[-1]["spec"]["nodes"].append({**deepcopy(node), "name": "second"})
    assert not InfrastructureModel.from_documents(docs).validate().warnings


@pytest.mark.parametrize("component", ["profile", "adapter", "accelerator", "controller", "device"])
def test_hardware_vendor_accepted_and_manufacturer_rejected(component):
    server = {"siteRef": "site/lab", "accelerators": [{"name": "gpu0", "type": "gpu"}],
              "networkAdapters": [{"name": "nic", "interfaces": [{"name": "p1"}]}],
              "storage": {"controllers": [{"name": "raid"}], "devices": [{"name": "disk"}]}}
    profile = {"capabilities": {}}
    docs = [document("Site", "lab", {}), document("Server", "host", server),
            document("HardwareProfile", "hardware", profile)]
    target = {"profile": profile, "adapter": server["networkAdapters"][0],
              "accelerator": server["accelerators"][0], "controller": server["storage"]["controllers"][0],
              "device": server["storage"]["devices"][0]}[component]
    target["vendor"] = "Example"
    assert not InfrastructureModel.from_documents(docs).validate().warnings
    target["manufacturer"] = target.pop("vendor")
    with pytest.raises(ValidationError, match="manufacturer") as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert {i.code for i in caught.value.issues} == {"schema"}
