from copy import deepcopy

import pytest

from infra_model import InfrastructureModel, ValidationError
from infra_model.loader import load_documents
from conftest import ROOT


@pytest.fixture
def cpu_inventory():
    docs = [doc.data for doc in load_documents(ROOT / 'examples/baremetal')
            if doc.data['kind'] != 'Cluster']
    profile = next(doc for doc in docs if doc['kind'] == 'HardwareProfile')
    server = next(doc for doc in docs if doc['kind'] == 'Server')
    return docs, server['spec']['capabilities']['compute']['cpu'], profile['spec']['capabilities']['compute']['cpu']


def error(docs, code, path):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert any(issue.code == code and issue.path.endswith(path) for issue in caught.value.issues), str(caught.value)
    assert all(issue.source for issue in caught.value.issues)


def test_valid_aggregate_topology(cpu_inventory):
    docs, cpu, _ = cpu_inventory
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    actual = model.effective_capabilities('server/server01')['compute']['cpu']
    assert actual['sockets'] == 2 and actual['cores'] == 128 and actual['threads'] == 256
    assert actual['topology'] == {'coresPerSocket': 64, 'threadsPerCore': 2}


@pytest.mark.parametrize('field,value', [('coresPerSocket', 63), ('threadsPerCore', 1)])
def test_aggregate_topology_mismatch(cpu_inventory, field, value):
    docs, cpu, _ = cpu_inventory
    cpu['topology'][field] = value
    error(docs, 'cpu_topology', '/topology/' + field)


@pytest.mark.parametrize('supported,enabled', [(True, True), (True, False), (False, False)])
def test_smt_combinations_and_hardware_threads(cpu_inventory, supported, enabled):
    docs, cpu, _ = cpu_inventory
    cpu['smt'] = {'supported': supported, 'enabled': enabled}
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    assert model.effective_capabilities('server/server01')['compute']['cpu']['threads'] == 256


def test_smt_enabled_when_unsupported(cpu_inventory):
    docs, cpu, _ = cpu_inventory
    cpu['smt'] = {'supported': False, 'enabled': True}
    error(docs, 'incompatible_resource', '/smt/enabled')


def test_smt_enabled_is_never_inferred(cpu_inventory):
    docs, cpu, _ = cpu_inventory
    del cpu['smt']['enabled']
    result = InfrastructureModel.from_documents(docs).effective_capabilities('server/server01')
    assert 'enabled' not in result['compute']['cpu']['smt']


def test_smt_support_unknown(cpu_inventory):
    docs, cpu, profile = cpu_inventory
    cpu['smt'].pop('supported')
    profile['smt'].pop('supported')
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 1 and 'UNKNOWN' in warnings[0].message
    assert warnings[0].path.endswith('/smt/enabled')


@pytest.mark.parametrize('count', [1, 2, 4])
def test_concise_numa_nodes_per_socket(cpu_inventory, count):
    docs, cpu, _ = cpu_inventory
    cpu['numa'] = {'mode': 'numa', 'nodesPerSocket': count}
    assert not InfrastructureModel.from_documents(docs).validate().warnings


@pytest.mark.parametrize('mutation,code,path', [
    (lambda cpu: cpu['numa']['nodes'][1].update(id=0), 'duplicate_numa_id', '/nodes/1/id'),
    (lambda cpu: cpu['numa']['nodes'][1].update(socket=2), 'numa_socket', '/nodes/1/socket'),
    (lambda cpu: cpu['numa']['nodes'].pop(), 'numa_node_count', '/numa/nodes'),
    (lambda cpu: cpu['numa']['nodes'][0].update(cores=15), 'numa_core_count', '/numa/nodes'),
])
def test_detailed_numa_errors(cpu_inventory, mutation, code, path):
    docs, cpu, _ = cpu_inventory
    mutation(cpu)
    error(docs, code, path)


def test_unknown_node_cores_do_not_prove_sum(cpu_inventory):
    docs, cpu, _ = cpu_inventory
    cpu['numa']['nodes'][0].pop('cores')
    cpu['numa']['nodes'][1]['cores'] = 1
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_numa_memory_totals_not_enforced(cpu_inventory):
    docs, cpu, _ = cpu_inventory
    cpu['numa']['nodes'][0]['memory']['capacity']['value'] = 1
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_unknown_socket_count_warns(cpu_inventory):
    docs, cpu, _ = cpu_inventory
    cpu.pop('sockets')
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 8
    assert all(w.path.endswith('/socket') and 'UNKNOWN' in w.message for w in warnings)


def test_interleaved_without_nodes(cpu_inventory):
    docs, cpu, _ = cpu_inventory
    cpu['numa'] = {'mode': 'interleaved'}
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_omitted_numa_is_unknown_not_materialized(cpu_inventory):
    docs, cpu, profile = cpu_inventory
    cpu.pop('numa')
    profile.pop('numa')
    result = InfrastructureModel.from_documents(docs).effective_capabilities('server/server01')
    assert 'numa' not in result['compute']['cpu']


@pytest.mark.parametrize('field,value', [('sockets', 0), ('cores', 1.5), ('threads', True), ('cores', {'value': 128, 'unit': 'core'})])
def test_cpu_counts_schema(cpu_inventory, field, value):
    docs, cpu, _ = cpu_inventory
    cpu[field] = value
    error(docs, 'schema', '/' + field)


@pytest.mark.parametrize('parent,field,value', [
    ('topology', 'threadsPerCore', 0), ('topology', 'coresPerSocket', 0),
    ('numa', 'nodesPerSocket', 0), ('numa', 'mode', 'NPS4'),
    ('numa', 'supportedNodesPerSocket', [1, 1]),
    ('numa', 'supportedNodesPerSocket', [0]),
    ('smt', 'enabled', 'yes'), ('smt', 'hyperThreading', True),
])
def test_topology_schema(cpu_inventory, parent, field, value):
    docs, cpu, _ = cpu_inventory
    cpu[parent][field] = value
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(docs).validate()


@pytest.mark.parametrize('field,value', [('smt', {'enabled': True}), ('numa', {'mode': 'numa'}), ('numa', {'nodesPerSocket': 4}), ('numa', {'nodes': [{'id': 0, 'socket': 0}]})])
def test_profile_cannot_claim_server_state(cpu_inventory, field, value):
    docs, _, profile = cpu_inventory
    profile[field] = value
    with pytest.raises(ValidationError):
        InfrastructureModel.from_documents(docs).validate()


def test_effective_profile_defaults_recursive_merge_and_list_replacement(cpu_inventory):
    docs, cpu, profile = cpu_inventory
    profile.update(sockets=2, cores=128, threads=256, topology=deepcopy(cpu.pop('topology')))
    for field in ['sockets', 'cores', 'threads']:
        cpu.pop(field)
    cpu['smt'].pop('supported')
    cpu['numa']['supportedNodesPerSocket'] = [4]
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    effective = model.effective_capabilities('server/server01')['compute']['cpu']
    assert effective['topology']['coresPerSocket'] == 64
    assert effective['smt'] == {'supported': True, 'enabled': True}
    assert effective['numa']['supportedNodesPerSocket'] == [4]
    assert 'sockets' not in model.get('server/server01')['spec']['capabilities']['compute']['cpu']


def test_inherited_topology_consistency_checked(cpu_inventory):
    docs, cpu, profile = cpu_inventory
    profile['topology'] = cpu.pop('topology')
    cpu['cores'] = 127
    error(docs, 'cpu_topology', '/topology/coresPerSocket')


def test_profile_topology_validated_even_unused(cpu_inventory):
    docs, _, profile = cpu_inventory
    profile.update(sockets=2, cores=10, topology={'coresPerSocket': 4})
    docs = [doc for doc in docs if doc['kind'] == 'HardwareProfile']
    error(docs, 'cpu_topology', '/topology/coresPerSocket')


def test_profile_smt_contradiction_not_hidden_by_override(cpu_inventory):
    docs, _, profile = cpu_inventory
    profile['smt']['supported'] = False
    error(docs, 'incompatible_resource', '/smt/supported')


@pytest.mark.parametrize('override', [False, True])
def test_profile_numa_contradiction_not_hidden_by_override(cpu_inventory, override):
    docs, cpu, profile = cpu_inventory
    profile['numa']['supportedNodesPerSocket'] = [1, 2]
    if override:
        cpu['numa']['supportedNodesPerSocket'] = [4]
    error(docs, 'incompatible_resource', '/numa/nodesPerSocket')


def test_numa_support_unknown_warns(cpu_inventory):
    docs, _, profile = cpu_inventory
    profile.pop('numa')
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 1 and warnings[0].path.endswith('/numa/nodesPerSocket')


def test_disabled_smt_does_not_reduce_hardware_thread_capability():
    docs = [doc.data for doc in load_documents(ROOT / 'examples/baremetal')]
    server = next(doc for doc in docs if doc['kind'] == 'Server')
    node = next(doc for doc in docs if doc['kind'] == 'Cluster')['spec']['nodes'][0]
    server['spec']['capabilities']['compute']['cpu']['smt']['enabled'] = False
    node['requirements']['compute']['cpu'] = {'count': 256, 'unit': 'thread'}
    assert not InfrastructureModel.from_documents(docs).validate().warnings
