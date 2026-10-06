from copy import deepcopy

import pytest

from infra_model import InfrastructureModel, ValidationError
from infra_model.loader import load_documents
from conftest import ROOT


SERVER = 'server/server01'
NODE = 'cluster/baremetal-cluster/node/storage-1'
STORAGE = NODE + '/storage'
TABLE = STORAGE + '/partition-table/os-layout'


@pytest.fixture
def storage_inventory():
    docs = [doc.data for doc in load_documents(ROOT / 'examples/baremetal')]
    server = next(doc for doc in docs if doc['kind'] == 'Server')
    cluster = next(doc for doc in docs if doc['kind'] == 'Cluster')
    node = cluster['spec']['nodes'][0]
    return docs, server, node


def error(docs, code):
    with pytest.raises(ValidationError) as caught:
        InfrastructureModel.from_documents(docs).validate()
    assert code in {issue.code for issue in caught.value.issues}, str(caught.value)
    assert all(issue.source and issue.path for issue in caught.value.issues)
    return caught.value


def test_complete_storage_topology(storage_inventory):
    docs, _, _ = storage_inventory
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    assert len(model.of_kind('StorageController')) == 1
    assert len(model.of_kind('StorageDevice')) == 7
    assert len(model.of_kind('StorageVolume')) == 2
    assert model.get(SERVER + '/storage-volume/os')['controllerRef'] == SERVER + '/storage-controller/controller-1'


@pytest.mark.parametrize('collection,field,value', [
    ('devices', 'controllerRef', 'missing'),
    ('volumes', 'controllerRef', 'missing'),
    ('volumes', 'deviceRefs', ['missing']),
])
def test_bad_hardware_refs(storage_inventory, collection, field, value):
    docs, server, _ = storage_inventory
    server['spec']['storage'][collection][0][field] = value
    error(docs, 'unresolved_reference')


@pytest.mark.parametrize('refs', [['disk0', 'disk0'], ['disk0', SERVER + '/storage-device/disk0']])
def test_duplicate_volume_device_refs_after_normalization(storage_inventory, refs):
    docs, server, _ = storage_inventory
    server['spec']['storage']['volumes'][0]['deviceRefs'] = refs
    error(docs, 'duplicate_reference')


def test_conflicting_controller(storage_inventory):
    docs, server, _ = storage_inventory
    server['spec']['storage']['controllers'].append({'name': 'other', 'mode': 'hba'})
    server['spec']['storage']['devices'][0]['controllerRef'] = 'other'
    error(docs, 'storage_controller_conflict')


def test_unknown_member_controller_warns(storage_inventory):
    docs, server, _ = storage_inventory
    server['spec']['storage']['devices'][0].pop('controllerRef')
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert len(warnings) == 1 and 'UNKNOWN' in warnings[0].message


@pytest.mark.parametrize('collection', ['controllers', 'devices', 'volumes'])
def test_duplicate_hardware_names(storage_inventory, collection):
    docs, server, _ = storage_inventory
    items = server['spec']['storage'][collection]
    items.append(deepcopy(items[0]))
    error(docs, 'duplicate_component_name')


@pytest.mark.parametrize('mode', ['raid', 'hba', 'jbod', 'other'])
def test_controller_modes(storage_inventory, mode):
    docs, server, _ = storage_inventory
    server['spec']['storage']['controllers'][0]['mode'] = mode
    # Mode and RAID level are different descriptive dimensions; no firmware policy.
    InfrastructureModel.from_documents(docs).validate()


@pytest.mark.parametrize('level', ['raid0', 'raid1', 'raid5', 'raid6', 'raid10', 'raid50', 'raid60', 'other'])
def test_raid_levels_without_parity_math(storage_inventory, level):
    docs, server, _ = storage_inventory
    server['spec']['storage']['volumes'][0]['raid']['level'] = level
    InfrastructureModel.from_documents(docs).validate()


def test_optional_hardware_identity_and_pci_scope(storage_inventory):
    docs, server, _ = storage_inventory
    device = server['spec']['storage']['devices'][0]
    device.update(vendor='Example', model='SAS SSD', serial='serial-01')
    InfrastructureModel.from_documents(docs).validate()
    device['pciAddress'] = '41:00.0'
    error(docs, 'duplicate_pci')


@pytest.mark.parametrize('kind', ['gpt', 'mbr'])
def test_partition_table_types_and_optional_numbers(storage_inventory, kind):
    docs, _, node = storage_inventory
    table = node['configuration']['storage']['partitionTables'][0]
    table['type'] = kind
    for partition in table['partitions']:
        partition.pop('number')
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    partitions = model.get(TABLE)['partitions']
    assert [p['name'] for p in partitions] == ['efi', 'boot', 'system']
    assert all('number' not in p for p in partitions)


@pytest.mark.parametrize('source', [SERVER + '/storage-volume/missing', SERVER + '/storage-device/missing'])
def test_bad_partition_source(storage_inventory, source):
    docs, _, node = storage_inventory
    node['configuration']['storage']['partitionTables'][0]['sourceRef'] = source
    error(docs, 'unresolved_reference')


def test_partition_on_raw_device(storage_inventory):
    docs, _, node = storage_inventory
    node['configuration']['storage']['partitionTables'][0]['sourceRef'] = SERVER + '/storage-device/nvme0'
    node['configuration']['storage'].pop('lvm')
    assert not InfrastructureModel.from_documents(docs).validate().warnings


@pytest.mark.parametrize('mutation,code', [
    (lambda ps: ps[1].update(name='efi'), 'duplicate_component_name'),
    (lambda ps: ps[1].update(number=1), 'duplicate_partition_number'),
    (lambda ps: ps[0].update(grow=True), 'schema'),
    (lambda ps: ps[0].pop('size'), 'schema'),
    (lambda ps: ps.append({'name': 'extra', 'type': 'linux-lvm', 'grow': True}), 'multiple_grow'),
    (lambda ps: ps[2].update(grow=False), 'schema'),
    (lambda ps: ps[0].update(number=0), 'schema'),
    (lambda ps: ps[0].update(typeId='0x83'), 'schema'),
])
def test_partition_errors(storage_inventory, mutation, code):
    docs, _, node = storage_inventory
    mutation(node['configuration']['storage']['partitionTables'][0]['partitions'])
    error(docs, code)


def test_fixed_partition_with_false_grow_and_exact_other_type(storage_inventory):
    docs, _, node = storage_inventory
    partition = node['configuration']['storage']['partitionTables'][0]['partitions'][0]
    partition.update(type='other', typeId='0x83', grow=False)
    InfrastructureModel.from_documents(docs).validate()


def test_duplicate_partition_table_names(storage_inventory):
    docs, _, node = storage_inventory
    tables = node['configuration']['storage']['partitionTables']
    tables.append(deepcopy(tables[0]))
    error(docs, 'duplicate_component_name')


def test_lvm_all_source_types_and_local_references(storage_inventory):
    docs, _, _ = storage_inventory
    before = deepcopy(docs)
    model = InfrastructureModel.from_documents(docs)
    assert not model.validate().warnings
    assert model.get(STORAGE + '/lvm/pv/pv-system')['sourceRef'] == TABLE + '/partition/system'
    assert model.get(STORAGE + '/lvm/pv/pv-data')['sourceRef'] == SERVER + '/storage-volume/data'
    assert model.get(STORAGE + '/lvm/pv/pv-scratch')['sourceRef'] == SERVER + '/storage-device/nvme0'
    assert model.get(STORAGE + '/lvm/vg/vg_system')['physicalVolumeRefs'] == [STORAGE + '/lvm/pv/pv-system']
    assert model.get(STORAGE + '/lvm/lv/lv_root')['volumeGroupRef'] == STORAGE + '/lvm/vg/vg_system'
    assert docs == before
    # Normalization is visible consistently in all dictionary/graph views.
    assert model.get(NODE)['configuration']['storage']['lvm']['physicalVolumes'][0] == model.get(STORAGE + '/lvm/pv/pv-system')
    assert next(x for x in model.topology.nodes('PhysicalVolume') if x['name'] == 'pv-system')['sourceRef'] == TABLE + '/partition/system'


@pytest.mark.parametrize('source', [
    'partition-table/missing/partition/system',
    'partition-table/os-layout/partition/missing',
    SERVER + '/storage-device/missing', SERVER + '/storage-volume/missing',
])
def test_bad_pv_source(storage_inventory, source):
    docs, _, node = storage_inventory
    node['configuration']['storage']['lvm']['physicalVolumes'][0]['sourceRef'] = source
    error(docs, 'unresolved_reference')


@pytest.mark.parametrize('canonical', [False, True])
def test_duplicate_pv_source_after_normalization(storage_inventory, canonical):
    docs, _, node = storage_inventory
    pvs = node['configuration']['storage']['lvm']['physicalVolumes']
    pvs[1]['sourceRef'] = TABLE + '/partition/system' if canonical else pvs[0]['sourceRef']
    error(docs, 'duplicate_pv_source')


@pytest.mark.parametrize('collection', ['physicalVolumes', 'volumeGroups', 'logicalVolumes'])
def test_duplicate_lvm_names(storage_inventory, collection):
    docs, _, node = storage_inventory
    values = node['configuration']['storage']['lvm'][collection]
    values.append(deepcopy(values[0]))
    error(docs, 'duplicate_component_name')


@pytest.mark.parametrize('mutation,code', [
    (lambda lvm: lvm['volumeGroups'][0].update(physicalVolumeRefs=['missing']), 'unresolved_reference'),
    (lambda lvm: lvm['volumeGroups'][0].update(physicalVolumeRefs=['pv-system', 'pv-system']), 'duplicate_reference'),
    (lambda lvm: lvm['volumeGroups'][0].update(physicalVolumeRefs=['pv-system', STORAGE + '/lvm/pv/pv-system']), 'duplicate_reference'),
    (lambda lvm: lvm['volumeGroups'][1].update(physicalVolumeRefs=['pv-system']), 'pv_multiple_vgs'),
    (lambda lvm: lvm['logicalVolumes'][0].update(volumeGroupRef='missing'), 'unresolved_reference'),
    (lambda lvm: lvm['logicalVolumes'][0].update(grow=True), 'schema'),
    (lambda lvm: lvm['logicalVolumes'][0].pop('capacity'), 'schema'),
    (lambda lvm: lvm['logicalVolumes'][2].update(grow=False), 'schema'),
    (lambda lvm: lvm['logicalVolumes'][3].update(volumeGroupRef='vg_system'), 'multiple_grow'),
])
def test_lvm_errors(storage_inventory, mutation, code):
    docs, _, node = storage_inventory
    mutation(node['configuration']['storage']['lvm'])
    error(docs, code)


def test_fixed_lv_false_grow_and_separate_growing_vgs(storage_inventory):
    docs, _, node = storage_inventory
    node['configuration']['storage']['lvm']['logicalVolumes'][0]['grow'] = False
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def test_full_local_scope_references_are_also_accepted(storage_inventory):
    docs, server, node = storage_inventory
    for volume in server['spec']['storage']['volumes']:
        volume['controllerRef'] = SERVER + '/storage-controller/controller-1'
        volume['deviceRefs'] = [SERVER + '/storage-device/' + ref for ref in volume['deviceRefs']]
    lvm = node['configuration']['storage']['lvm']
    lvm['physicalVolumes'][0]['sourceRef'] = TABLE + '/partition/system'
    for vg in lvm['volumeGroups']:
        vg['physicalVolumeRefs'] = [STORAGE + '/lvm/pv/' + ref for ref in vg['physicalVolumeRefs']]
    for lv in lvm['logicalVolumes']:
        lv['volumeGroupRef'] = STORAGE + '/lvm/vg/' + lv['volumeGroupRef']
    assert not InfrastructureModel.from_documents(docs).validate().warnings


def other_server(docs, server):
    other = deepcopy(server)
    other['metadata']['name'] = 'other'
    docs.append(other)
    return other


@pytest.mark.parametrize('consumer', ['table', 'pv'])
@pytest.mark.parametrize('kind,name', [('storage-volume', 'data'), ('storage-device', 'nvme0')])
def test_block_source_must_belong_to_exact_server(storage_inventory, consumer, kind, name):
    docs, server, node = storage_inventory
    other_server(docs, server)
    storage = node['configuration']['storage']
    entry = storage['partitionTables'][0] if consumer == 'table' else storage['lvm']['physicalVolumes'][0]
    entry['sourceRef'] = f'server/other/{kind}/{name}'
    error(docs, 'storage_placement')


@pytest.mark.parametrize('field', ['controllerRef', 'deviceRefs'])
def test_hardware_refs_cannot_cross_server_scope(storage_inventory, field):
    docs, server, _ = storage_inventory
    other_server(docs, server)
    volume = server['spec']['storage']['volumes'][0]
    volume[field] = 'server/other/storage-controller/controller-1' if field == 'controllerRef' else ['server/other/storage-device/disk0']
    error(docs, 'reference_scope')


@pytest.mark.parametrize('field', ['sourceRef', 'physicalVolumeRefs', 'volumeGroupRef'])
def test_lvm_refs_cannot_cross_node_scope(storage_inventory, field):
    docs, _, node = storage_inventory
    cluster = next(doc for doc in docs if doc['kind'] == 'Cluster')
    other = deepcopy(node)
    other['name'] = 'other'
    cluster['spec']['nodes'].append(other)
    lvm = node['configuration']['storage']['lvm']
    prefix = NODE.replace('/storage-1', '/other') + '/storage/'
    if field == 'sourceRef':
        lvm['physicalVolumes'][0][field] = prefix + 'partition-table/os-layout/partition/system'
    elif field == 'physicalVolumeRefs':
        lvm['volumeGroups'][0][field] = [prefix + 'lvm/pv/pv-system']
    else:
        lvm['logicalVolumes'][0][field] = prefix + 'lvm/vg/vg_system'
    error(docs, 'reference_scope')


def test_no_exact_placement_warns_without_inventing_storage_availability(storage_inventory):
    docs, _, node = storage_inventory
    node.pop('placement')
    warnings = InfrastructureModel.from_documents(docs).validate().warnings
    assert warnings and all('UNKNOWN' in issue.message for issue in warnings)
    assert any(issue.path.endswith('/sourceRef') for issue in warnings)


def test_vm_cannot_configure_physical_storage(storage_inventory):
    docs, _, node = storage_inventory
    node['realization']['type'] = 'vm'
    node.pop('placement')
    error(docs, 'invalid_storage_configuration')


@pytest.mark.parametrize('source', ['pv-system', SERVER + '/storage-controller/controller-1', 'partition-table/os-layout', TABLE + '/partition/system\n'])
def test_reference_syntax_does_not_guess_or_accept_wrong_block_kinds(storage_inventory, source):
    docs, _, node = storage_inventory
    node['configuration']['storage']['lvm']['physicalVolumes'][0]['sourceRef'] = source
    error(docs, 'schema')


def test_graph_storage_identities_and_semantic_relationships(storage_inventory):
    docs, _, _ = storage_inventory
    model = InfrastructureModel.from_documents(reversed(docs))
    topology = model.topology
    controller = SERVER + '/storage-controller/controller-1'
    disk = SERVER + '/storage-device/disk0'
    volume = SERVER + '/storage-volume/os'
    partition = TABLE + '/partition/system'
    pv = STORAGE + '/lvm/pv/pv-system'
    vg = STORAGE + '/lvm/vg/vg_system'
    lv = STORAGE + '/lvm/lv/lv_root'
    expected = [
        (SERVER, controller, 'has_storage_controller'),
        (SERVER, disk, 'has_storage_device'),
        (SERVER, volume, 'has_storage_volume'),
        (controller, disk, 'controls'),
        (controller, volume, 'provides'),
        (volume, disk, 'uses_device'),
        (NODE, TABLE, 'configures'),
        (TABLE, volume, 'uses_block_device'),
        (TABLE, partition, 'has_partition'),
        (pv, partition, 'uses_block_device'),
        (vg, pv, 'uses_pv'),
        (lv, vg, 'allocated_from'),
        (NODE, pv, 'configures'), (NODE, vg, 'configures'), (NODE, lv, 'configures'),
        (STORAGE + '/lvm/pv/pv-data', SERVER + '/storage-volume/data', 'uses_block_device'),
        (STORAGE + '/lvm/pv/pv-scratch', SERVER + '/storage-device/nvme0', 'uses_block_device'),
    ]
    actual = {(edge['source'], edge['target'], edge['relation']) for edge in topology.edges()}
    assert set(expected) <= actual
    assert len(topology) == 30
    assert all(edge['source'] in topology and edge['target'] in topology for edge in topology.edges())
    assert not hasattr(topology, 'graph')
    assert topology.neighbors(partition, 'has_partition', 'in') == [TABLE]


@pytest.mark.parametrize('location,field,value', [
    ('storage', 'filesystems', []), ('storage', 'mountPoints', []),
    ('partition', 'filesystem', 'xfs'), ('partition', 'mountPoint', '/'),
    ('lv', 'filesystem', 'xfs'), ('lv', 'mountPoint', '/var'),
    ('lvm', 'thinPools', []), ('table', 'type', 'none'),
    ('partition', 'startSector', 2048), ('controller', 'type', 'raid'),
    ('controller', 'cachePolicy', 'writeback'), ('volume', 'stripeSize', 64),
    ('device', 'medium', 'mixed'),
])
def test_out_of_scope_fields_stay_out_of_core(storage_inventory, location, field, value):
    docs, server, node = storage_inventory
    storage = node['configuration']['storage']
    containers = {'storage': storage, 'table': storage['partitionTables'][0],
                  'partition': storage['partitionTables'][0]['partitions'][0],
                  'lvm': storage['lvm'], 'lv': storage['lvm']['logicalVolumes'][0],
                  'controller': server['spec']['storage']['controllers'][0],
                  'volume': server['spec']['storage']['volumes'][0],
                  'device': server['spec']['storage']['devices'][0]}
    containers[location][field] = value
    error(docs, 'schema')


def test_removed_alpha_storage_shape_rejected(storage_inventory):
    docs, server, _ = storage_inventory
    server['spec']['storageDevices'] = server['spec']['storage']['devices']
    error(docs, 'schema')


def test_storage_extensions_and_status_remain_opaque(storage_inventory):
    docs, _, node = storage_inventory
    table = node['configuration']['storage']['partitionTables'][0]
    table['extensions'] = {'example.org/policy': {'sourceRef': 'opaque'}}
    table['status'] = {'extensions': {'example.org/observations': {'sourceRef': 'opaque'}}}
    normalized = InfrastructureModel.from_documents(docs).get(TABLE)
    assert normalized['extensions'] == table['extensions']
    assert normalized['status'] == table['status']
