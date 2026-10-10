from copy import deepcopy

import pytest

from infra_model import InfrastructureModel


LEFT = "server/amst-w2/network-adapter/slot2/interface/p1"
RIGHT = "network-device/amst-data-sw/interface/swp1"


def test_physical_topology_and_link_identity(documents):
    topology = InfrastructureModel.from_documents(documents).topology
    assert topology.neighbors(LEFT, "physical_link") == [RIGHT]
    assert topology.neighbors(RIGHT, "physical_link") == [LEFT]
    assert topology.neighbors("link/amst-w2-data", "terminates_at") == sorted([LEFT, RIGHT])
    assert len(topology.edges("physical_link")) == 2
    assert len(topology.nodes("Link")) == 1
    assert topology.neighbors(LEFT, "contains", "in") == ["server/amst-w2/network-adapter/slot2"]


def test_parallel_links_are_not_collapsed(documents, inventory):
    other = deepcopy(inventory["Link/amst-w2-data"])
    other["metadata"]["name"] = "parallel"
    documents.append(other)
    topology = InfrastructureModel.from_documents(documents).topology
    edges = topology.edges("physical_link")
    assert len(edges) == 4
    assert {edge["key"] for edge in edges} == {"link/amst-w2-data", "link/parallel"}
    assert {edge["link"] for edge in edges} == {"link/amst-w2-data", "link/parallel"}


def test_logical_attachments_separate_from_physical_links(documents):
    topology = InfrastructureModel.from_documents(documents).topology
    node = "cluster/openstack-lab/node/controller-1"
    assert topology.neighbors(node, "network_attachment") == ["network/lab-management", "network/lab-storage"]
    assert topology.neighbors(node, "physical_link") == []
    assert topology.neighbors(node, "targets_site") == ["site/lab-site"]
    assert topology.neighbors("network/lab-management", "scoped_to") == ["site/lab-site"]
    assert topology.neighbors("network/lab-management", "located_at") == []
    attachment = node + "/network-attachment/management"
    assert topology.neighbors(attachment, "attached_to") == ["network/lab-management"]


def test_parallel_logical_attachments_remain_distinct(documents, inventory):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["networkAttachments"].append({"name": "second-management", "networkRef": "network/lab-management"})
    topology = InfrastructureModel.from_documents(documents).topology
    edges = [edge for edge in topology.edges("network_attachment") if edge["source"].endswith("/controller-1") and edge["target"] == "network/lab-management"]
    assert len(edges) == 2
    assert len({edge["key"] for edge in edges}) == 2


def test_graph_has_no_implicit_or_dangling_nodes(documents):
    model = InfrastructureModel.from_documents(documents)
    topology = model.topology
    assert all(node.get("id") and node.get("kind") for node in topology.nodes())
    assert all(edge["source"] in topology and edge["target"] in topology for edge in topology.edges())
    assert topology.neighbors("server/amst-w2", "uses_profile") == ["hardware-profile/dell-r7525"]
    assert topology.neighbors("server/amst-w2", "located_at", "both") == ["site/AMST"]
    assert topology.neighbors("network-device/amst-data-sw", "located_at") == ["site/AMST"]
    assert not hasattr(topology, "graph")


def test_exact_resource_edge(documents, inventory, amst_network_scope):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node.update(realization={"type": "baremetal"}, placement={"resourceRef": "server/amst-w2", "siteRef": "site/AMST"})
    topology = InfrastructureModel.from_documents(documents).topology
    assert topology.neighbors("cluster/openstack-lab/node/controller-1", "placed_on") == ["server/amst-w2"]


def test_neighbor_errors(documents):
    topology = InfrastructureModel.from_documents(documents).topology
    with pytest.raises(KeyError):
        topology.neighbors("missing")
    with pytest.raises(ValueError):
        topology.neighbors(LEFT, direction="sideways")
