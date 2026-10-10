from copy import deepcopy
import json

import pytest
import yaml

from infra_model import InfrastructureModel, ValidationError
from conftest import ROOT


NODE_ID = "cluster/lab/node/node-a"
ATTACHMENT_PATH = "/spec/nodes/0/networkAttachments"


def document(kind, name, spec):
    return {"apiVersion": "infra.model/v0alpha1", "kind": kind,
            "metadata": {"name": name}, "spec": spec}


def attachment(name, group=None, adapter=None, **requirements):
    result = {"name": name, "networkRef": "network/shared",
              "interfaceRequirements": {"type": "ethernet", **requirements}}
    if group is not None:
        result["adapterGroup"] = group
    if adapter is not None:
        result["interfaceRequirements"]["adapter"] = adapter
    return result


def model_documents(*attachments, network_spec=None):
    node = {"name": "node-a", "realization": {"type": "vm"},
            "networkAttachments": list(attachments)}
    return [document("Site", "site-a", {}), document("Site", "site-b", {}),
            document("Network", "shared", network_spec or {}),
            document("Cluster", "lab", {"nodes": [node]})]


def issues_for(documents, code):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert {issue.code for issue in caught.value.issues} == {code}, str(caught.value)
    return caught.value.issues


@pytest.mark.parametrize("group", [None, "highspeed-1", "a", "1", "Adapter_1.v2"])
def test_optional_group_and_single_member_valid(group):
    model = InfrastructureModel.from_documents(model_documents(attachment("data", group)))
    assert len(model.validate().warnings) == 1  # Abstract VM placement remains UNKNOWN.
    normalized = model.of_kind("NetworkAttachment")[0]
    if group is None:
        assert "adapterGroup" not in normalized
    else:
        assert normalized["adapterGroup"] == group


@pytest.mark.parametrize("group", [
    "", " ", "high speed", "highspeed-1\n", "-group", "_group", ".group",
    "adapter-group/foo", "group:foo", "grüppe", None, 1, [], {},
])
def test_group_uses_existing_local_identifier_rules(group):
    item = attachment("data")
    item["adapterGroup"] = group
    issues = issues_for(model_documents(item), "schema")
    assert issues[0].path == ATTACHMENT_PATH + "/0/adapterGroup"


def test_group_schema_has_no_default_and_remains_optional():
    definitions = json.loads((ROOT / "schema/v0alpha1/common.json").read_text())["$defs"]
    schema = definitions["networkAttachment"]
    assert "adapterGroup" not in schema["required"]
    assert "default" not in schema["properties"]["adapterGroup"]
    assert "default" not in definitions["name"]


@pytest.mark.parametrize("location", ["attachment", "interfaceRequirements", "adapter"])
def test_unknown_fields_and_wrong_group_nesting_still_rejected(location):
    item = attachment("data", "hs1", {"class": "standard"})
    if location == "attachment":
        item["unknown"] = "value"
    elif location == "interfaceRequirements":
        item["interfaceRequirements"]["adapterGroup"] = "hs1"
    else:
        item["interfaceRequirements"]["adapter"]["adapterGroup"] = "hs1"
    issues_for(model_documents(item), "schema")


@pytest.mark.parametrize("from_file", [False, True])
def test_group_preserved_in_normalized_views_without_source_mutation(tmp_path, from_file):
    docs = model_documents(attachment("data", "Highspeed_1"), attachment("management"))
    before = deepcopy(docs)
    if from_file:
        path = tmp_path / "infrastructure.yaml"
        source = yaml.safe_dump_all(docs, sort_keys=False)
        path.write_text(source)
        model = InfrastructureModel.load(path)
    else:
        model = InfrastructureModel.from_documents(docs)
    model.validate()
    views = [model.of_kind("NetworkAttachment"), model.topology.nodes("NetworkAttachment"),
             model.documents[-1]["spec"]["nodes"][0]["networkAttachments"],
             model.get(NODE_ID)["networkAttachments"]]
    for data, management in views:
        assert data["adapterGroup"] == "Highspeed_1"
        assert "adapterGroup" not in management
        data["adapterGroup"] = "changed"
    assert model.get(NODE_ID + "/network-attachment/data")["adapterGroup"] == "Highspeed_1"
    assert docs == before
    if from_file:
        assert path.read_text() == source


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("partial", [
    {"class": "standard", "vendor": "Vendor-A", "model": "Model-A"},
    {"class": "standard", "vendor": "Vendor-A"},
    {"class": "standard", "model": "Model-A"},
    {"class": "standard"},
    None,
])
def test_same_group_allows_compatible_identity_and_partial_refinement(partial, reverse):
    full = {"class": "standard", "vendor": "Vendor-A", "model": "Model-A"}
    items = [attachment("data", "hs1", full), attachment("storage", "hs1", partial)]
    if reverse:
        items.reverse()
    model = InfrastructureModel.from_documents(model_documents(*items))
    assert len(model.validate().warnings) == 1
    assert [item["adapterGroup"] for item in model.of_kind("NetworkAttachment")] == ["hs1", "hs1"]
    # Partial constraints are retained, not filled from another group member.
    assert model.get(NODE_ID + "/network-attachment/storage")["interfaceRequirements"].get("adapter") == partial


@pytest.mark.parametrize("field,first,second", [
    ("class", "standard", "smartnic"),
    ("vendor", "Vendor-A", "Vendor-B"),
    ("model", "Model-A", "Model-B"),
    ("vendor", "Vendor-A", "vendor-a"),
    ("model", "Model-A", "model-a"),
])
def test_conflicting_explicit_identity_reports_node_group_field_and_values(field, first, second):
    docs = model_documents(
        attachment("data", "hs1", {"class": "standard", field: first}),
        attachment("storage", "hs1", {"class": "standard", field: second}),
    )
    issues = issues_for(docs, "adapter_group_conflict")
    assert len(issues) == 1
    issue = issues[0]
    assert issue.source == "memory#document=4"
    assert issue.path == ATTACHMENT_PATH + f"/1/interfaceRequirements/adapter/{field}"
    for detail in (NODE_ID, "hs1", field, first, second):
        assert detail in issue.message


def test_conflicts_after_unspecified_member_are_not_hidden():
    docs = model_documents(
        attachment("unspecified", "hs1"),
        attachment("data", "hs1", {"class": "standard", "model": "Model-A"}),
        attachment("storage", "hs1", {"class": "standard", "model": "Model-B"}),
    )
    issues = issues_for(docs, "adapter_group_conflict")
    assert issues[0].path == ATTACHMENT_PATH + "/2/interfaceRequirements/adapter/model"


@pytest.mark.parametrize("groups", [("hs1", "hs2"), ("hs1", "HS1"), (None, None), ("hs1", None), (None, "hs1")])
def test_different_groups_and_ungrouped_attachments_are_not_compared(groups):
    model = InfrastructureModel.from_documents(model_documents(
        attachment("data", groups[0], {"class": "standard", "vendor": "Vendor-A", "model": "Model-A"}),
        attachment("storage", groups[1], {"class": "smartnic", "vendor": "Vendor-B", "model": "Model-B"}),
    ))
    assert len(model.validate().warnings) == 1
    assert [item.get("adapterGroup") for item in model.of_kind("NetworkAttachment")] == list(groups)


@pytest.mark.parametrize("same_cluster", [True, False])
def test_same_group_key_on_different_nodes_is_independent(same_cluster):
    docs = model_documents(
        attachment("data", "hs1", {"class": "standard", "vendor": "Vendor-A", "model": "Model-A"}),
        attachment("storage", "hs1", {"class": "standard", "vendor": "Vendor-A", "model": "Model-A"}),
    )
    second = deepcopy(docs[-1]["spec"]["nodes"][0])
    second["name"] = "node-b"
    for item in second["networkAttachments"]:
        item["interfaceRequirements"]["adapter"] = {"class": "smartnic", "vendor": "Vendor-B", "model": "Model-B"}
    if same_cluster:
        docs[-1]["spec"]["nodes"].append(second)
    else:
        docs.append(document("Cluster", "other", {"nodes": [second]}))
    model = InfrastructureModel.from_documents(docs)
    assert len(model.validate().warnings) == 2
    assert len(model.of_kind("NetworkAttachment")) == 4


def test_identical_ungrouped_requirements_do_not_invent_sharing():
    adapter = {"class": "standard", "vendor": "Vendor-A", "model": "Model-A"}
    model = InfrastructureModel.from_documents(model_documents(
        attachment("data", adapter=adapter), attachment("storage", adapter=adapter)))
    assert all("adapterGroup" not in item for item in model.of_kind("NetworkAttachment"))
    assert not model.of_kind("AdapterGroup")


def test_different_interface_constraints_do_not_alone_conflict():
    docs = model_documents(
        attachment("data", "hs1", minSpeed={"value": 100, "unit": "Gbps"}, features=["rdma"]),
        attachment("storage", "hs1", minSpeed={"value": 25, "unit": "Gbps"}, features=["sriov"]),
        attachment("other", "hs1", type="other"),
        attachment("unspecified", "hs1"),
    )
    del docs[-1]["spec"]["nodes"][0]["networkAttachments"][-1]["interfaceRequirements"]
    assert len(InfrastructureModel.from_documents(docs).validate().warnings) == 1


def test_grouping_keeps_attachment_identities_and_all_graph_relationships():
    docs = model_documents(attachment("data"), attachment("storage"))
    ungrouped = InfrastructureModel.from_documents(docs)
    for item in docs[-1]["spec"]["nodes"][0]["networkAttachments"]:
        item["adapterGroup"] = "hs1"
    grouped = InfrastructureModel.from_documents(docs)
    assert grouped.validate() == ungrouped.validate()
    assert [(item["id"], item["kind"]) for item in grouped.topology.nodes()] == [
        (item["id"], item["kind"]) for item in ungrouped.topology.nodes()]
    assert grouped.topology.edges() == ungrouped.topology.edges()
    assert not grouped.topology.nodes("AdapterGroup")
    assert grouped.topology.neighbors(NODE_ID, "contains") == [
        NODE_ID + "/network-attachment/data", NODE_ID + "/network-attachment/storage"]
    assert len(grouped.topology.edges("attached_to")) == 2
    assert len(grouped.topology.edges("network_attachment")) == 2


@pytest.mark.parametrize("count", [2, 3])
def test_point_to_point_counts_attachments_not_groups(count):
    docs = model_documents(*(attachment(f"data-{index}", "hs1") for index in range(count)),
                           network_spec={"connectivity": "point-to-point"})
    if count == 3:
        issues = issues_for(docs, "network_endpoint_cardinality")
        assert len(issues) == 1 and "found 3" in issues[0].message
    else:
        model = InfrastructureModel.from_documents(docs)
        assert len(model.of_kind("NetworkAttachment")) == 2


@pytest.mark.parametrize("site_refs", [None, ["site/site-a"], ["site/site-b"], ["site/site-a", "site/site-b"]])
@pytest.mark.parametrize("node_site", [None, "site/site-a", "site/site-b"])
def test_grouping_preserves_effective_site_and_network_scope(site_refs, node_site):
    docs = model_documents(attachment("data"), attachment("storage"),
                           network_spec={} if site_refs is None else {"siteRefs": site_refs})
    cluster = docs[-1]["spec"]
    if node_site is not None:
        cluster["siteRef"] = "site/site-a"
        if node_site == "site/site-b":
            cluster["nodes"][0]["placement"] = {"siteRef": node_site}
    grouped_docs = deepcopy(docs)
    for item in grouped_docs[-1]["spec"]["nodes"][0]["networkAttachments"]:
        item["adapterGroup"] = "hs1"
    if node_site is not None and site_refs is not None and node_site not in site_refs:
        assert issues_for(grouped_docs, "network_site_scope") == issues_for(docs, "network_site_scope")
    else:
        grouped = InfrastructureModel.from_documents(grouped_docs)
        ungrouped = InfrastructureModel.from_documents(docs)
        assert grouped.validate().warnings == ungrouped.validate().warnings
        assert grouped.topology.edges() == ungrouped.topology.edges()
        assert grouped.topology.neighbors(NODE_ID, "targets_site") == ([] if node_site is None else [node_site])


@pytest.mark.parametrize("failure", [None, "speed", "device"])
def test_grouping_keeps_inventory_checks_without_port_count_or_device_allocation(failure):
    docs = model_documents(
        attachment("data", "hs1", minSpeed={"value": 100, "unit": "Gbps"}, features=["rdma"]),
        attachment("storage", "hs1", minSpeed={"value": 25, "unit": "Gbps"}, features=["sriov"]),
    )
    node = docs[-1]["spec"]["nodes"][0]
    node.update(realization={"type": "baremetal"}, placement={"resourceRef": "server/host"},
                requirements={"devices": [{"name": "accelerator", "type": "gpu", "count": 2 if failure == "device" else 1}]})
    docs.append(document("Server", "host", {
        "siteRef": "site/site-a",
        "capabilities": {"nodeTypes": ["baremetal"]},
        "accelerators": [{"name": "gpu", "type": "gpu"}],
        "networkAdapters": [{"name": "adapter", "interfaces": [{
            "name": "port", "type": "ethernet", "capabilities": {
                "bandwidth": {"value": 25 if failure == "speed" else 100, "unit": "Gbps"},
                "features": ["rdma", "sriov"],
            },
        }]}],
    }))
    ungrouped_docs = deepcopy(docs)
    for item in ungrouped_docs[-2]["spec"]["nodes"][0]["networkAttachments"]:
        del item["adapterGroup"]
    if failure:
        assert issues_for(docs, "incompatible_resource") == issues_for(ungrouped_docs, "incompatible_resource")
    else:
        # Independent compatibility can succeed with one known port; it is not
        # proof that the two-interface group can be jointly allocated.
        assert not InfrastructureModel.from_documents(docs).validate().warnings
        assert not InfrastructureModel.from_documents(ungrouped_docs).validate().warnings
