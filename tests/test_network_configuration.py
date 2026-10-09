from copy import deepcopy

import pytest

from infra_model import InfrastructureModel, ValidationError
from conftest import ROOT


def network(spec, name="shared"):
    return {"apiVersion": "infra.model/v0alpha1", "kind": "Network",
            "metadata": {"name": name}, "spec": spec}


def assert_invalid(spec, code, path):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents([network(spec)]).validate()
    assert any(issue.code == code and issue.path == path for issue in caught.value.issues), str(caught.value)
    assert all(issue.source == "memory#document=1" for issue in caught.value.issues)


@pytest.mark.parametrize("prefix,address", [
    ("192.0.2.0/24", "192.0.2.1"),
    ("192.0.2.0/24", "198.51.100.1"),
    ("2001:db8:1234:1::/64", "2001:db8:1234:1::1"),
    ("2001:db8:1234:1::/64", "fe80::1"),
])
def test_default_gateway_valid(prefix, address):
    spec = {"prefixes": [prefix], "defaultGateways": [{"prefix": prefix, "address": address}]}
    assert not InfrastructureModel.from_documents([network(spec)]).validate().warnings


def test_gateway_prefix_uses_semantic_network_comparison():
    spec = {"prefixes": ["2001:DB8:1234:1::/64"], "defaultGateways": [
        {"prefix": "2001:0db8:1234:0001:0000:0000:0000:0000/64", "address": "fe80::1"}]}
    model = InfrastructureModel.from_documents([network(spec)])
    assert not model.validate().warnings
    assert model.get("network/shared")["spec"] == spec


@pytest.mark.parametrize("prefixes", [None, [], ["198.51.100.0/24"], ["192.0.2.0/25"], ["192.0.0.0/16"]])
def test_gateway_prefix_must_be_declared_on_same_network(prefixes):
    spec = {"defaultGateways": [{"prefix": "192.0.2.0/24", "address": "192.0.2.1"}]}
    if prefixes is not None:
        spec["prefixes"] = prefixes
    assert_invalid(spec, "gateway_prefix_not_declared", "/spec/defaultGateways/0/prefix")
    with pytest.raises(ValidationError, match="gateway_prefix_not_declared"):
        InfrastructureModel.from_documents([
            network(spec), network({"prefixes": ["192.0.2.0/24"]}, "other")]).validate()


@pytest.mark.parametrize("prefix,address", [
    ("192.0.2.0/24", "fe80::1"), ("2001:db8::/64", "192.0.2.1"),
])
def test_gateway_address_family_must_match_prefix(prefix, address):
    assert_invalid({"prefixes": [prefix], "defaultGateways": [{"prefix": prefix, "address": address}]},
                   "gateway_address_family", "/spec/defaultGateways/0/address")


@pytest.mark.parametrize("second_prefix,second_address", [
    ("2001:db8::/64", "fe80::1"),
    ("2001:db8::/64", "fe80::2"),
    ("2001:0DB8:0:0:0:0:0:0/64", "fe80::1"),
    ("2001:0DB8:0:0:0:0:0:0/64", "fe80::2"),
])
def test_only_one_gateway_per_equivalent_prefix(second_prefix, second_address):
    spec = {"prefixes": ["2001:db8::/64"], "defaultGateways": [
        {"prefix": "2001:db8::/64", "address": "fe80::1"},
        {"prefix": second_prefix, "address": second_address}]}
    assert_invalid(spec, "duplicate_default_gateway", "/spec/defaultGateways/1/prefix")


def test_gateway_address_can_be_reused_for_distinct_prefixes_and_networks():
    prefixes = ["2001:db8::/64", "2001:db8:1::/64"]
    spec = {"prefixes": prefixes, "defaultGateways": [
        {"prefix": prefix, "address": "fe80::1"} for prefix in prefixes]}
    assert not InfrastructureModel.from_documents([network(spec), network(spec, "other")]).validate().warnings


@pytest.mark.parametrize("prefix", [
    "bad", "192.0.2.1/24", "2001:db8::1/64", "192.0.2.0", "192.0.2.0/33",
    "2001:db8::/129", "192.0.2.0/255.255.255.0", "192.0.2.0/0.0.0.255",
    "192.0.2.0/+24", "192.0.2.0/024", "192.0.2.0/24\n", "fe80::%eth0/64",
])
def test_gateway_prefix_uses_existing_cidr_conventions(prefix):
    assert_invalid({"defaultGateways": [{"prefix": prefix, "address": "192.0.2.1"}]},
                   "schema", "/spec/defaultGateways/0/prefix")


@pytest.mark.parametrize("entry", [{}, {"prefix": "192.0.2.0/24"}, {"address": "192.0.2.1"},
                                   {"prefix": "192.0.2.0/24", "address": "192.0.2.1", "metric": 100}])
def test_gateway_fields_required_and_closed(entry):
    assert_invalid({"defaultGateways": [entry]}, "schema", "/spec/defaultGateways/0")


@pytest.mark.parametrize("address", ["8.8.8.999", "resolver.example", "192.0.2.1/24", "fe80::1%eth0",
                                     "2001:db8::xyz", "8.8.8.8\n", "", None, 123])
@pytest.mark.parametrize("field", ["gateway", "dns"])
def test_network_configuration_requires_plain_ip_addresses(field, address):
    if field == "gateway":
        spec = {"defaultGateways": [{"prefix": "192.0.2.0/24", "address": address}]}
        path = "/spec/defaultGateways/0/address"
    else:
        spec = {"dns": {"servers": [address]}}
        path = "/spec/dns/servers/0"
    assert_invalid(spec, "schema", path)


@pytest.mark.parametrize("servers", [["8.8.8.8"], ["2001:4860:4860::8888"],
                                     ["2001:4860:4860::8888", "8.8.8.8", "192.0.2.53"], []])
def test_dns_valid_and_order_preserved_without_prefixes(servers):
    spec = {"dns": {"servers": servers}}
    model = InfrastructureModel.from_documents([network(spec)])
    assert not model.validate().warnings
    assert model.get("network/shared")["spec"] == spec


@pytest.mark.parametrize("servers", [["8.8.8.8", "8.8.8.8"], ["2001:db8::53", "2001:db8::53"]])
def test_dns_duplicate_server_rejected_by_schema(servers):
    assert_invalid({"dns": {"servers": servers}}, "schema", "/spec/dns/servers")


def test_dns_equivalent_ipv6_addresses_are_duplicates():
    assert_invalid({"dns": {"servers": ["2001:db8::53", "192.0.2.53", "2001:0DB8:0:0:0:0:0:53"]}},
                   "duplicate_dns_server", "/spec/dns/servers/2")


@pytest.mark.parametrize("spec,path", [
    ({"defaultGateways": None}, "/spec/defaultGateways"),
    ({"defaultGateways": {}}, "/spec/defaultGateways"),
    ({"dns": None}, "/spec/dns"), ({"dns": []}, "/spec/dns"), ({"dns": {}}, "/spec/dns"),
    ({"dns": {"servers": None}}, "/spec/dns/servers"),
    ({"dns": {"servers": "8.8.8.8"}}, "/spec/dns/servers"),
    ({"dns": {"servers": [], "searchDomains": ["example.org"]}}, "/spec/dns"),
    ({"routes": []}, "/spec"),
])
def test_network_configuration_shapes_are_closed(spec, path):
    assert_invalid(spec, "schema", path)


@pytest.mark.parametrize("spec", [
    {}, {"prefixes": ["192.0.2.0/24"]}, {"defaultGateways": []}, {"dns": {"servers": []}},
    {"defaultGateways": [], "dns": {"servers": []}},
    {"prefixes": ["192.0.2.0/24", "2001:DB8::/64"], "defaultGateways": [
        {"prefix": "192.0.2.0/24", "address": "192.0.2.1"},
        {"prefix": "2001:0db8:0:0::/64", "address": "fe80::1"}],
     "dns": {"servers": ["2001:4860:4860::8888", "8.8.8.8"]}},
])
def test_network_source_and_omitted_empty_distinction_preserved_in_all_views(spec):
    source = network(spec)
    before = deepcopy(source)
    model = InfrastructureModel.from_documents([source])
    report = model.validate()
    assert not report.warnings
    assert report.entity_count == 1  # Configuration stays Network attributes.
    views = [model.get("network/shared"), model.networks()[0], model.documents[0],
             model.topology.nodes("Network")[0]]
    for view in views:
        assert view["spec"] == spec
        view["spec"].clear()
    assert model.get("network/shared")["spec"] == spec
    assert not model.topology.edges()
    assert source == before


def test_network_configuration_does_not_change_attachments(documents):
    before = InfrastructureModel.from_documents(documents)
    after_docs = deepcopy(documents)
    for doc in after_docs:
        if doc["kind"] == "Network":
            doc["spec"].pop("defaultGateways", None)
            doc["spec"].pop("dns", None)
    after = InfrastructureModel.from_documents(after_docs)
    assert before.of_kind("NetworkAttachment") == after.of_kind("NetworkAttachment")
    assert before.validate().warnings == after.validate().warnings
    assert before.topology.edges() == after.topology.edges()


@pytest.mark.parametrize("field,value", [("defaultGateways", []), ("dns", {"servers": []})])
def test_network_configuration_not_accepted_on_attachments(documents, inventory, field, value):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    node["networkAttachments"][0][field] = value
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert any(issue.code == "schema" and issue.path.endswith("/networkAttachments/0")
               for issue in caught.value.issues)


def test_fabric_like_example_network_defaults():
    model = InfrastructureModel.load(ROOT / "examples/fabric-like")
    management = model.get("network/lab-management")["spec"]
    assert management["defaultGateways"] == [{"prefix": "192.0.2.0/24", "address": "192.0.2.1"}]
    assert management["dns"] == {"servers": ["192.0.2.53"]}
    storage = model.get("network/lab-storage")["spec"]
    assert storage["defaultGateways"] == [] and "dns" not in storage
