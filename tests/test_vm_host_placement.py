from copy import deepcopy
import json

import pytest

from infra_model import InfrastructureModel, ValidationError
from infra_model.loader import SourceDocument
from infra_model.registry import Registry
from infra_model.validator import validate_registry
from conftest import ROOT


NODE_ID = "cluster/lab/node/vm-1"


def document(kind, name, spec):
    return {"apiVersion": "infra.model/v0alpha1", "kind": kind,
            "metadata": {"name": name}, "spec": spec}


@pytest.fixture
def source():
    return [
        document("Site", "site-a", {}),
        document("Site", "site-b", {}),
        document("Server", "host-a", {"siteRef": "site/site-a"}),
        document("Server", "host-b", {"siteRef": "site/site-b"}),
        document("Network", "shared", {"siteRefs": ["site/site-a"]}),
        document("Cluster", "lab", {"siteRef": "site/site-a", "nodes": [{
            "name": "vm-1", "realization": {"type": "vm"},
            "placement": {"hostRef": "server/host-a"},
            "networkAttachments": [{"name": "data", "networkRef": "network/shared"}],
        }]}),
    ]


def node(source):
    return source[-1]["spec"]["nodes"][0]


def assert_error(source, code, path):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(source).validate()
    assert any(issue.code == code and issue.path == path for issue in caught.value.issues), str(caught.value)
    return caught.value.issues


def test_vm_host_reference_accepted_and_source_preserved(source):
    before = deepcopy(source)
    model = InfrastructureModel.from_documents(source)
    assert len(model.validate().warnings) == 1
    assert model.get(NODE_ID)["placement"] == {"hostRef": "server/host-a"}
    assert source == before
    schema = json.loads((ROOT / "schema/v0alpha1/common.json").read_text())
    host_ref = schema["$defs"]["placement"]["properties"]["hostRef"]
    assert host_ref["$ref"].endswith("#/$defs/resourceRef")
    assert "default" not in host_ref


@pytest.mark.parametrize("reference", ["host-a", "Server/host-a", "server/host a", "server/host-a/extra",
                                       "server/host-a\n", "site/site-a", "network/shared", "", None, 1])
def test_host_reference_shape_and_wrong_kind_rejected(source, reference):
    node(source)["placement"]["hostRef"] = reference
    assert_error(source, "schema", "/spec/nodes/0/placement/hostRef")


def test_dangling_host_reference(source):
    node(source)["placement"]["hostRef"] = "server/missing"
    issues = assert_error(source, "unresolved_reference", "/spec/nodes/0/placement/hostRef")
    assert len(issues) == 1
    assert issues[0].source == "memory#document=6"
    assert "server/missing" in issues[0].message


def test_semantic_reference_resolution_checks_server_kind(source):
    # The public schema already rejects this kind prefix. Exercise the resolver
    # independently too, as with the existing resourceRef semantic-boundary test.
    node(source)["placement"]["hostRef"] = "site/site-a"
    registry = Registry([SourceDocument(data, "memory") for data in source])
    with pytest.raises(ValidationError) as caught:
        validate_registry(registry)
    assert caught.value.issues[0].code == "reference_kind"
    assert caught.value.issues[0].path == "/spec/nodes/0/placement/hostRef"


def test_baremetal_host_is_semantic_error(source):
    node(source)["realization"]["type"] = "baremetal"
    issues = assert_error(source, "invalid_placement", "/spec/nodes/0/placement/hostRef")
    assert len(issues) == 1


@pytest.mark.parametrize("realization", ["vm", "baremetal"])
def test_host_and_resource_refs_rejected(source, realization):
    node(source)["realization"]["type"] = realization
    node(source)["placement"]["resourceRef"] = "server/host-a"
    # The existing VM/resourceRef schema constraint remains in force.
    code = "schema" if realization == "vm" else "invalid_placement"
    assert_error(source, code, "/spec/nodes/0/placement")
    registry = Registry([SourceDocument(data, "memory") for data in source])
    with pytest.raises(ValidationError) as caught:
        validate_registry(registry)
    assert len(caught.value.issues) == 1
    assert caught.value.issues[0].code == "invalid_placement"
    assert "mutually exclusive" in caught.value.issues[0].message


def test_unsupported_realization_cannot_use_host_at_semantic_boundary(source):
    node(source)["realization"]["type"] = "other"
    registry = Registry([SourceDocument(data, "memory") for data in source])
    with pytest.raises(ValidationError) as caught:
        validate_registry(registry)
    assert caught.value.issues[0].code == "invalid_placement"


@pytest.mark.parametrize("override,host,valid", [
    (None, "server/host-a", True),
    (None, "server/host-b", False),
    ("site/site-b", "server/host-b", True),
    ("site/site-b", "server/host-a", False),
])
def test_effective_site_consistency(source, override, host, valid):
    source[4]["spec"]["siteRefs"].append("site/site-b")
    node(source)["placement"]["hostRef"] = host
    if override:
        node(source)["placement"]["siteRef"] = override
    if valid:
        model = InfrastructureModel.from_documents(source)
        assert len(model.validate().warnings) == 1
        assert model.topology.neighbors(NODE_ID, "targets_site") == [override or "site/site-a"]
    else:
        issues = assert_error(source, "incompatible_resource", "/spec/nodes/0/placement/hostRef")
        assert len(issues) == 1
        assert "UNSATISFIED" in issues[0].message and host in issues[0].message


def test_host_only_derives_no_site_for_registry_graph_or_network_scope(source):
    source[-1]["spec"].pop("siteRef")
    # This host is outside the Network's allowed scope. It must not become the
    # node's effective site, create a scope error, or add a Network warning.
    node(source)["placement"]["hostRef"] = "server/host-b"
    registry = Registry([SourceDocument(data, "memory") for data in source])
    assert registry.node_site(registry.entries[NODE_ID]) is None
    model = InfrastructureModel.from_documents(source)
    warnings = model.validate().warnings
    assert len(warnings) == 1
    assert warnings[0].code == "unevaluated_requirement"
    assert warnings[0].path == "/spec/nodes/0/realization"
    assert model.topology.neighbors(NODE_ID, "targets_site") == []
    assert model.topology.neighbors(NODE_ID, "targets_host") == ["server/host-b"]
    assert model.topology.neighbors("network/shared", "scoped_to") == ["site/site-a"]


def test_network_scope_still_checks_explicit_effective_site(source):
    node(source)["placement"] = {"siteRef": "site/site-b", "hostRef": "server/host-b"}
    issues = assert_error(source, "network_site_scope", "/spec/nodes/0/networkAttachments/0/networkRef")
    assert len(issues) == 1


def test_host_identity_does_not_evaluate_vm_capacity_or_host_capabilities(source):
    # Host inventory explicitly cannot meet the requested values, but hostRef
    # introduces neither a compatibility evaluator nor scheduling/accounting.
    source[2]["spec"]["capabilities"] = {"nodeTypes": ["baremetal"],
                                        "compute": {"cpu": {"cores": 1}},
                                        "memory": {"capacity": {"value": 1, "unit": "GiB"}}}
    node(source)["requirements"] = {"compute": {"cpu": {"count": 1000, "unit": "vcpu"}},
                                    "memory": {"capacity": {"value": 1000, "unit": "GiB"}}}
    warnings = InfrastructureModel.from_documents(source).validate().warnings
    assert len(warnings) == 1 and warnings[0].code == "unevaluated_requirement"
    assert "does not prove" in warnings[0].message


def test_host_graph_relationships_and_existing_site_edges(source):
    topology = InfrastructureModel.from_documents(source).topology
    assert topology.neighbors(NODE_ID, "targets_host") == ["server/host-a"]
    assert topology.neighbors("server/host-a", "targets_host", "in") == [NODE_ID]
    assert topology.neighbors(NODE_ID, "targets_site") == ["site/site-a"]
    assert topology.neighbors("server/host-a", "located_at") == ["site/site-a"]
    assert topology.neighbors(NODE_ID, "located_at") == []
    assert topology.neighbors(NODE_ID, "placed_on") == []


def test_existing_site_only_vm_placement_remains_unchanged(source):
    node(source).pop("placement")
    model = InfrastructureModel.from_documents(source)
    assert len(model.validate().warnings) == 1
    assert model.topology.neighbors(NODE_ID, "targets_site") == ["site/site-a"]
    assert model.topology.neighbors(NODE_ID, "targets_host") == []
    assert "hostRef" not in model.get(NODE_ID).get("placement", {})


def test_existing_baremetal_resource_placement_remains_unchanged(source):
    node(source)["realization"]["type"] = "baremetal"
    node(source)["placement"] = {"resourceRef": "server/host-a"}
    source[2]["spec"]["capabilities"] = {"nodeTypes": ["baremetal"]}
    model = InfrastructureModel.from_documents(source)
    assert not model.validate().warnings
    assert model.topology.neighbors(NODE_ID, "placed_on") == ["server/host-a"]
    assert model.topology.neighbors(NODE_ID, "targets_host") == []
