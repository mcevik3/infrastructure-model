from copy import deepcopy

import pytest

from infra_model import InfrastructureModel, ValidationError
from conftest import ROOT


@pytest.mark.parametrize("directory,count", [("examples", 16), ("examples/baremetal", 4), ("examples/amst", 5), ("examples/chameleon-like", 3), ("examples/fabric-like", 4)])
def test_examples_validate(directory, count):
    model = InfrastructureModel.load(ROOT / directory)
    report = model.validate()
    assert report.document_count == count
    expected_warnings = 2 if directory in {"examples", "examples/fabric-like"} else 0
    assert len(report.warnings) == expected_warnings
    assert all("UNKNOWN" in warning.message for warning in report.warnings)


def test_public_api_and_embedded_identities():
    model = InfrastructureModel.load(ROOT / "examples")
    assert model.get("server/amst-w2")["kind"] == "Server"
    assert len(model.servers()) == 3
    assert len(model.networks()) == 2
    interface = model.get("server/amst-w2/network-adapter/slot2/interface/p1")
    assert interface["kind"] == "Interface"
    node = model.get("cluster/openstack-lab/node/controller-1")
    assert node["kind"] == "ClusterNode"
    assert len(model.documents) == 16
    assert all(doc["kind"] != "ClusterNode" for doc in model.documents)
    assert len(model.topology) == model.validate().entity_count == 55
    with pytest.raises(KeyError):
        model.get("server/missing")


def test_reads_are_defensive_copies(documents):
    model = InfrastructureModel.from_documents(documents)
    original = deepcopy(documents)
    server = model.get("server/amst-w2")
    server["spec"]["siteRef"] = "site/changed"
    model.servers()[0]["metadata"]["name"] = "changed"
    model.documents.clear()
    model.topology.nodes()[0].clear()
    model.topology.edges()[0].clear()
    documents.clear()
    assert model.get("server/amst-w2")["spec"]["siteRef"] == "site/AMST"
    assert len(model.documents) == len(original)
    assert model.topology.nodes()[0]
    assert model.topology.edges()[0]


def test_source_not_mutated_and_extensions_status_preserved(documents, inventory):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["extensions"] = {"example.org/custom": {"nested": [1, True, None], "siteRef": "opaque"}}
    node["status"] = {"extensions": {"example.org/observation": {"observedAddress": "203.0.113.1", "resourceRef": "opaque"}}}
    before = deepcopy(documents)
    model = InfrastructureModel.from_documents(documents)
    model.validate()
    normalized = model.get("cluster/openstack-lab/node/controller-1")
    assert normalized["extensions"] == node["extensions"]
    assert normalized["status"] == node["status"]
    assert documents == before


def test_queries_validate_before_exposing_invalid_data(documents, inventory):
    inventory["Server/amst-w2"]["spec"]["siteRef"] = "site/missing"
    model = InfrastructureModel.from_documents(documents)
    for operation in [lambda: model.get("server/amst-w2"), model.servers, lambda: model.topology]:
        with pytest.raises(ValidationError):
            operation()


def test_profile_defaults_merge_without_inheriting_status_or_components(documents, inventory):
    profile = inventory["HardwareProfile/dell-r7525"]
    profile["status"] = {"availability": "unavailable", "extensions": {"example.org/observation": {"value": 1}}}
    profile["spec"]["capabilities"]["extensions"] = {"example.org/defaults": {"keep": "inherited", "replace": [1, 2], "mapping": {"a": 1, "b": 2}}}
    server = inventory["Server/amst-w2"]
    server["spec"]["capabilities"] = {"nodeTypes": ["baremetal"], "compute": {"cpu": {"cores": 32}}, "extensions": {"example.org/defaults": {"replace": [3], "mapping": {"a": 9}}}}
    model = InfrastructureModel.from_documents(documents)
    effective = model.effective_capabilities("server/amst-w2")
    assert effective["nodeTypes"] == ["baremetal"]
    assert effective["compute"]["cpu"]["cores"] == 32
    assert effective["compute"]["cpu"]["threads"] == 128
    assert effective["extensions"]["example.org/defaults"] == {"keep": "inherited", "replace": [3], "mapping": {"a": 9, "b": 2}}
    assert "status" not in model.get("server/amst-w2")
    assert model.get("server/amst-w2")["spec"]["storage"]["devices"][0]["name"] == "nvme0"
    assert profile["spec"]["capabilities"]["compute"]["cpu"]["cores"] == 64


@pytest.mark.parametrize("override", [None, {"cpu": None}, {"cpu": {"cores": None}}])
def test_no_unset_cpu_overlay(documents, inventory, override):
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"compute": override}
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize('override', [None, {'capacity': None}, {'capacity': {'value': 2}}])
def test_no_unset_or_partial_quantity_overlay(documents, inventory, override):
    inventory['Server/amst-w2']['spec']['capabilities'] = {'memory': override}
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()
