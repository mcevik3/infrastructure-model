from copy import deepcopy

import pytest

from infra_model import InfrastructureModel, ValidationError
from test_network_scope import assert_issue, document, model_documents


INTENT = {"layer": "layer3", "addressFamily": "ipv4",
          "addressAllocation": "provider-managed", "reachability": "provider-internal"}


@pytest.mark.parametrize("spec", [
    {}, {"layer": "layer2"}, {"layer": "layer3"},
    {"layer": "layer3", "addressFamily": "ipv4"},
    {"layer": "layer3", "addressFamily": "ipv6"},
    {"layer": "layer3", "reachability": "provider-internal"},
    {"layer": "layer3", "reachability": "public"},
    {"layer": "layer3", "addressFamily": "ipv4", "addressAllocation": "provider-managed"},
    INTENT, {**INTENT, "addressFamily": "ipv6"}, {**INTENT, "reachability": "public"},
])
def test_optional_intent_preserved_without_defaults(spec):
    documents = model_documents(spec)
    before = deepcopy(documents)
    model = InfrastructureModel.from_documents(documents)
    assert not model.validate().warnings
    for view in [model.get("network/shared"), model.networks()[0], model.documents[3],
                 model.topology.nodes("Network")[0]]:
        assert view["spec"] == spec
    assert documents == before


@pytest.mark.parametrize("field,value", [
    ("addressFamily", "ipv4"), ("addressAllocation", "provider-managed"),
    ("reachability", "provider-internal"), ("reachability", "public"),
])
@pytest.mark.parametrize("layer", [None, "layer2"])
def test_new_fields_require_explicit_layer3(field, value, layer):
    spec = {field: value}
    if layer is not None:
        spec["layer"] = layer
    assert_issue(model_documents(spec), "network_layer3_required", f"/spec/{field}")


@pytest.mark.parametrize("field,values", [
    ("addressFamily", ["IPv4", "FABNetv4", "FABNetv6", "ipv4Ext", "ipv6Ext", "dual-stack", None]),
    ("addressAllocation", ["static", "dhcp", "FABNetv4", None]),
    ("reachability", ["internal", "private", "IPv4Ext", "IPv6Ext", None]),
])
def test_generic_enums_are_closed(field, values):
    for value in values:
        assert_issue(model_documents({"layer": "layer3", field: value}), "schema", f"/spec/{field}")


@pytest.mark.parametrize("family,prefix,address", [
    ("ipv4", "192.0.2.0/24", "192.0.2.10"),
    ("ipv6", "2001:db8::/64", "2001:db8::10"),
])
def test_authored_family_consistency(family, prefix, address):
    documents = model_documents({"layer": "layer3", "addressFamily": family, "prefixes": [prefix]}, [None])
    documents[-1]["spec"]["nodes"][0]["networkAttachments"][0]["addresses"] = [address]
    InfrastructureModel.from_documents(documents).validate()
    documents[3]["spec"]["addressFamily"] = "ipv6" if family == "ipv4" else "ipv4"
    issues = assert_issue(documents, "network_address_family", "/spec/prefixes/0")
    assert any(issue.code == "network_address_family" and
               issue.path == "/spec/nodes/0/networkAttachments/0/addresses/0" for issue in issues)


def test_attachment_family_mismatch_is_diagnosed_independently():
    documents = model_documents({"layer": "layer3", "addressFamily": "ipv4", "prefixes": ["192.0.2.0/24"]}, [None])
    documents[-1]["spec"]["nodes"][0]["networkAttachments"][0]["addresses"] = ["2001:db8::1"]
    assert_issue(documents, "network_address_family", "/spec/nodes/0/networkAttachments/0/addresses/0")


@pytest.mark.parametrize("layer", [None, "layer2", "layer3"])
def test_omitted_family_preserves_existing_dual_stack(layer):
    spec = {"prefixes": ["192.0.2.0/24", "2001:db8::/64"]}
    if layer is not None:
        spec["layer"] = layer
    documents = model_documents(spec, [None])
    documents[-1]["spec"]["nodes"][0]["networkAttachments"][0]["addresses"] = ["192.0.2.10", "2001:db8::10"]
    model = InfrastructureModel.from_documents(documents)
    model.validate()
    assert model.get("network/shared")["spec"] == spec


def test_provider_managed_requires_family():
    assert_issue(model_documents({"layer": "layer3", "addressAllocation": "provider-managed"}),
                 "network_address_family_required", "/spec/addressAllocation")


@pytest.mark.parametrize("prefixes", [[], ["192.0.2.0/24"]])
def test_provider_managed_rejects_authored_prefixes(prefixes):
    assert_issue(model_documents({**INTENT, "prefixes": prefixes}),
                 "provider_managed_addressing", "/spec/prefixes")


@pytest.mark.parametrize("addresses", [[], ["192.0.2.10"]])
def test_provider_managed_rejects_authored_attachment_addresses(addresses):
    documents = model_documents(INTENT, [None])
    documents[-1]["spec"]["nodes"][0]["networkAttachments"][0]["addresses"] = addresses
    assert_issue(documents, "provider_managed_addressing", "/spec/nodes/0/networkAttachments/0/addresses")


@pytest.mark.parametrize("servers", [[], ["192.0.2.53", "2001:db8::53"]])
def test_provider_managed_allows_independent_empty_gateways_and_dns(servers):
    spec = {**INTENT, "defaultGateways": [], "dns": {"servers": servers}}
    model = InfrastructureModel.from_documents(model_documents(spec))
    assert not model.validate().warnings
    assert model.get("network/shared")["spec"] == spec


def test_provider_managed_retains_gateway_and_dns_consistency_checks():
    assert_issue(model_documents({**INTENT, "defaultGateways": [
        {"prefix": "192.0.2.0/24", "address": "192.0.2.1"}]}),
        "gateway_prefix_not_declared", "/spec/defaultGateways/0/prefix")
    assert_issue(model_documents({**INTENT, "dns": {"servers": ["2001:db8::53", "2001:0db8::53"]}}),
                 "duplicate_dns_server", "/spec/dns/servers/1")


def test_no_provider_topology_interface_or_realization_constraints():
    spec = {**INTENT, "siteRefs": ["site/site-b", "site/site-a"]}
    documents = model_documents(spec, ["site/site-a", "site/site-b", "site/site-a"])
    nodes = documents[-1]["spec"]["nodes"]
    nodes[0]["networkAttachments"].append({"name": "second", "networkRef": "network/shared",
                                         "interfaceRequirements": {"type": "ethernet", "adapter": {"class": "smartnic"}}})
    nodes[1]["realization"]["type"] = "baremetal"
    other = document("Cluster", "other", {"nodes": [deepcopy(nodes[2])]})
    documents.append(other)
    model = InfrastructureModel.from_documents(documents)
    model.validate()
    assert len(model.of_kind("NetworkAttachment")) == 5
    assert model.get("network/shared")["spec"]["siteRefs"] == spec["siteRefs"]
    assert model.topology.neighbors("network/shared", "scoped_to") == ["site/site-a", "site/site-b"]


def test_layer3_scope_and_point_to_point_rules_still_apply():
    assert_issue(model_documents({**INTENT, "siteRefs": ["site/site-a"]}, ["site/site-b"]),
                 "network_site_scope", "/spec/nodes/0/networkAttachments/0/networkRef")
    documents = model_documents({**INTENT, "connectivity": "point-to-point"}, [None, None])
    InfrastructureModel.from_documents(documents).validate()
    documents[-1]["spec"]["nodes"].append({"name": "third", "realization": {"type": "vm"},
                                         "networkAttachments": [{"name": "data", "networkRef": "network/shared"}]})
    assert_issue(documents, "network_endpoint_cardinality", "/spec/connectivity")
