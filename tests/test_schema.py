from copy import deepcopy
from importlib.resources import files
import json

from jsonschema import Draft202012Validator
import pytest

from infra_model import InfrastructureModel, ValidationError
from infra_model.schema import KIND_PATHS


@pytest.mark.parametrize("name", ["common", *KIND_PATHS.values()])
def test_packaged_schemas_are_valid_draft_2020_12(name):
    text = files("infra_model._schemas").joinpath(name + ".json").read_text()
    schema = json.loads(text)
    Draft202012Validator.check_schema(schema)
    assert "networkx" not in text.lower()
    assert schema["$id"].endswith(f"/{name}.json")


@pytest.mark.parametrize("kind", ["ClusterNode", "Unknown", None, [], {}])
def test_top_level_kinds_are_closed(documents, kind):
    documents[0]["kind"] = kind
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("value", ["wrong/v0alpha1", "infra.model/v1", None, 1])
def test_api_version_is_explicit(documents, value):
    documents[0]["apiVersion"] = value
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("name", ["", "slash/name", "has space", ".hidden", "../escape", "trailing\n"])
def test_names_are_path_safe(documents, name):
    documents[0]["metadata"]["name"] = name
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("quantity", [8, "8 GiB", {"value": 8}, {"unit": "GiB"}, {"value": -1, "unit": "GiB"}, {"value": 0, "unit": "GiB"}, {"value": True, "unit": "GiB"}, {"value": 8, "unit": "bananas"}, {"value": 8, "unit": "core"}])
def test_quantities_are_explicit_positive_and_dimensioned(documents, inventory, quantity):
    inventory["HardwareProfile/dell-r7525"]["spec"]["capabilities"]["memory"]["capacity"] = quantity
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


def test_core_counts_are_integral(documents, inventory):
    inventory["HardwareProfile/dell-r7525"]["spec"]["capabilities"]["compute"]["cores"] = {"value": 1.5, "unit": "core"}
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("prefix", ["192.0.2.1/24", "192.0.2.0/33", "bad", "192.0.2.0", "2001:db8::1/64", "fe80::%eth0/64", "192.0.2.0/255.255.255.0", "192.0.2.0/0.0.0.255", "192.0.2.0/+24", "192.0.2.0/024", "192.0.2.0/24\n"])
def test_malformed_network_prefix(documents, inventory, prefix):
    inventory["Network/lab-management"]["spec"]["prefixes"] = [prefix]
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("address", ["999.1.1.1", "host.example", "192.0.2.1/24", "fe80::1%eth0"])
def test_static_addresses_are_plain_ip_literals(documents, inventory, address):
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["networkAttachments"][0]["addresses"] = [address]
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("address", ["not-pci", "0000:41:00.8", "0000:41:00", "00000:41:00.0", "0000:41:20.0", "0000:41:ff.0", "0000:41:00.0\n"])
def test_malformed_pci(documents, inventory, address):
    inventory["Server/amst-w2"]["spec"]["networkAdapters"][0]["pciAddress"] = address
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("address", ["not-mac", "00:11:22:33:44", "00:11:22:33:44:GG", "00:11:22:33:44:55\n"])
def test_malformed_mac(documents, inventory, address):
    inventory["Server/amst-w2"]["spec"]["networkAdapters"][0]["interfaces"][0]["macAddress"] = address
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("location", ["root", "spec", "metadata", "node", "adapter", "requirements"])
def test_unknown_core_fields_rejected(documents, inventory, location):
    server = inventory["Server/amst-w2"]
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    container = {"root": server, "spec": server["spec"], "metadata": server["metadata"], "node": node,
                 "adapter": server["spec"]["networkAdapters"][0], "requirements": node["requirements"]}[location]
    container["unknownField"] = "value"
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("key", ["unqualified", "example.org/key\n"])
def test_extensions_require_namespace(documents, key):
    documents[0]["extensions"] = {key: {"data": 1}}
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


def test_capabilities_and_requirements_not_interchangeable(documents, inventory):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["capabilities"] = deepcopy(node["requirements"])
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


def test_graph_ids_are_not_authorable_source_fields(documents):
    documents[0]["id"] = "site/override"
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


def test_references_disallow_trailing_newline(documents, inventory):
    inventory["Server/amst-w2"]["spec"]["siteRef"] = "site/AMST\n"
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert caught.value.issues[0].code == "schema"


@pytest.mark.parametrize("cpu", [4, {"count": 4}, {"unit": "vcpu"}, {"count": 0, "unit": "vcpu"}, {"count": 1.5, "unit": "vcpu"}, {"count": True, "unit": "vcpu"}, {"count": 4, "unit": "socket"}, {"value": 4, "unit": "core"}])
def test_cpu_requirements_have_explicit_positive_count_and_unit(documents, inventory, cpu):
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["requirements"]["compute"]["cpu"] = cpu
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("field", ["cores", "threads", "sockets"])
def test_inventory_cpu_fields_not_requirement_fields(documents, inventory, field):
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["requirements"]["compute"][field] = {"value": 4, "unit": "core"}
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


def test_physical_sockets_are_explicit_inventory_quantities(documents, inventory):
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"compute": {"sockets": {"value": 2, "unit": "socket"}}}
    model = InfrastructureModel.from_documents(documents)
    assert model.effective_capabilities("server/amst-w2")["compute"]["sockets"] == {"value": 2, "unit": "socket"}


@pytest.mark.parametrize("location", ["capability", "requirement", "device"])
@pytest.mark.parametrize("field,value", [("media", "nvme"), ("medium", "mixed"), ("medium", "nvme"), ("protocol", "mixed"), ("protocol", "ssd")])
def test_storage_vocabulary_is_separated(documents, inventory, location, field, value):
    container = {
        "capability": inventory["HardwareProfile/dell-r7525"]["spec"]["capabilities"]["storage"],
        "requirement": inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["requirements"]["storage"],
        "device": inventory["Server/amst-w2"]["spec"]["storageDevices"][0],
    }[location]
    container[field] = value
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("kind", list(KIND_PATHS))
@pytest.mark.parametrize("status", [
    {"extensions": {"unqualified": {}}},
    {"extensions": {"example.org/key\n": {}}},
    {"providerState": "active"},
    {"observedAt": "yesterday"},
    {"availability": 1},
    {"operationalState": {}},
])
def test_top_level_status_is_generic_and_namespaced(documents, kind, status):
    next(doc for doc in documents if doc["kind"] == kind)["status"] = status
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("kind", list(KIND_PATHS))
def test_generic_status_and_provider_observations_are_valid(documents, kind):
    document = next(doc for doc in documents if doc["kind"] == kind)
    document["status"] = {"availability": "available", "operationalState": "ready", "powerState": "unknown", "observedAt": "2026-01-01T00:00:00Z", "extensions": {"example.org/observation": {"resourceRef": "opaque", "nested": [None, True, 2]}}}
    model = InfrastructureModel.from_documents(documents)
    model.validate()
    assert next(doc for doc in model.documents if doc["kind"] == kind)["status"] == document["status"]


def test_vm_cannot_exactly_select_server(documents, inventory):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["placement"] = {"resourceRef": "server/amst-w2", "siteRef": "site/AMST"}
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert {issue.code for issue in caught.value.issues} == {"schema"}
    assert caught.value.issues[0].path.endswith("/placement")


@pytest.mark.parametrize("field,value", [("nodeType", "vm"), ("resourceRef", "server/amst-w2"), ("siteRef", "site/AMST")])
def test_old_node_placement_fields_are_rejected(documents, inventory, field, value):
    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0][field] = value
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


def test_vm_site_placement_and_namespaced_affinity_are_valid(documents, inventory):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["placement"] = {"siteRef": "site/AMST", "extensions": {"example.org/host-affinity": {"host": "opaque"}}}
    model = InfrastructureModel.from_documents(documents)
    assert model.validate().warnings
    assert model.topology.neighbors("cluster/openstack-lab/node/controller-1", "targets_site") == ["site/AMST"]
    assert not model.topology.edges("placed_on")


@pytest.mark.parametrize("timestamp", ["2026-02-30T00:00:00Z", "2026-01-01", "2026-01-01T00:00:00", "2026-01-01T00:00:00+25:00", "2026-01-01T00:00:00Z\n"])
def test_observation_time_requires_valid_date_and_timezone(documents, timestamp):
    documents[0]["status"] = {"observedAt": timestamp}
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("field,value", [("medium", "hdd"), ("medium", "ssd"), ("medium", "other"), ("protocol", "nvme"), ("protocol", "sata"), ("protocol", "sas"), ("protocol", "scsi"), ("protocol", "virtio"), ("protocol", "other")])
def test_storage_vocabulary_accepts_supported_values(documents, inventory, field, value):
    storage = inventory["HardwareProfile/dell-r7525"]["spec"]["capabilities"]["storage"]
    storage[field] = value
    InfrastructureModel.from_documents(documents).validate()


def test_unknown_storage_composition_can_be_omitted(documents, inventory):
    for storage in [inventory["HardwareProfile/dell-r7525"]["spec"]["capabilities"]["storage"], inventory["Server/amst-w2"]["spec"]["storageDevices"][0]]:
        storage.pop("medium")
        storage.pop("protocol")
    InfrastructureModel.from_documents(documents).validate()
