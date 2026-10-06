from copy import deepcopy
from decimal import Inexact, Rounded, localcontext

import pytest

from infra_model import InfrastructureModel, ValidationError


NODE = "cluster/devices/node/worker"


def document(kind, name, spec):
    return {"apiVersion": "infra.model/v0alpha1", "kind": kind,
            "metadata": {"name": name}, "spec": spec}


@pytest.fixture
def device_model():
    server = {"siteRef": "site/lab", "capabilities": {"nodeTypes": ["baremetal"]},
              "storage": {"devices": [
                  {"name": "nvme0", "vendor": "Example", "model": "SSD", "protocol": "nvme",
                   "capacity": {"value": 1, "unit": "TiB"}, "features": ["encryption"]},
                  {"name": "nvme1", "protocol": "nvme", "capacity": {"value": 1, "unit": "TB"}},
              ]},
              "networkAdapters": [
                  {"name": "dpu0", "class": "dpu", "vendor": "Example", "model": "DPU",
                   "features": ["offload"], "interfaces": [{"name": "p1"}, {"name": "p2"}]},
              ]}
    node = {"name": "worker", "realization": {"type": "baremetal"},
            "placement": {"resourceRef": "server/host"},
            "requirements": {"devices": [{"name": "local", "type": "storage"}]}}
    docs = [document("Site", "lab", {}), document("Server", "host", server),
            document("Cluster", "devices", {"nodes": [node]})]
    return docs, server, node, node["requirements"]["devices"][0]


def error(docs, code, path):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert any(i.code == code and i.path.endswith(path) for i in caught.value.issues), str(caught.value)
    return caught.value


@pytest.mark.parametrize("kind", ["gpu", "fpga", "dpu", "storage", "other"])
def test_generic_device_types_are_valid(device_model, kind):
    docs, _, _, required = device_model
    required["type"] = kind
    if kind == "gpu":
        required["constraints"] = {"vendor": "NVIDIA", "model": "A100"}
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == (1 if kind in {"gpu", "fpga", "other"} else 0)
    assert all("UNKNOWN" in w.message for w in warnings)


@pytest.mark.parametrize("count", [None, 1, 2])
def test_nvme_count_and_capacity_satisfied(device_model, count):
    docs, _, _, required = device_model
    required["constraints"] = {"protocol": "nvme", "minCapacity": {"value": 1, "unit": "TB"}}
    if count is not None:
        required["count"] = count
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    assert ("count" in model.get(NODE + "/requirement/device/local")) == (count is not None)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(count=3),
    lambda r: r.update(constraints={"protocol": "sata"}),
    lambda r: r.update(constraints={"minCapacity": {"value": 2, "unit": "TB"}}),
])
def test_device_known_unsatisfied(device_model, mutation):
    docs, _, _, required = device_model
    mutation(required)
    assert "UNSATISFIED" in str(error(docs, "incompatible_resource", "/requirements/devices/0"))


def test_device_count_beyond_integer_string_conversion_limit(device_model):
    docs, _, _, required = device_model
    required["count"] = 10**5000
    error(docs, "incompatible_resource", "/requirements/devices/0")


@pytest.mark.parametrize("field,value", [("vendor", "Example"), ("model", "SSD"),
                                        ("features", ["encryption"]), ("protocol", "nvme"),
                                        ("minCapacity", {"value": 1, "unit": "TB"})])
def test_missing_device_constraint_evidence_is_unknown(device_model, field, value):
    docs, server, _, required = device_model
    server["storage"]["devices"] = [{"name": "unknown"}]
    required["constraints"] = {field: value}
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 1 and "UNKNOWN" in warnings[0].message
    assert warnings[0].path.endswith("/requirements/devices/0")


def test_unknown_candidates_may_cover_count_but_cannot_invent_components(device_model):
    docs, server, _, required = device_model
    required.update(count=2, constraints={"vendor": "Example"})
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    server["storage"]["devices"][1]["vendor"] = "Different"
    error(docs, "incompatible_resource", "/requirements/devices/0")


def test_all_constraints_must_match_one_device(device_model):
    docs, server, _, required = device_model
    server["storage"]["devices"][0]["capacity"] = {"value": 500, "unit": "GB"}
    server["storage"]["devices"][1]["protocol"] = "sata"
    required["constraints"] = {"protocol": "nvme", "minCapacity": {"value": 1, "unit": "TB"}}
    error(docs, "incompatible_resource", "/requirements/devices/0")


def test_storage_inventory_omitted_versus_explicit_empty(device_model):
    docs, server, _, _ = device_model
    server["storage"].pop("devices")
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    # Neither aggregate capability nor RAID volume is a physical device count.
    server["capabilities"]["storage"] = {"capacity": {"value": 100, "unit": "TB"}, "protocol": "nvme"}
    server["storage"].update(controllers=[{"name": "raid"}],
                             volumes=[{"name": "volume", "controllerRef": "raid"}])
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    server["storage"]["devices"] = []
    error(docs, "incompatible_resource", "/requirements/devices/0")


def test_dpu_matches_existing_adapter_and_counts_components_not_ports(device_model):
    docs, _, _, required = device_model
    required.update(type="dpu", constraints={"vendor": "Example", "model": "DPU", "features": ["offload"]})
    assert not InfrastructureModel.from_documents(docs).validate().warnings
    required["count"] = 2
    error(docs, "incompatible_resource", "/requirements/devices/0")


@pytest.mark.parametrize("adapter_class", [None, "standard"])
def test_dpu_unknown_or_explicit_mismatch(device_model, adapter_class):
    docs, server, _, required = device_model
    required["type"] = "dpu"
    adapter = server["networkAdapters"][0]
    adapter.pop("class")
    if adapter_class is None:
        assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    else:
        adapter["class"] = adapter_class
        error(docs, "incompatible_resource", "/requirements/devices/0")


@pytest.mark.parametrize("count", [0, -1, 1.5, True, "2"])
def test_bad_device_count(device_model, count):
    docs, _, _, required = device_model
    required["count"] = count
    error(docs, "schema", "/count")


@pytest.mark.parametrize("kind", ["nvme", "NIC_Basic", "network", "GPU"])
def test_unsupported_device_type(device_model, kind):
    docs, _, _, required = device_model
    required["type"] = kind
    error(docs, "schema", "/type")


@pytest.mark.parametrize("kind", ["gpu", "fpga", "dpu", "other"])
@pytest.mark.parametrize("constraint", [{"protocol": "nvme"}, {"minCapacity": {"value": 1, "unit": "TB"}}])
def test_storage_constraints_rejected_for_other_types(device_model, kind, constraint):
    docs, _, _, required = device_model
    required.update(type=kind, constraints=constraint)
    error(docs, "schema", "/constraints")


@pytest.mark.parametrize("constraint", [
    {"minCapacity": {"value": 1, "unit": "Gbps"}}, {"protocol": "ssd"},
    {"pciAddress": "0000:41:00.0"}, {"vendor": ""}, {"features": ["rdma", "rdma"]},
    {"features": ["RDMA"]}, {"features": ["rdma\n"]},
])
def test_device_constraints_are_closed_and_typed(device_model, constraint):
    docs, _, _, required = device_model
    required["constraints"] = constraint
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert {i.code for i in caught.value.issues} == {"schema"}


@pytest.mark.parametrize("field", ["name", "type"])
def test_required_device_fields(device_model, field):
    docs, _, _, required = device_model
    required.pop(field)
    error(docs, "schema", "/requirements/devices/0")


def test_duplicate_requirement_names(device_model):
    docs, _, node, required = device_model
    node["requirements"]["devices"].append(deepcopy(required))
    error(docs, "duplicate_component_name", "/requirements/devices/1/name")


def test_requirement_identity_graph_and_source_preservation(device_model):
    docs, _, node, required = device_model
    required["status"] = {"extensions": {"example.org/observation": {"count": 999}}}
    before = deepcopy(docs)
    model = InfrastructureModel.from_documents(reversed(docs))
    identity = NODE + "/requirement/device/local"
    actual = model.get(identity)
    assert actual["kind"] == "DeviceRequirement"
    assert actual["name"] == "local" and "count" not in actual
    assert model.get(NODE)["requirements"]["devices"] == [actual]
    assert model.of_kind("DeviceRequirement") == [actual]
    assert model.topology.nodes("DeviceRequirement") == [actual]
    assert model.topology.neighbors(NODE, "contains") == [identity]
    assert model.topology.neighbors(NODE, "requires_device") == [identity]
    assert model.topology.neighbors(identity, "requires_device", "in") == [NODE]
    assert not model.topology.neighbors(identity, "uses_device")
    actual["name"] = "changed"
    model.topology.nodes("DeviceRequirement")[0].clear()
    assert model.get(identity)["name"] == "local"
    assert docs == before
    assert all(e["source"] in model.topology and e["target"] in model.topology for e in model.topology.edges())
    assert not hasattr(model.topology, "graph")


def test_requirements_are_not_top_level_documents():
    error([document("DeviceRequirement", "gpu", {"type": "gpu"})], "schema", "/kind")


def test_names_reuse_across_nodes_and_independent_checks_do_not_allocate(device_model):
    docs, _, node, required = device_model
    required["count"] = 2
    node["requirements"]["devices"].append({"name": "another", "type": "storage", "count": 2})
    other = deepcopy(node)
    other["name"] = "second"
    docs[-1]["spec"]["nodes"].append(other)
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    assert len(model.of_kind("DeviceRequirement")) == 4


def test_opaque_extensions_and_status_do_not_supply_evidence(device_model):
    docs, server, _, required = device_model
    required.update(type="gpu", constraints={"vendor": "NVIDIA"})
    server["extensions"] = {"example.org/devices": [{"type": "gpu", "vendor": "NVIDIA"}]}
    docs[1]["status"] = {"extensions": deepcopy(server["extensions"])}
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1
    required.update(type="storage", constraints={})
    required["extensions"] = {"example.org/constraint": True}
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1


def test_min_capacity_uses_existing_exact_quantity_logic(device_model):
    docs, server, _, required = device_model
    server["storage"]["devices"] = [{"name": "disk", "capacity": {"value": 1073, "unit": "MB"}}]
    required["constraints"] = {"minCapacity": {"value": 1, "unit": "GiB"}}
    with localcontext() as ctx:
        ctx.prec = 1
        ctx.traps[Inexact] = ctx.traps[Rounded] = True
        error(docs, "incompatible_resource", "/requirements/devices/0")
        server["storage"]["devices"][0]["capacity"] = {"value": 1024, "unit": "MiB"}
        assert not InfrastructureModel.from_documents(docs).validate().warnings
