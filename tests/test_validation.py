from copy import deepcopy

import pytest

from infra_model import InfrastructureModel, ValidationError


def assert_error(documents, code, text=None):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert code in {issue.code for issue in caught.value.issues}
    if text:
        assert text in str(caught.value)
    assert all(issue.source and issue.path for issue in caught.value.issues)
    return caught.value


@pytest.mark.parametrize("target,field,reference", [
    ("Server/amst-w2", "siteRef", "site/missing"),
    ("NetworkDevice/amst-data-sw", "siteRef", "site/missing"),
    ("Network/lab-management", "siteRef", "site/missing"),
    ("Cluster/openstack-lab", "siteRef", "site/missing"),
    ("Server/amst-w2", "profileRef", "hardware-profile/missing"),
    ("NetworkDevice/amst-data-sw", "profileRef", "hardware-profile/missing"),
])
def test_bad_top_level_references(documents, inventory, target, field, reference):
    inventory[target]["spec"][field] = reference
    assert_error(documents, "unresolved_reference", reference)


@pytest.mark.parametrize("field,reference", [("siteRef", "site/missing"), ("resourceRef", "server/missing")])
def test_bad_node_references(documents, inventory, field, reference):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["realization"] = {"type": "baremetal"}
    node.setdefault("placement", {})[field] = reference
    assert_error(documents, "unresolved_reference", reference)


def test_bad_network_ref(documents, inventory):
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["networkAttachments"][0]["networkRef"] = "network/missing"
    assert_error(documents, "unresolved_reference", "network/missing")


@pytest.mark.parametrize("endpoint", ["server/amst-w2/network-adapter/slot2/interface/missing", "network-device/amst-data-sw/interface/missing"])
def test_invalid_link_endpoint(documents, inventory, endpoint):
    inventory["Link/amst-w2-data"]["spec"]["endpoints"][0] = endpoint
    assert_error(documents, "unresolved_reference", endpoint)


@pytest.mark.parametrize("field,value", [("siteRef", "network/lab-management"), ("profileRef", "server/amst-w2")])
def test_reference_wrong_kind_rejected_by_schema(documents, inventory, field, value):
    inventory["Server/amst-w2"]["spec"][field] = value
    assert_error(documents, "schema")


@pytest.mark.parametrize("field,value", [("resourceRef", "network-device/amst-data-sw"), ("resourceRef", "cluster/openstack-lab/node/compute-1")])
def test_resource_ref_only_accepts_server(documents, inventory, field, value):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["realization"] = {"type": "baremetal"}
    node.setdefault("placement", {})[field] = value
    assert_error(documents, "schema")


def test_network_ref_must_be_network(documents, inventory):
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["networkAttachments"][0]["networkRef"] = "site/lab-site"
    assert_error(documents, "schema")


@pytest.mark.parametrize("endpoints", [[], ["network-device/amst-data-sw/interface/swp1"], ["server/amst-w2/network-adapter/slot2/interface/p1"] * 2, ["network-device/amst-data-sw/interface/swp1"] * 3, ["server/amst-w2/network-adapter/slot2", "network-device/amst-data-sw/interface/swp1"]])
def test_link_requires_two_distinct_interfaces(documents, inventory, endpoints):
    inventory["Link/amst-w2-data"]["spec"]["endpoints"] = endpoints
    assert_error(documents, "schema")


def test_duplicate_mac_case_insensitive_across_resources(documents, inventory):
    inventory["Server/amst-w2"]["spec"]["networkAdapters"][0]["interfaces"][0]["macAddress"] = "02:AA:BB:CC:DD:EE"
    inventory["NetworkDevice/amst-data-sw"]["spec"]["interfaces"][0]["macAddress"] = "02:aa:bb:cc:dd:ee"
    assert_error(documents, "duplicate_mac", "02:aa:bb:cc:dd:ee")


@pytest.mark.parametrize("address", ["0000:41:00.0", "41:00.0"])
def test_duplicate_pci_between_component_types(documents, inventory, address):
    inventory["Server/amst-w2"]["spec"]["storageDevices"][0]["pciAddress"] = address
    assert_error(documents, "duplicate_pci", "0000:41:00.0")


def test_duplicate_pci_between_adapters_case_insensitive(documents, inventory):
    adapters = inventory["Server/amst-w2"]["spec"]["networkAdapters"]
    adapters[0]["pciAddress"] = "0000:AB:00.0"
    adapters.append({"name": "slot3", "pciAddress": "ab:00.0", "interfaces": [{"name": "p1"}]})
    assert_error(documents, "duplicate_pci")


def test_pci_reuse_on_different_servers_is_valid(documents, inventory):
    inventory["Server/P3-CPU-004"]["spec"]["networkAdapters"][0]["pciAddress"] = "0000:41:00.0"
    InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("address", ["203.0.113.1", "2001:db8::1"])
def test_address_outside_network_prefix(documents, inventory, address):
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["networkAttachments"][0]["addresses"] = [address]
    assert_error(documents, "address_outside_prefix", address)


def test_addresses_require_network_prefix(documents, inventory):
    inventory["Network/lab-management"]["spec"].pop("prefixes")
    assert_error(documents, "address_outside_prefix")


def test_dual_stack_multiple_prefixes(documents, inventory):
    inventory["Network/lab-management"]["spec"]["prefixes"].extend(["2001:db8::/64", "203.0.113.0/24"])
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["networkAttachments"][0]["addresses"].extend(["2001:db8::10", "203.0.113.7"])
    InfrastructureModel.from_documents(documents).validate()


def test_logical_network_without_prefix_allows_addressless_attachment(documents, inventory):
    inventory["Network/lab-management"]["spec"] = {"layer": "layer2"}
    for node in inventory["Cluster/openstack-lab"]["spec"]["nodes"]:
        node["networkAttachments"][0].pop("addresses")
    InfrastructureModel.from_documents(documents).validate()


def test_duplicate_top_level_name(documents):
    documents.append(deepcopy(documents[0]))
    assert_error(documents, "duplicate_name")


def test_name_reuse_across_kinds(documents):
    documents.append({"apiVersion": "infra.model/v0alpha1", "kind": "Network", "metadata": {"name": "AMST"}, "spec": {}})
    InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("collection", ["adapters", "interfaces", "switch_interfaces", "devices", "nodes", "attachments"])
def test_duplicate_component_names(documents, inventory, collection):
    server = inventory["Server/amst-w2"]["spec"]
    nodes = inventory["Cluster/openstack-lab"]["spec"]["nodes"]
    items = {
        "adapters": server["networkAdapters"],
        "interfaces": server["networkAdapters"][0]["interfaces"],
        "switch_interfaces": inventory["NetworkDevice/amst-data-sw"]["spec"]["interfaces"],
        "devices": server["storageDevices"], "nodes": nodes,
        "attachments": nodes[0]["networkAttachments"],
    }[collection]
    items.append(deepcopy(items[0]))
    assert_error(documents, "duplicate_component_name")


def test_component_names_may_repeat_across_collections(documents, inventory):
    inventory["Server/amst-w2"]["spec"]["storageDevices"][0]["name"] = "slot2"
    model = InfrastructureModel.from_documents(documents)
    assert model.get("server/amst-w2/storage-device/slot2")["kind"] == "StorageDevice"
    assert model.get("server/amst-w2/network-adapter/slot2")["kind"] == "NetworkAdapter"
    assert len(model.topology.nodes("StorageDevice")) == 1


def test_aggregate_semantic_diagnostics(documents, inventory):
    inventory["Server/amst-w2"]["spec"]["siteRef"] = "site/missing"
    inventory["Server/P3-CPU-004"]["spec"]["profileRef"] = "hardware-profile/missing"
    error = assert_error(documents, "unresolved_reference")
    assert len(error.issues) == 2
