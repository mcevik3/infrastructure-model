from copy import deepcopy
from decimal import Inexact, Rounded, localcontext

import pytest

from infra_model import InfrastructureModel, ValidationError


@pytest.fixture
def placed(documents, inventory):
    node = inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]
    inventory["Cluster/openstack-lab"]["spec"]["nodes"] = [node]
    node["realization"] = {"type": "baremetal"}
    node["placement"] = {"resourceRef": "server/amst-w2", "siteRef": "site/AMST"}
    node["requirements"]["compute"]["cpu"] = {"count": 4, "unit": "core"}
    return documents, inventory, node


def test_compatible_exact_resource_and_profile_inheritance(placed):
    documents, inventory, node = placed
    report = InfrastructureModel.from_documents(documents).validate()
    assert not report.warnings


@pytest.mark.parametrize("group,field,value", [
    ("compute", "cpu", {"count": 65, "unit": "core"}),
    ("compute", "architecture", "aarch64"),
    ("compute", "cpu", {"count": 129, "unit": "thread"}),
    ("memory", "capacity", {"value": 513, "unit": "GiB"}),
    ("storage", "capacity", {"value": 3, "unit": "TB"}),
    ("storage", "medium", "hdd"),
    ("storage", "protocol", "sata"),
])
def test_incompatible_exact_resource(placed, group, field, value):
    documents, _, node = placed
    node["requirements"].setdefault(group, {})[field] = value
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert any(issue.code == "incompatible_resource" for issue in caught.value.issues)
    assert f"{group}.{field}" in str(caught.value)


def test_node_type_and_site_incompatibility(placed):
    documents, inventory, node = placed
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"nodeTypes": ["vm"]}
    node["placement"].pop("siteRef")  # Inherits lab-site from its Cluster.
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert len(caught.value.issues) == 2
    assert "requested site/lab-site" in str(caught.value)
    assert "requires baremetal" in str(caught.value)


def test_server_overrides_profile_leaves(placed):
    documents, inventory, node = placed
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"compute": {"cpu": {"cores": 2}}}
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()
    node["requirements"]["compute"]["cpu"]["count"] = 2
    model = InfrastructureModel.from_documents(documents)
    assert not model.validate().warnings
    assert model.effective_capabilities("server/amst-w2")["compute"] == {
        "architecture": "x86_64", "cpu": {"cores": 2, "threads": 128},
    }
    caps = model.effective_capabilities("server/amst-w2")
    caps["compute"]["cpu"]["cores"] = 999
    assert model.effective_capabilities("server/amst-w2")["compute"]["cpu"]["cores"] == 2


@pytest.mark.parametrize("available,required,valid", [
    ({"value": 1, "unit": "GiB"}, {"value": 1024, "unit": "MiB"}, True),
    ({"value": 1, "unit": "GB"}, {"value": 1, "unit": "GiB"}, False),
    ({"value": 1, "unit": "GiB"}, {"value": 1, "unit": "GB"}, True),
    ({"value": 1.5, "unit": "GiB"}, {"value": 1536, "unit": "MiB"}, True),
])
def test_explicit_decimal_binary_unit_conversion(placed, available, required, valid):
    documents, inventory, node = placed
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"memory": {"capacity": available}}
    node["requirements"]["memory"]["capacity"] = required
    model = InfrastructureModel.from_documents(documents)
    if valid:
        model.validate()
    else:
        with pytest.raises(ValidationError):
            model.validate()


def test_frequency_conversion(placed):
    documents, inventory, node = placed
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"compute": {"frequency": {"value": 2.4, "unit": "GHz"}}}
    node["requirements"]["compute"]["frequency"] = {"value": 2400, "unit": "MHz"}
    InfrastructureModel.from_documents(documents).validate()
    node["requirements"]["compute"]["frequency"]["value"] = 2401
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(documents).validate()


def test_unknown_capabilities_report_warnings(placed):
    documents, inventory, node = placed
    inventory["Server/amst-w2"]["spec"].pop("profileRef")
    model = InfrastructureModel.from_documents(documents)
    report = model.validate()
    assert len(report.warnings) == 5  # node type, architecture, cores, memory, storage
    assert {warning.code for warning in report.warnings} == {"unevaluated_requirement"}


def test_unplaced_intent_does_not_require_inventory(documents):
    docs = [doc for doc in documents if doc["kind"] in {"Cluster", "Network", "Site"}]
    report = InfrastructureModel.from_documents(docs).validate()
    assert len(report.warnings) == 2
    assert all("UNKNOWN" in warning.message for warning in report.warnings)


def test_per_node_check_does_not_allocate_or_sum_capacity(placed):
    documents, inventory, node = placed
    node["requirements"]["compute"]["cpu"]["count"] = 64
    second = deepcopy(node)
    second["name"] = "second-placement"
    second.pop("networkAttachments")
    inventory["Cluster/openstack-lab"]["spec"]["nodes"].append(second)
    assert not InfrastructureModel.from_documents(documents).validate().warnings


def test_bare_metal_placement(placed):
    documents, _, node = placed
    node["realization"] = {"type": "baremetal"}
    assert not InfrastructureModel.from_documents(documents).validate().warnings


@pytest.mark.parametrize("precision", [1, 3, 6, 28, 50])
@pytest.mark.parametrize("available,required,valid", [
    ({"value": 1073, "unit": "MB"}, {"value": 1, "unit": "GiB"}, False),
    ({"value": 10**29, "unit": "B"}, {"value": 10**29 + 1, "unit": "B"}, False),
    ({"value": 10**29 + 1, "unit": "B"}, {"value": 10**29, "unit": "B"}, True),
    ({"value": 0.1, "unit": "GB"}, {"value": 100, "unit": "MB"}, True),
    ({"value": 1.5, "unit": "GiB"}, {"value": 1536, "unit": "MiB"}, True),
])
def test_quantity_comparisons_ignore_decimal_context(placed, precision, available, required, valid):
    documents, inventory, node = placed
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"memory": {"capacity": available}}
    node["requirements"]["memory"]["capacity"] = required
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        if valid:
            assert not InfrastructureModel.from_documents(documents).validate().warnings
        else:
            with pytest.raises(ValidationError) as caught:
                InfrastructureModel.from_documents(documents).validate()
            assert {issue.code for issue in caught.value.issues} == {"incompatible_resource"}
        assert context.prec == precision
        assert not any(context.flags.values())


def test_integer_quantity_beyond_string_conversion_limit(placed):
    documents, inventory, node = placed
    value = 10**5000
    inventory["Server/amst-w2"]["spec"]["capabilities"] = {"memory": {"capacity": {"value": value, "unit": "B"}}}
    node["requirements"]["memory"]["capacity"] = {"value": value + 1, "unit": "B"}
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert caught.value.issues[0].code == "incompatible_resource"
    node["requirements"]["memory"]["capacity"]["value"] = value
    assert not InfrastructureModel.from_documents(documents).validate().warnings


@pytest.mark.parametrize("unit,count,valid", [("core", 64, True), ("core", 65, False), ("thread", 128, True), ("thread", 129, False)])
def test_physical_cpu_requirement_units(placed, unit, count, valid):
    documents, _, node = placed
    node["requirements"]["compute"]["cpu"] = {"count": count, "unit": unit}
    if valid:
        assert not InfrastructureModel.from_documents(documents).validate().warnings
    else:
        with pytest.raises(ValidationError, match="UNSATISFIED"):
            InfrastructureModel.from_documents(documents).validate()


@pytest.mark.parametrize("count", [1, 64, 1000])
def test_vcpu_is_unknown_regardless_of_physical_core_count(placed, count):
    documents, _, node = placed
    node["requirements"]["compute"]["cpu"] = {"count": count, "unit": "vcpu"}
    report = InfrastructureModel.from_documents(documents).validate()
    assert len(report.warnings) == 1
    assert report.warnings[0].code == "unevaluated_requirement"
    assert report.warnings[0].path.endswith("/requirements/compute/cpu")
    assert "UNKNOWN" in report.warnings[0].message


@pytest.mark.parametrize("field,required", [("medium", "ssd"), ("protocol", "nvme")])
def test_storage_fields_independently_unknown_when_absent(placed, field, required):
    documents, inventory, node = placed
    storage = inventory["HardwareProfile/dell-r7525"]["spec"]["capabilities"]["storage"]
    storage.pop(field)
    node["requirements"]["storage"][field] = required
    report = InfrastructureModel.from_documents(documents).validate()
    assert len(report.warnings) == 1
    assert report.warnings[0].path.endswith(f"/storage/{field}")
    assert "UNKNOWN" in report.warnings[0].message


def test_storage_medium_and_protocol_both_checked(placed):
    documents, _, node = placed
    node["requirements"]["storage"].update(medium="ssd", protocol="nvme")
    assert not InfrastructureModel.from_documents(documents).validate().warnings
    node["requirements"]["storage"].update(medium="hdd", protocol="sas")
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(documents).validate()
    assert {issue.path.rsplit("/", 1)[1] for issue in caught.value.issues} == {"medium", "protocol"}


def test_requirement_extensions_are_unknown(placed):
    documents, _, node = placed
    node["requirements"]["extensions"] = {"example.org/feature": True}
    node["requirements"]["compute"]["extensions"] = {"example.org/feature": True}
    report = InfrastructureModel.from_documents(documents).validate()
    assert len(report.warnings) == 2
    assert all("UNKNOWN" in warning.message for warning in report.warnings)


def test_compatibility_three_results():
    from infra_model.validator import _Compatibility, _compare, _compare_cpu

    assert _compare({"value": 1, "unit": "GiB"}, {"value": 1024, "unit": "MiB"}) is _Compatibility.SATISFIED
    assert _compare("ssd", "hdd") is _Compatibility.UNSATISFIED
    assert _compare("ssd", None) is _Compatibility.UNKNOWN
    assert _compare_cpu({"count": 4, "unit": "core"}, {"cpu": {"cores": 64}}, "vm") is _Compatibility.UNKNOWN


@pytest.mark.parametrize("unit,field", [("core", "cores"), ("thread", "threads")])
def test_missing_physical_cpu_dimension_is_unknown(placed, unit, field):
    documents, inventory, node = placed
    inventory["HardwareProfile/dell-r7525"]["spec"]["capabilities"]["compute"]["cpu"].pop(field)
    node["requirements"]["compute"]["cpu"] = {"count": 4, "unit": unit}
    report = InfrastructureModel.from_documents(documents).validate()
    assert len(report.warnings) == 1
    assert report.warnings[0].path.endswith("/compute/cpu")


def test_semantic_boundary_rejects_vm_resource_reference(documents, inventory):
    from infra_model.loader import SourceDocument
    from infra_model.registry import Registry
    from infra_model.validator import validate_registry

    inventory["Cluster/openstack-lab"]["spec"]["nodes"][0]["placement"] = {"resourceRef": "server/amst-w2"}
    registry = Registry([SourceDocument(document, "memory") for document in documents])
    with pytest.raises(ValidationError) as caught:
        validate_registry(registry)
    assert caught.value.issues[0].code == "invalid_placement"
    assert caught.value.issues[0].path.endswith("/placement/resourceRef")
