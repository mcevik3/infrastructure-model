from copy import deepcopy
import json

import pytest
import yaml

from infra_model import InfrastructureModel, ValidationError
from conftest import ROOT


NODE = "cluster/devices/node/worker"
DEVICE = NODE + "/requirement/device/dpu-a"
ATTACHMENT = NODE + "/network-attachment/data"
PATH = "/spec/nodes/0/networkAttachments/0"


def document(kind, name, spec):
    return {"apiVersion": "infra.model/v0alpha1", "kind": kind,
            "metadata": {"name": name}, "spec": spec}


@pytest.fixture
def intent():
    device = {"name": "dpu-a", "type": "dpu"}
    attachment = {"name": "data", "networkRef": "network/data",
                  "deviceRequirementRef": "dpu-a", "interfaceRequirements": {"type": "ethernet"}}
    node = {"name": "worker", "realization": {"type": "vm"},
            "requirements": {"devices": [device]}, "networkAttachments": [attachment]}
    docs = [document("Network", "data", {}), document("Cluster", "devices", {"nodes": [node]})]
    return docs, node, device, attachment


def issues_for(docs, code):
    before = deepcopy(docs)
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert {issue.code for issue in caught.value.issues} == {code}, str(caught.value)
    assert docs == before
    return caught.value.issues


@pytest.mark.parametrize("count", [None, 1])
def test_local_and_canonical_references_have_identical_normalized_meaning(intent, count):
    docs, _, device, attachment = intent
    if count is not None:
        device["count"] = count
    local = InfrastructureModel.from_documents(docs)
    attachment["deviceRequirementRef"] = DEVICE
    canonical = InfrastructureModel.from_documents(docs)
    assert local.validate() == canonical.validate()
    assert local.documents == canonical.documents
    assert local.topology.nodes() == canonical.topology.nodes()
    assert local.topology.edges() == canonical.topology.edges()
    assert local.get(ATTACHMENT)["deviceRequirementRef"] == DEVICE
    assert ("count" in local.get(DEVICE)) == (count is not None)
    assert len(local.validate().warnings) == 1  # Existing unplaced-VM warning.


@pytest.mark.parametrize("name", ["a", "1", "DPU_1.v2", "dpu-a"])
def test_reference_reuses_existing_case_sensitive_names(intent, name):
    docs, _, device, attachment = intent
    device["name"] = attachment["deviceRequirementRef"] = name
    model = InfrastructureModel.from_documents(docs)
    assert model.get(ATTACHMENT)["deviceRequirementRef"] == NODE + "/requirement/device/" + name


@pytest.mark.parametrize("reference", ["missing", "DPU-a", NODE + "/requirement/device/missing"])
def test_unknown_target_is_rejected_at_reference(intent, reference):
    docs, _, _, attachment = intent
    attachment["deviceRequirementRef"] = reference
    issue, = issues_for(docs, "unresolved_reference")
    assert issue.path == PATH + "/deviceRequirementRef"
    assert issue.source == "memory#document=2"
    assert "DeviceRequirement" in issue.message


@pytest.mark.parametrize("other_cluster", [False, True])
def test_existing_cross_node_target_is_rejected_even_with_same_local_name(intent, other_cluster):
    docs, node, _, attachment = intent
    second = deepcopy(node)
    second["name"] = "other"
    if other_cluster:
        docs.append(document("Cluster", "second", {"nodes": [second]}))
    else:
        docs[-1]["spec"]["nodes"].append(second)
    target_node = f"cluster/{'second' if other_cluster else 'devices'}/node/other"
    attachment["deviceRequirementRef"] = target_node + "/requirement/device/dpu-a"
    issue, = issues_for(docs, "reference_scope")
    assert issue.path == PATH + "/deviceRequirementRef"
    assert NODE in issue.message


def test_reused_local_names_resolve_independently_on_each_node(intent):
    docs, node, _, _ = intent
    second = deepcopy(node)
    second["name"] = "other"
    docs[-1]["spec"]["nodes"].append(second)
    model = InfrastructureModel.from_documents(docs)
    assert [a["deviceRequirementRef"] for a in model.of_kind("NetworkAttachment")] == [
        DEVICE, "cluster/devices/node/other/requirement/device/dpu-a"]


@pytest.mark.parametrize("count", [2, 3, 10**5000], ids=["two", "three", "large-integer"])
def test_count_many_rejects_reference_but_preserves_allocation_only_intent(intent, count):
    docs, _, device, attachment = intent
    device["count"] = count
    issue, = issues_for(docs, "device_requirement_count")
    assert issue.path == PATH + "/deviceRequirementRef"
    del attachment["deviceRequirementRef"]
    model = InfrastructureModel.from_documents(docs)
    assert model.get(DEVICE)["count"] == count
    assert not model.topology.edges("uses_device_requirement")


def test_reference_and_adapter_group_are_mutually_exclusive(intent):
    docs, _, _, attachment = intent
    attachment["adapterGroup"] = "dpu-a"
    issue, = issues_for(docs, "schema")
    assert issue.path == PATH
    assert "adapterGroup" in issue.message and "deviceRequirementRef" in issue.message


def test_reference_requires_interface_requirements(intent):
    docs, _, _, attachment = intent
    del attachment["interfaceRequirements"]
    issue, = issues_for(docs, "schema")
    assert issue.path == PATH
    assert "interfaceRequirements" in issue.message


def test_nic_grouping_coexists_with_device_reference_without_implicit_association(intent):
    docs, node, _, _ = intent
    nics = [{"name": name, "networkRef": "network/data", "adapterGroup": "dpu-a",
             "interfaceRequirements": {"type": "ethernet", "adapter": {"class": "standard", "model": "NIC"}}}
            for name in ("nic-a", "nic-b")]
    node["networkAttachments"].extend(nics)
    model = InfrastructureModel.from_documents(docs)
    assert len(model.validate().warnings) == 1
    assert len(model.topology.edges("uses_device_requirement")) == 1
    for nic in model.of_kind("NetworkAttachment")[1:]:
        assert nic["adapterGroup"] == "dpu-a"
        assert "deviceRequirementRef" not in nic
        assert nic["interfaceRequirements"]["adapter"] == {"class": "standard", "model": "NIC"}


@pytest.mark.parametrize("kind", ["gpu", "storage", "other"])
def test_non_network_eligible_device_requirement_rejected(intent, kind):
    docs, _, device, _ = intent
    device["type"] = kind
    issue, = issues_for(docs, "device_requirement_type")
    assert issue.path == PATH + "/deviceRequirementRef"
    assert kind in issue.message


@pytest.mark.parametrize("adapter_class", ["standard", "smartnic", "other"])
def test_dpu_reference_rejects_contradictory_adapter_class(intent, adapter_class):
    docs, _, _, attachment = intent
    attachment["interfaceRequirements"]["adapter"] = {"class": adapter_class}
    issue, = issues_for(docs, "device_requirement_conflict")
    assert issue.path == PATH + "/interfaceRequirements/adapter/class"
    assert DEVICE in issue.message


def test_fpga_reference_rejects_dpu_adapter_class(intent):
    docs, _, device, attachment = intent
    device["type"] = "fpga"
    attachment["interfaceRequirements"]["adapter"] = {"class": "dpu"}
    issue, = issues_for(docs, "device_requirement_conflict")
    assert issue.path == PATH + "/interfaceRequirements/adapter/class"
    assert issue.source == "memory#document=2"
    assert DEVICE in issue.message and "fpga" in issue.message and "dpu" in issue.message


@pytest.mark.parametrize("kind", ["dpu", "fpga"])
@pytest.mark.parametrize("field", ["vendor", "model"])
def test_device_and_interface_selectors_must_agree_exactly(intent, kind, field):
    docs, _, device, attachment = intent
    device.update(type=kind, constraints={field: "Example"})
    attachment["interfaceRequirements"]["adapter"] = {
        "class": "dpu" if kind == "dpu" else "smartnic", field: "example"}
    issue, = issues_for(docs, "device_requirement_conflict")
    assert issue.path == PATH + f"/interfaceRequirements/adapter/{field}"
    assert "Example" in issue.message and "example" in issue.message


@pytest.mark.parametrize("adapter", [None, {"class": "dpu"},
                                    {"class": "dpu", "vendor": "Example", "model": "DPU"}])
def test_compatible_and_partial_selectors_are_not_filled_in(intent, adapter):
    docs, _, device, attachment = intent
    device["constraints"] = {"vendor": "Example", "model": "DPU", "features": ["offload"]}
    attachment["interfaceRequirements"]["features"] = ["rdma"]
    if adapter is not None:
        attachment["interfaceRequirements"]["adapter"] = adapter
    model = InfrastructureModel.from_documents(docs)
    assert model.get(ATTACHMENT)["interfaceRequirements"] == attachment["interfaceRequirements"]
    assert model.get(DEVICE)["constraints"] == device["constraints"]


@pytest.mark.parametrize("device_count", [1, 2])
def test_two_attachments_per_separately_named_identical_device_preserve_graph(intent, device_count):
    docs, node, device, attachment = intent
    device["constraints"] = {"vendor": "Example", "model": "DPU"}
    node["requirements"]["devices"] = [dict(device, name=f"dpu-{i}") for i in range(device_count)]
    node["networkAttachments"] = [dict(attachment, name=f"data-{i}-{port}", deviceRequirementRef=f"dpu-{i}")
                                  for i in range(device_count) for port in range(2)]
    unreferenced_docs = deepcopy(docs)
    for item in unreferenced_docs[-1]["spec"]["nodes"][0]["networkAttachments"]:
        del item["deviceRequirementRef"]
    baseline = InfrastructureModel.from_documents(unreferenced_docs)
    model = InfrastructureModel.from_documents(docs)
    assert len(model.of_kind("DeviceRequirement")) == device_count
    assert len(model.topology.edges("uses_device_requirement")) == 2 * device_count
    assert [e for e in model.topology.edges() if e["relation"] != "uses_device_requirement"] == baseline.topology.edges()
    for i in range(device_count):
        identity = NODE + f"/requirement/device/dpu-{i}"
        owners = [NODE + f"/network-attachment/data-{i}-{port}" for port in range(2)]
        assert model.topology.neighbors(identity, "uses_device_requirement", "in") == owners
        assert model.topology.neighbors(identity, "requires_device", "in") == [NODE]
        for owner in owners:
            assert model.topology.neighbors(owner, "uses_device_requirement") == [identity]
            assert model.topology.neighbors(owner, "attached_to") == ["network/data"]
    assert not model.topology.edges("uses_device")
    assert not model.topology.edges("uses_interface")
    assert all(e["source"] in model.topology and e["target"] in model.topology for e in model.topology.edges())


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_reference_rejects_conflicting_attachment_identity(intent, reverse):
    docs, node, _, attachment = intent
    attachment["interfaceRequirements"]["adapter"] = {"class": "dpu", "model": "A"}
    second = deepcopy(attachment)
    second["name"] = "second"
    second["deviceRequirementRef"] = DEVICE
    second["interfaceRequirements"]["adapter"]["model"] = "B"
    node["networkAttachments"].append(second)
    if reverse:
        node["networkAttachments"].reverse()
    issue, = issues_for(docs, "device_requirement_conflict")
    assert issue.path == "/spec/nodes/0/networkAttachments/1/interfaceRequirements/adapter/model"


@pytest.mark.parametrize("interface_type", ["ethernet", "infiniband", "other"])
def test_future_fpga_is_valid_generic_intent_without_inventing_adapter_class(intent, interface_type):
    docs, _, device, attachment = intent
    device["type"] = "fpga"
    attachment["interfaceRequirements"]["type"] = interface_type
    model = InfrastructureModel.from_documents(docs)
    assert model.get(DEVICE)["type"] == "fpga"
    assert model.get(ATTACHMENT)["interfaceRequirements"] == {"type": interface_type}
    assert model.topology.neighbors(ATTACHMENT, "uses_device_requirement") == [DEVICE]


@pytest.mark.parametrize("kind", ["dpu", "fpga"])
@pytest.mark.parametrize("matching_interface", [False, True])
def test_exact_inventory_never_proves_correlated_device_interface_feasibility(intent, kind, matching_interface):
    docs, node, device, _ = intent
    device.update(type=kind, constraints={"vendor": "Example", "model": "Device"})
    node.update(realization={"type": "baremetal"}, placement={"resourceRef": "server/host"})
    server = {"siteRef": "site/lab", "capabilities": {"nodeTypes": ["baremetal"]}, "networkAdapters": []}
    if kind == "dpu":
        server["networkAdapters"].append({"name": "device", "class": "dpu", "vendor": "Example",
                                          "model": "Device", "interfaces": [{"name": "unreported"}]})
    else:
        server["accelerators"] = [{"name": "device", "type": "fpga", "vendor": "Example", "model": "Device"}]
    if matching_interface:
        # The independent interface match is on an unrelated adapter.
        server["networkAdapters"].append({"name": "unrelated", "class": "standard",
                                          "interfaces": [{"name": "p1", "type": "ethernet"}]})
    docs.extend([document("Site", "lab", {}), document("Server", "host", server)])
    model = InfrastructureModel.from_documents(docs)
    warning, = model.validate().warnings
    assert warning.code == "unevaluated_requirement"
    assert warning.path == PATH + "/deviceRequirementRef"
    assert "UNKNOWN" in warning.message and "correlated" in warning.message
    assert model.topology.neighbors(ATTACHMENT, "uses_device_requirement") == [DEVICE]
    # References do not bypass independently known device inventory failures.
    server["networkAdapters" if kind == "dpu" else "accelerators"] = []
    issue, = issues_for(docs, "incompatible_resource")
    assert issue.path == "/spec/nodes/0/requirements/devices/0"


def test_matching_dpu_port_still_does_not_claim_binding_or_reserve_inventory(intent):
    docs, node, _, attachment = intent
    node.update(realization={"type": "baremetal"}, placement={"resourceRef": "server/host"})
    node["networkAttachments"].append(dict(attachment, name="second"))
    docs.extend([document("Site", "lab", {}), document("Server", "host", {
        "siteRef": "site/lab", "capabilities": {"nodeTypes": ["baremetal"]},
        "networkAdapters": [{"name": "dpu", "class": "dpu", "interfaces": [{"name": "p1", "type": "ethernet"}]}],
    })])
    model = InfrastructureModel.from_documents(docs)
    assert len(model.validate().warnings) == 2
    assert all(w.code == "unevaluated_requirement" and "correlated" in w.message for w in model.validate().warnings)
    assert not model.topology.edges("uses_interface")
    assert not model.topology.neighbors(DEVICE)


@pytest.mark.parametrize("from_file", [False, True])
def test_sources_opaque_payloads_and_defensive_views_are_preserved(intent, tmp_path, from_file):
    docs, _, device, attachment = intent
    device["extensions"] = {"example.org/intent": {"deviceRequirementRef": "leave-me-local"}}
    attachment["status"] = {"extensions": {"example.org/observed": {"deviceRequirementRef": "dpu-a"}}}
    before = deepcopy(docs)
    source = yaml.safe_dump_all(docs, sort_keys=False)
    path = tmp_path / "intent.yaml"
    if from_file:
        path.write_text(source)
        model = InfrastructureModel.load(path)
    else:
        model = InfrastructureModel.from_documents(docs)
    repeated = InfrastructureModel.from_documents(docs)
    reordered = InfrastructureModel.from_documents(reversed(docs))
    assert model.documents == repeated.documents
    assert sorted(model.documents, key=lambda d: d["id"]) == sorted(reordered.documents, key=lambda d: d["id"])
    assert sorted(model.topology.edges(), key=str) == sorted(reordered.topology.edges(), key=str)
    views = [model.get(ATTACHMENT), model.get(NODE)["networkAttachments"][0],
             model.documents[-1]["spec"]["nodes"][0]["networkAttachments"][0],
             model.of_kind("NetworkAttachment")[0], model.topology.nodes("NetworkAttachment")[0]]
    for item in views:
        assert item["deviceRequirementRef"] == DEVICE
        assert item["status"] == attachment["status"]
        item["deviceRequirementRef"] = "changed"
    assert model.get(ATTACHMENT)["deviceRequirementRef"] == DEVICE
    assert model.get(DEVICE)["extensions"] == device["extensions"]
    assert "count" not in model.get(DEVICE)
    assert docs == before
    if from_file:
        assert path.read_text() == source


@pytest.mark.parametrize("reference", [None, 1, [], {}, "", "dpu-a\n", "dpu a", "-dpu", "dpu:a",
                                       "requirement/device/dpu-a", "server/host/network-adapter/dpu-a",
                                       NODE, NODE + "/requirement/device/dpu-a/extra", DEVICE + "\n"])
def test_reference_schema_uses_existing_local_or_canonical_conventions(intent, reference):
    docs, _, _, attachment = intent
    attachment["deviceRequirementRef"] = reference
    issue, = issues_for(docs, "schema")
    assert issue.path == PATH + "/deviceRequirementRef"


def test_reference_schema_adds_no_default():
    definitions = json.loads((ROOT / "schema/v0alpha1/common.json").read_text())["$defs"]
    attachment = definitions["networkAttachment"]
    assert "deviceRequirementRef" not in attachment["required"]
    assert "default" not in attachment["properties"]["deviceRequirementRef"]
    assert "default" not in definitions["deviceRequirementRef"]
