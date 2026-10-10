from copy import deepcopy
import json

import pytest

from infra_model import InfrastructureModel, ValidationError
from conftest import ROOT


def document(kind, name, spec):
    return {"apiVersion": "infra.model/v0alpha1", "kind": kind,
            "metadata": {"name": name}, "spec": spec}


def model_documents(spec, node_sites=(), default_site=None):
    documents = [document("Site", name, {}) for name in ("site-a", "site-b", "site-c")]
    documents.append(document("Network", "shared", spec))
    if node_sites:
        nodes = []
        for index, site in enumerate(node_sites):
            node = {"name": f"vm-{index}", "realization": {"type": "vm"},
                    "networkAttachments": [{"name": "data", "networkRef": "network/shared"}]}
            if site is not None:
                node["placement"] = {"siteRef": site}
            nodes.append(node)
        cluster = {"nodes": nodes}
        if default_site is not None:
            cluster["siteRef"] = default_site
        documents.append(document("Cluster", "lab", cluster))
    return documents


def assert_issue(documents, code, path):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert any(issue.code == code and issue.path == path for issue in caught.value.issues), str(caught.value)
    return caught.value.issues


@pytest.mark.parametrize("spec", [
    {}, {"siteRefs": ["site/site-a"]}, {"siteRefs": ["site/site-b", "site/site-a"]},
])
def test_site_scope_valid_and_preserved_in_all_views(spec):
    documents = model_documents(spec)
    before = deepcopy(documents)
    model = InfrastructureModel.from_documents(documents)
    assert not model.validate().warnings
    views = [model.get("network/shared"), model.networks()[0], model.documents[3],
             model.topology.nodes("Network")[0]]
    for view in views:
        assert view["spec"] == spec
        view["spec"].clear()
    assert model.get("network/shared")["spec"] == spec
    assert documents == before
    assert model.topology.neighbors("network/shared", "scoped_to") == sorted(spec.get("siteRefs", []))


@pytest.mark.parametrize("site_refs,path", [
    ([], "/spec/siteRefs"),
    (["site/site-a", "site/site-a"], "/spec/siteRefs"),
    (None, "/spec/siteRefs"),
    ("site/site-a", "/spec/siteRefs"),
    (["site-a"], "/spec/siteRefs/0"),
    (["network/shared"], "/spec/siteRefs/0"),
    (["site/site-a\n"], "/spec/siteRefs/0"),
])
def test_site_scope_shape_and_canonical_unique_references(site_refs, path):
    assert_issue(model_documents({"siteRefs": site_refs}), "schema", path)


def test_each_site_reference_must_resolve():
    issues = assert_issue(model_documents({"siteRefs": ["site/site-a", "site/missing"]}),
                          "unresolved_reference", "/spec/siteRefs/1")
    assert issues[0].source == "memory#document=4"
    assert "site/missing" in issues[0].message


@pytest.mark.parametrize("spec", [
    {"siteRef": "site/site-a"},
    {"siteRef": "site/site-a", "siteRefs": ["site/site-a"]},
])
def test_old_network_site_ref_rejected_without_alias(spec):
    assert_issue(model_documents(spec), "schema", "/spec")


@pytest.mark.parametrize("site_refs,default_site,node_sites,effective_sites", [
    (["site/site-a"], "site/site-a", [None], ["site/site-a"]),
    (["site/site-b"], "site/site-a", ["site/site-b"], ["site/site-b"]),
    (["site/site-a", "site/site-b"], "site/site-a", [None, "site/site-b"], ["site/site-a", "site/site-b"]),
    (["site/site-b", "site/site-a"], "site/site-a", [None, "site/site-b"], ["site/site-a", "site/site-b"]),
    (["site/site-a", "site/site-c"], "site/site-a", [None], ["site/site-a"]),
    (None, "site/site-a", [None, "site/site-b"], ["site/site-a", "site/site-b"]),
    (["site/site-b"], None, ["site/site-b"], ["site/site-b"]),
])
def test_effective_node_sites_and_allowed_scope(site_refs, default_site, node_sites, effective_sites):
    spec = {} if site_refs is None else {"siteRefs": site_refs}
    model = InfrastructureModel.from_documents(model_documents(spec, node_sites, default_site))
    assert len(model.validate().warnings) == len(node_sites)
    for index, site in enumerate(effective_sites):
        assert model.topology.neighbors(f"cluster/lab/node/vm-{index}", "targets_site") == [site]
    # Network edges describe allowed scope, including unused sites, not actual endpoints.
    network_edges = [(edge["relation"], edge["target"]) for edge in model.topology.edges()
                     if edge["source"] == "network/shared"]
    assert sorted(network_edges) == [("scoped_to", site) for site in sorted(site_refs or [])]


@pytest.mark.parametrize("node_site", [None, "site/site-c"])
def test_attached_node_outside_scope_is_invalid(node_site):
    documents = model_documents({"siteRefs": ["site/site-b"]}, [node_site], "site/site-a")
    issues = assert_issue(documents, "network_site_scope", "/spec/nodes/0/networkAttachments/0/networkRef")
    assert len(issues) == 1
    assert issues[0].source == "memory#document=5"
    assert (node_site or "site/site-a") in issues[0].message
    assert "network/shared" in issues[0].message


def test_unresolved_site_adds_no_network_warning():
    unconstrained = InfrastructureModel.from_documents(model_documents({}, [None]))
    scoped = InfrastructureModel.from_documents(model_documents({"siteRefs": ["site/site-a"]}, [None]))
    assert scoped.validate().warnings == unconstrained.validate().warnings
    assert len(scoped.validate().warnings) == 1
    assert scoped.validate().warnings[0].path == "/spec/nodes/0/realization"
    assert scoped.validate().warnings[0].code == "unevaluated_requirement"
    assert scoped.topology.neighbors("cluster/lab/node/vm-0", "targets_site") == []


@pytest.mark.parametrize("node_site,default_site,path", [
    ("site/missing", "site/site-a", "/spec/nodes/0/placement/siteRef"),
    (None, "site/missing", "/spec/siteRef"),
])
def test_dangling_placement_reference_has_no_redundant_scope_error(node_site, default_site, path):
    documents = model_documents({"siteRefs": ["site/site-b"]}, [node_site], default_site)
    issues = assert_issue(documents, "unresolved_reference", path)
    assert len(issues) == 1


def test_exact_resource_does_not_infer_node_site():
    documents = model_documents({"siteRefs": ["site/site-a"]}, [None])
    node = documents[-1]["spec"]["nodes"][0]
    node.update(realization={"type": "baremetal"}, placement={"resourceRef": "server/host"})
    documents.append(document("Server", "host", {
        "siteRef": "site/site-c", "capabilities": {"nodeTypes": ["baremetal"]}}))
    model = InfrastructureModel.from_documents(documents)
    assert not model.validate().warnings
    assert model.topology.neighbors("cluster/lab/node/vm-0", "targets_site") == []


@pytest.mark.parametrize("layer", [None, "layer2", "layer3"])
@pytest.mark.parametrize("connectivity", [None, "multipoint", "point-to-point"])
@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_connectivity_endpoint_cardinality_for_any_layer(layer, connectivity, count):
    spec = {}
    if layer is not None:
        spec["layer"] = layer
    if connectivity is not None:
        spec["connectivity"] = connectivity
    documents = model_documents(spec, [None] * count)
    if connectivity == "point-to-point" and count > 2:
        issues = assert_issue(documents, "network_endpoint_cardinality", "/spec/connectivity")
        assert len(issues) == 1 and "found 3" in issues[0].message
        assert issues[0].source == "memory#document=4"
    else:
        model = InfrastructureModel.from_documents(documents)
        assert len(model.validate().warnings) == count
        assert model.get("network/shared")["spec"] == spec
        assert model.topology.nodes("Network")[0]["spec"] == spec


@pytest.mark.parametrize("value", [None, "", "unicast", "Multipoint", "point_to_point", [], 2])
def test_connectivity_enum_is_closed(value):
    assert_issue(model_documents({"connectivity": value}), "schema", "/spec/connectivity")


def test_connectivity_schema_has_no_default():
    schema = json.loads((ROOT / "schema/v0alpha1/network.json").read_text())
    assert "default" not in schema["properties"]["spec"]["properties"]["connectivity"]


def test_dual_stack_addresses_count_as_one_endpoint_per_attachment():
    documents = model_documents({"connectivity": "point-to-point", "prefixes": ["192.0.2.0/24", "2001:db8::/64"]},
                                [None, None])
    for index, node in enumerate(documents[-1]["spec"]["nodes"], 1):
        node["networkAttachments"][0]["addresses"] = [f"192.0.2.{index}", f"2001:db8::{index}"]
    model = InfrastructureModel.from_documents(documents)
    assert len(model.validate().warnings) == 2
    assert len(model.of_kind("NetworkAttachment")) == 2


def test_multiple_attachments_on_one_node_are_distinct_endpoints():
    documents = model_documents({"connectivity": "point-to-point"}, [None])
    node = documents[-1]["spec"]["nodes"][0]
    node["networkAttachments"] = [{"name": f"data-{index}", "networkRef": "network/shared"} for index in range(3)]
    assert_issue(documents, "network_endpoint_cardinality", "/spec/connectivity")


def test_endpoint_counts_span_clusters_and_are_per_network():
    documents = model_documents({"connectivity": "point-to-point"}, [None, None])
    second_cluster = deepcopy(documents[-1])
    second_cluster["metadata"]["name"] = "second"
    second_cluster["spec"]["nodes"] = second_cluster["spec"]["nodes"][:1]
    documents.append(second_cluster)
    assert_issue(documents, "network_endpoint_cardinality", "/spec/connectivity")
    second_cluster["spec"]["nodes"][0]["networkAttachments"][0]["networkRef"] = "network/other"
    documents.append(document("Network", "other", {"connectivity": "point-to-point"}))
    assert len(InfrastructureModel.from_documents(documents).validate().warnings) == 3


def test_multi_site_example_inherits_and_overrides_cluster_default():
    model = InfrastructureModel.load(ROOT / "examples/multi-site")
    assert len(model.validate().warnings) == 2
    assert model.get("cluster/multi-site")["spec"]["siteRef"] == "site/site-a"
    assert model.get("network/multi-site-data")["spec"] == {
        "layer": "layer2", "connectivity": "multipoint", "siteRefs": ["site/site-a", "site/site-b"]}
    assert model.topology.neighbors("cluster/multi-site/node/vm-a", "targets_site") == ["site/site-a"]
    assert model.topology.neighbors("cluster/multi-site/node/vm-b", "targets_site") == ["site/site-b"]
    assert len(model.of_kind("NetworkAttachment")) == 2
