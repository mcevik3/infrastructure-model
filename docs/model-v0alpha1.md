# Provider-neutral infrastructure model: v0alpha1

This document specifies the first reference implementation. See
[decisions.md](decisions.md) for provisional details that need review against any
earlier design. The schema API version is `v0alpha1` (`apiVersion: infra.model/v0alpha1`).
The Python distribution version is `0.1.0a1`; package releases and schema API
versions are separate version domains. The `infra.model` API namespace remains
provisional, not a permanent organizational namespace.

YAML is the source representation; graph nodes and Python objects
are derived views, not the source schema.

## Document envelope and identity

```yaml
apiVersion: infra.model/v0alpha1
kind: Server
metadata:
  name: machine-1
  description: Example physical server
  labels:
    environment: lab
spec:
  siteRef: site/lab
status:
  observedAt: '2026-01-01T00:00:00Z'
  availability: available
  operationalState: ready
  extensions:
    inventory.example/observation:
      discoveryState: illustrative
extensions:
  inventory.example/source:
    externalId: opaque-123
```

`apiVersion`, `kind`, `metadata.name`, and `spec` are required. Names match
`[A-Za-z0-9][A-Za-z0-9._-]*` across the entire string, with no whitespace, and are
case-sensitive. Top-level names are unique
within each kind. Unknown core fields are rejected. Optional `metadata.description`
and string-valued `metadata.labels` provide authored metadata. `status` is a
generic object with optional `availability`, `operationalState`, `powerState`
(nonempty descriptive strings), `observedAt` (timezone-qualified date-time;
leap seconds are not supported), and
`extensions`. These observations are not placement evidence. Provider-specific
transient/discovered state belongs under `status.extensions`; unknown status
fields are rejected. The same status structure is used by embedded entities.

Extension containers are permitted at the document, metadata, spec, embedded
entity, status, placement, capability, requirement, image, and location levels
described by the schemas. Extension keys have the form `namespace/key`, for example
`inventory.example/source`. Their values are arbitrary JSON-compatible data.
References inside status or extensions are opaque and are not resolved.

| Document kind | Canonical ID | Layer and meaning |
| --- | --- | --- |
| Site | `site/<name>` | Shared location identity |
| HardwareProfile | `hardware-profile/<name>` | Reusable inventory capabilities |
| Server | `server/<name>` | Physical compute inventory |
| NetworkDevice | `network-device/<name>` | Physical network inventory |
| Network | `network/<name>` | Logical connectivity domain |
| Link | `link/<name>` | Physical point-to-point connectivity |
| Cluster | `cluster/<name>` | Deployment intent with embedded nodes |

There is no top-level ClusterNode document. Canonical entity IDs are derived,
never authored. A detailed NUMA node’s numeric `id` is an inventory identifier,
not a canonical entity ID; NUMA records remain CPU topology values.
Examples of embedded IDs:

```text
server/amst-w2/network-adapter/slot2
server/amst-w2/network-adapter/slot2/interface/p1
server/amst-w2/storage-device/nvme0
network-device/amst-data-sw/interface/swp1
cluster/openstack-lab/node/controller-1
cluster/openstack-lab/node/controller-1/network-attachment/management
```

Named embedded entities have a direct `name` property and optional `extensions` and
`status`. Names must be unique within each collection under a parent. For
example, `server/x/storage-device/nvme-1` and
`server/x/network-adapter/nvme-1` may coexist; two storage devices named
`nvme-1` under `server/x` may not. The existing `storage-device` canonical
segment is retained. The same interface name may appear under different adapters.

## Quantities, capabilities, and requirements

Capacity, frequency, bandwidth, partition size, and alignment quantities use
`{value, unit}` objects with positive finite values. Inventory CPU counts use
positive integers under `compute.cpu`. ClusterNode CPU requirements instead use
`{count, unit}`, with a positive integer count. Units are case-sensitive and
dimension-specific:

| Quantity | Units |
| --- | --- |
| Memory/storage capacity | `B`, `kB`, `MB`, `GB`, `TB`, `KiB`, `MiB`, `GiB`, `TiB` |
| CPU requirement | `vcpu`, `core`, `thread` (with `count`) |
| CPU frequency | `Hz`, `kHz`, `MHz`, `GHz` |
| Link/interface bandwidth | `bps`, `Kbps`, `Mbps`, `Gbps`, `Tbps` |

SI prefixes multiply by powers of 1000; IEC prefixes multiply by powers of 1024.
Comparisons use exact rational scaling, independent of the ambient
`decimal.Decimal` context, and preserve authored units in source and query
dictionaries. Integers are compared without rounding or conversion to decimal
text, including arbitrarily large integers accepted by the input model. Other
JSON numbers use their decimal string representation after parsing; this does
not add arbitrary-precision decimal parsing to the YAML/JSON input model.
Unitless shorthand is rejected for quantities; named inventory CPU count fields
are integers with their dimension fixed by the field name.

Inventory capabilities may declare:

```yaml
capabilities:
  nodeTypes: [baremetal, vm]
  compute:
    architecture: x86_64
    cpu:
      sockets: 2
      cores: 64
      threads: 128
    frequency: {value: 2.4, unit: GHz}
  memory:
    capacity: {value: 512, unit: GiB}
  storage:
    capacity: {value: 2, unit: TB}
    medium: ssd
    protocol: nvme
```

`compute` fields are independently optional. `memory` and `storage`, if present,
require `capacity`. Storage has independent optional `medium` (`hdd`, `ssd`,
`other`) and `protocol` (`nvme`, `sata`, `sas`, `scsi`, `virtio`, `other`).
An NVMe SSD uses `medium: ssd` and `protocol: nvme`. Unknown medium or protocol
is omitted; neither field supports `mixed`. No aggregate composition is inferred.
Architecture is a case-sensitive descriptive string.

Inventory Server CPU fields always describe physical hardware: `sockets` counts
physical processor packages, `cores` total physical cores, and `threads` total
hardware execution threads supported by the processors, regardless of SMT state.
ClusterNode `requirements.compute` has optional `architecture`, `frequency`,
`cpu`, and `extensions`, with CPU demand expressed as:

```yaml
requirements:
  compute:
    cpu: {count: 4, unit: vcpu}
```

`vcpu` means guest/virtual CPU demand, `core` physical-core demand, and `thread`
hardware-thread demand. Exact bare-metal placement can compare core/thread
demand against physical inventory. Physical core/thread counts cannot prove
vCPU compatibility: v0alpha1 has no generic virtualization allocation or
overcommit evidence, so this comparison is UNKNOWN.

Requirements also support `memory` and `storage`, but never `nodeTypes` or
inventory CPU fields such as `cores`. The node's `realization.type` expresses
the requested execution form. Requirements are minimum quantities and exact
string matches for architecture, medium, and protocol. Each storage field is
checked independently when required; missing information yields UNKNOWN.

## Inventory kinds

**Site** has an optional `spec.location` object containing descriptive `description`,
`country`, `region`, and `city` strings. Geography is not inferred from its name.

**HardwareProfile** requires `spec.capabilities`; optional `manufacturer` and
`model` describe hardware. Profiles have no physical identity, component instances,
or site constraint. Empty capabilities express that no capabilities are declared.

**Server** requires `spec.siteRef`. Optional `profileRef` points to a HardwareProfile.
HardwareProfile supplies reusable defaults/capabilities. Actual Server instance
values are authoritative: `spec.capabilities` mappings merge recursively, lists
replace inherited lists, and omitted values inherit. Both quantity fields remain
required when authoring an override. v0alpha1 has no delete/unset operation for
an inherited capability; null is not an unset marker for core capability fields.
Components and status are not inherited; only capabilities participate in this
merge. The same capability merge applies to a NetworkDevice using a profile.
`model.effective_capabilities(id)` returns the merged result, while `get(id)` keeps
the authored resource fields separate from the profile. Status is never merged
into capabilities. Capacities are not inferred or summed from components.

A Server may contain `spec.networkAdapters` and `spec.storage`:

```yaml
networkAdapters:
  - name: slot2
    manufacturer: Example vendor
    model: dual-port
    pciAddress: '0000:41:00.0'
    interfaces:
      - name: p1
        macAddress: '02:00:00:00:02:01'
        capabilities:
          bandwidth: {value: 100, unit: Gbps}
storage:
  devices:
    - name: nvme0
      pciAddress: '0000:42:00.0'
      capacity: {value: 2, unit: TB}
      medium: ssd
      protocol: nvme
```

A NetworkAdapter requires one or more interfaces. Adapter `manufacturer`, `model`,
and `pciAddress` are optional. An Interface requires only a name and may declare
`macAddress` and `capabilities.bandwidth`. An adapter is not itself a Link endpoint.
Storage devices may declare `vendor`, `model`, `serial`, `pciAddress`, `capacity`,
`medium`, `protocol`, `controllerRef`, and `location.bay`. Storage hardware is
described further below; aggregate storage capabilities remain separate and
are never inferred from components.

PCI addresses have `[domain:]bus:device.function` hexadecimal syntax (device
`00`–`1f`, function `0`–`7`); an omitted
domain is treated as `0000` for duplicate detection. They must be unique across
all adapters, storage controllers, and storage devices within one Server.
Different Servers may reuse PCI addresses. MAC addresses use six colon-separated hexadecimal octets and must
be unique across modeled Interfaces, ignoring case. Authored spellings are preserved.

**NetworkDevice** requires `spec.siteRef` and may declare `profileRef`, `capabilities`,
`deviceType` (`switch`, `router`, `firewall`, `other`), and a direct `interfaces`
collection using the same Interface schema. This allows switch ports without
pretending they are Server adapters.

**Link** requires `spec.endpoints`, exactly two distinct canonical Interface IDs.
Both endpoints must resolve. Optional `spec.capabilities.bandwidth` describes
physical link capability. Links have their own identities; parallel Links remain
distinct. The model does not enforce one cable per port or infer logical Networks.

## Logical networking and deployment intent

**Network** has optional `spec.siteRef`, `layer` (`layer2` or `layer3`), and `prefixes`.
Omitting siteRef allows a domain without a single-site scope. Each prefix must be
a strict IPv4/IPv6 CIDR network in prefix-length notation, with no host bits
set: `192.0.2.0/24` and `2001:db8::/64` are valid; `192.0.2.1/24` is not.
Dotted netmasks (`192.0.2.0/255.255.255.0`) and hostmasks
(`192.0.2.0/0.0.0.255`) are rejected. Prefix lengths use unsigned decimal digits
without leading zeros, except `0` itself. Address-family and range checks remain
in force. Multiple prefixes and dual-stack domains are supported.
No provider service, network implementation, or delegation is implied.

**Cluster** requires a nonempty `spec.nodes` collection. Optional `siteRef` supplies
a default placement location. Optional `image` contains a required descriptive
`name` and optional string `version`. Nodes may supply their own image; no image
resolution is performed or materialized in source dictionaries.

```yaml
apiVersion: infra.model/v0alpha1
kind: Cluster
metadata:
  name: demo
spec:
  siteRef: site/lab
  image: {name: Rocky Linux, version: '9'}
  nodes:
    - name: controller-1
      realization: {type: vm}
      roles: [controller]
      requirements:
        compute:
          cpu: {count: 4, unit: vcpu}
        memory:
          capacity: {value: 16, unit: GiB}
        storage:
          capacity: {value: 80, unit: GiB}
      networkAttachments:
        - name: management
          networkRef: network/management
          addresses: [192.0.2.11]
```

Every ClusterNode requires `name` and `realization.type` (`vm` or `baremetal`).
Optional fields are `placement`, `requirements`, `roles`, `image`,
`networkAttachments`, and `configuration`. `placement.siteRef` overrides the Cluster's `spec.siteRef`
default. Roles are unique strings with no execution semantics. The earlier
node-level `nodeType`, `siteRef`, and `resourceRef` fields are not accepted.

Each network attachment requires a name and a `networkRef` resolving to a Network.
Optional `addresses` are unique plain IPv4/IPv6 host literals, without prefix
lengths or IPv6 zone IDs. Every address must lie within at least one same-family
prefix declared on that Network. An addressless attachment may refer to a Network
without prefixes. Addresses are intent, distinct from observed addresses in status.

## Exact resource placement

`placement.resourceRef` means exact selection of an existing inventory resource.
It is supported only for `realization.type: baremetal` and must resolve to
`server/<name>`. The logical node and physical Server remain separate entities.

```yaml
name: worker-1
realization: {type: baremetal}
placement:
  siteRef: site/AMST
  resourceRef: server/amst-w2
requirements:
  compute:
    cpu: {count: 4, unit: core}
```

VM nodes use site placement and requirements in the generic model. A VM
`placement.resourceRef` is rejected; it does not select a hypervisor host.
Provider host affinity may be carried in a namespaced extension, for example
`placement.extensions["provider.example/host-affinity"]`, until a future generic
placement concept is designed. Such opaque extensions are not resolved or
evaluated by this validator.

Validation checks exact bare-metal selection as follows:

1. Effective node site (`node.placement.siteRef`, then `cluster.spec.siteRef`)
   matches the Server site when specified.
2. The requested realization appears in effective `capabilities.nodeTypes` when
   declared (`baremetal` or `vm`; advertisement alone does not prove allocation).
3. Requested physical core/thread demand and memory, storage, and frequency
   minima are met by effective capabilities after exact unit conversion.
4. Requested architecture, storage medium, and storage protocol each match
   their declared capability independently.

The internal compatibility contract has three results:

| Result | Meaning | Validation outcome |
| --- | --- | --- |
| SATISFIED | Available known capability proves the requirement is met | No diagnostic |
| UNSATISFIED | Available known capability proves the requirement is not met | `incompatible_resource` error |
| UNKNOWN | Generic information is insufficient to prove either result | `unevaluated_requirement` warning |

Missing capabilities, vCPU demand against physical inventory, and opaque
requirement extensions yield UNKNOWN. Without an exact resource, site-only or
unplaced intent has one UNKNOWN placement warning per node; validation does not
search for or require matching inventory. Authored storage sources still require
existing inventory and receive additional UNKNOWN availability warnings without
exact placement, as described in the storage configuration contract below.

A successfully validated model is not necessarily proven deployable. Image
compatibility, live availability, network reachability, allocation across nodes,
and exclusivity are not evaluated. Each exact placement is checked independently;
no scheduling, reservations, or VM overcommit policy is implemented.

## Validation and normalization

The processing pipeline is:

1. Safely parse files; reject ambiguous/non-JSON-compatible YAML.
2. Validate all source documents against the bundled schemas.
3. Derive canonical IDs, enforce name uniqueness, index embedded entities, and
   expand documented local storage references.
4. Resolve references and check physical identity, CPU topology, storage and
   block-device configuration, IP membership, and exact placement.
5. Construct topology lazily when requested.

Files and documents need not be dependency ordered. Schema and semantic errors
carry stable codes such as `schema`, `duplicate_name`, `duplicate_component_name`,
`unresolved_reference`, `duplicate_mac`, `duplicate_pci`, `address_outside_prefix`,
and `incompatible_resource`. A failed phase raises `ValidationError` and prevents
invalid data or an incomplete graph from being returned. A successful immutable
`ValidationReport` includes document/entity counts and warning issues.

Normalized results add `id` to every entity and `kind` to embedded entities, leaving
authored spec/status/extensions separate. Public query results are copies. Names,
units, and MAC/PCI spelling retain authored representations. Existing network
and placement references already use canonical syntax. Local storage references
expand to full canonical IDs in all normalized dictionary and graph views.
Source inputs and opaque status/extensions are never mutated.

## Public construction API

`InfrastructureModel.load(path)` and
`InfrastructureModel.from_documents(iterable_of_source_dicts)` are the supported
public construction APIs. The first parses YAML; the second accepts JSON-compatible
source dictionaries. Both validate explicitly with `validate()` or on the first
query. Direct construction from internal `SourceDocument` records is implementation
behavior, not a supported public API. Query dictionaries with generated IDs are
not source documents for reloading.

## Topology API

The internal graph is a NetworkX MultiDiGraph. No NetworkX object is returned by
the public API. Every top-level and embedded entity is a vertex with a canonical
ID. Edges preserve these meanings and directions:

| Relation | Direction |
| --- | --- |
| `contains` | Parent → embedded entity |
| `located_at` | Inventory resource or Network → Site |
| `targets_site` | Cluster/ClusterNode → requested Site (including node default) |
| `uses_profile` | Server/NetworkDevice → HardwareProfile |
| `placed_on` | Bare-metal ClusterNode → exact selected Server |
| `attached_to` | NetworkAttachment → Network |
| `network_attachment` | ClusterNode → Network, keyed by attachment ID |
| `terminates_at` | Link → each Interface endpoint |
| `physical_link` | Interface → peer, in both directions, keyed by Link ID |
| `has_storage_controller` | Server → StorageController |
| `has_storage_device` | Server → StorageDevice |
| `has_storage_volume` | Server → StorageVolume |
| `controls` | StorageController → StorageDevice |
| `provides` | StorageController → StorageVolume |
| `uses_device` | StorageVolume → StorageDevice |
| `configures` | ClusterNode → PartitionTable, PhysicalVolume, VolumeGroup, or LogicalVolume |
| `uses_block_device` | PartitionTable → StorageDevice/StorageVolume; PhysicalVolume → Partition/StorageDevice/StorageVolume |
| `has_partition` | PartitionTable → Partition |
| `uses_pv` | VolumeGroup → PhysicalVolume |
| `allocated_from` | LogicalVolume → VolumeGroup |

`topology.nodes(kind=None)` returns normalized dictionaries;
`topology.edges(relation=None)` returns dictionaries with `source`, `target`,
`key`, and `relation`, plus `link` or `attachment` IDs for their respective edges.
`topology.neighbors(id, relation=None, direction="out")` returns sorted unique
IDs; direction may be `out`, `in`, or `both`. `len(topology)` counts entities and
`id in topology` tests membership. Neighbors collapse duplicate peers, while
edge queries retain parallel connections. No logical connectivity is inferred
from physical paths or vice versa.


## CPU topology, SMT, and NUMA

CPU fields are independently optional under `spec.capabilities.compute.cpu`.
`vendor` and `model` are descriptive strings. Server inventory may record:

```yaml
compute:
  cpu:
    vendor: AMD
    model: EPYC
    sockets: 2
    cores: 128
    threads: 256
    topology:
      coresPerSocket: 64
      threadsPerCore: 2
    smt:
      supported: true
      enabled: true
    numa:
      mode: numa
      nodesPerSocket: 4
```

`sockets` counts physical processor packages, `cores` total physical cores,
`threads` total supported hardware execution threads, `coresPerSocket` physical
cores per package, and `threadsPerCore` supported hardware threads per core.
All counts are positive integers, including `threadsPerCore >= 1`.
SMT is the generic term. `smt.supported` is hardware/platform capability and
`smt.enabled` is current Server configuration. The validator checks:

- `sockets * topology.coresPerSocket == cores` when all three are present.
- `cores * topology.threadsPerCore == threads` when all three are present.
- SMT enabled with explicit lack of support is UNSATISFIED; enabled with unknown
  support is UNKNOWN. Counts never infer the enabled state.

Disabling SMT does not reduce `threads` or change hardware-thread requirement
comparisons. Those comparisons describe hardware capability, not OS-visible CPU
counts or proof of runtime availability.

NUMA mode is `numa` or `interleaved`; omission means unknown/not recorded.
`nodesPerSocket` is a positive integer. `interleaved` needs neither a count nor
nodes, and zero is never an interleaving sentinel. Generic counts 1, 2, and 4 may
map to AMD NPS1, NPS2, and NPS4 in a future platform adapter; AMD NPS0 may map to
`mode: interleaved`. These vendor terms are not core enum values.

A supplied detailed `numa.nodes` list is a complete node inventory. Each record
requires nonnegative integer `id` and `socket`; `cores` is an optional positive
integer to allow unknown per-node counts, and `memory.capacity` is optional:

```yaml
nodes:
  - id: 0
    socket: 0
    cores: 16
    memory:
      capacity: {value: 64, unit: GiB}
```

The excerpt shows one record, not the full eight-node inventory in the complete
example. Numeric node IDs must be unique within the Server. Socket indices are
zero-based and must be below `sockets`; unknown socket count produces UNKNOWN
warnings. In `mode: numa`, a supplied nodes list must have exactly
`sockets * nodesPerSocket` entries when both counts are known. If every node
has a core count and the aggregate is known, node cores must sum to aggregate
physical cores. NUMA memory totals need not equal Server memory in v0alpha1.
No Linux logical CPU IDs, affinity masks, cpusets, or scheduler topology are modeled.

HardwareProfile uses the same CPU hardware defaults, but only support fields for
SMT/NUMA:

```yaml
compute:
  cpu:
    vendor: AMD
    model: EPYC
    smt: {supported: true}
    numa: {supportedNodesPerSocket: [1, 2, 4]}
```

`enabled`, `mode`, `nodesPerSocket`, and detailed `nodes` are Server-only fields;
profiles cannot claim an actual Server configuration. Effective CPU inventories
are checked after recursive profile merging, and standalone profile aggregate
counts/topology are checked too. Server values override defaults, lists replace,
and omitted values inherit. Explicit support restrictions are also checked
against the original profile: an instance cannot turn profile SMT unsupported
into supported, advertise NUMA counts outside a profile support list, or select
an unsupported count by overriding that list. Missing support information gives
UNKNOWN when a configured state needs it. Mere omission does not invent capability
or produce warnings for every possible missing optional field.

## Storage hardware inventory

`Server.spec.storage` contains optional `controllers`, `devices`, and `volumes`
lists. Names are unique separately within each collection. These are physical
hardware inventory/configuration; they are never inherited from a profile.
`capabilities.storage` still describes an independently declared aggregate
capability for placement, with no automatic summation.

- **StorageController** requires `name`; optional `vendor`, `model`, `serial`,
  `pciAddress`, and `mode` describe it. Modes are `raid`, `hba`, `jbod`, `other`.
  There is no redundant controller `type` field.
- **StorageDevice** requires `name`. Optional `controllerRef` selects a controller
  in this Server. Optional `medium` (`hdd`, `ssd`, `other`) and `protocol`
  (`nvme`, `sata`, `sas`, `scsi`, `virtio`, `other`) remain independent. Identity,
  capacity, and nonnegative integer `location.bay` fields are optional.
- **StorageVolume** requires `name` and `controllerRef`. It is a block device
  provided by a controller, such as a hardware RAID virtual disk. Optional
  `deviceRefs` lists member devices; `capacity` is declared, not calculated.
  Optional `raid` requires `level`: `raid0`, `raid1`, `raid5`, `raid6`, `raid10`,
  `raid50`, `raid60`, or `other`. Controller mode and volume RAID level describe
  different dimensions; firmware mode/level policy is not inferred.

Controller and device references accept names local to their Server or canonical
IDs within that same Server. They must resolve. Duplicate member references
are rejected after normalization, including a local and canonical spelling of
the same device. Known controller conflicts between volume and device are errors;
missing member controller information yields UNKNOWN warnings. There is no RAID
capacity/parity math or controller configuration policy.

## ClusterNode block-device configuration

`ClusterNode.configuration.storage` is optional and contains `partitionTables`
and optional `lvm`. This physical-source configuration supports bare-metal nodes;
VM storage/VM NUMA is deferred. Every referenced hardware block source must exist.
For exact placement, its owning Server must equal `placement.resourceRef`.
Without exact placement, source existence is checked but availability to the node
is UNKNOWN; the validator does not infer placement from a storage reference.
A PV that references a local partition uses that table’s checked hardware source.
Cross-node partition/PV/VG references are always invalid, even if both nodes
select the same Server.

A partition table requires `name`, `sourceRef`, `type` (`gpt` or `mbr`), and a
nonempty ordered `partitions` list. Optional `alignment` is a byte quantity.
There is no table type `none`; whole-device PVs do not require a partition table.
The source must be `server/<server>/storage-volume/<volume>` or
`server/<server>/storage-device/<device>`.

A partition requires `name`, `type`, and either `size: {value, unit}` or
`grow: true`, but never both. `grow: false` is permitted with fixed size and is
not a substitute for size. At most one partition per table may grow. Names and
supplied positive integer `number` values must be unique within the table.
Numbers may be omitted for later deterministic assignment; the normalizer keeps
list order and does not assign numbers or sectors.

Partition `type` is the block classification: `efi-system`, `bios-boot`,
`linux-filesystem`, `linux-lvm`, `linux-swap`, `linux-raid`, or `other`.
Optional `role` is semantic intent: `efi`, `boot`, `root`, `swap`, `lvm`, `data`,
or `other`. Neither field selects a filesystem or mount point. An optional
nonempty `typeId` supplies an exact uncommon GPT GUID/MBR identifier only when
`type: other`; v0alpha1 preserves it without platform-specific parsing.

LVM contains three optional collections, with names unique within each collection
across the ClusterNode:

- **PhysicalVolume** requires `name` and `sourceRef`. Sources are a local
  partition (`partition-table/<table>/partition/<partition>`), its canonical
  ID, a hardware storage volume, or a raw storage device. Each exact normalized
  source may be assigned to only one PV in the node.
- **VolumeGroup** requires `name` and nonempty `physicalVolumeRefs` resolving to
  PVs in the same node. References may be local PV names or full canonical IDs.
  Duplicate references are invalid, and a PV may belong to at most one VG.
- **LogicalVolume** requires `name`, `volumeGroupRef`, and either
  `capacity: {value, unit}` or `grow: true`, exclusively. The VG reference is
  local by name or canonical within the node. At most one LV per VG may grow;
  different VGs may each have a grow LV. LV names are unique across the node,
  not merely within each VG.

The validator does not calculate allocation sizes, partition boundaries, RAID
capacity, or LVM extents. Shared use of hardware sources across partition tables,
RAID membership, or whole-device consumers is not a provisioning/exclusivity
check in this pass. Exact-source PV uniqueness is enforced as described above.

## Storage identities and reference normalization

The existing `storage-device` segment is retained. New canonical identities are:

| Kind | Canonical ID |
| --- | --- |
| StorageController | `server/<server>/storage-controller/<name>` |
| StorageDevice | `server/<server>/storage-device/<name>` |
| StorageVolume | `server/<server>/storage-volume/<name>` |
| PartitionTable | `cluster/<cluster>/node/<node>/storage/partition-table/<name>` |
| Partition | `cluster/<cluster>/node/<node>/storage/partition-table/<table>/partition/<name>` |
| PhysicalVolume | `cluster/<cluster>/node/<node>/storage/lvm/pv/<name>` |
| VolumeGroup | `cluster/<cluster>/node/<node>/storage/lvm/vg/<name>` |
| LogicalVolume | `cluster/<cluster>/node/<node>/storage/lvm/lv/<name>` |

`storage` and `lvm` are grouping paths, not extra graph entities. Named objects
retain `contains` edges to their Server, ClusterNode, or partition table and also
have the semantic edges listed in the topology contract above. No second graph
API or exposed NetworkX representation is introduced. Names retain their exact
spelling (`vg_system` is not rewritten as `vg-system`).

Local references are expanded only in their documented field-specific scope.
There is no basename search, cross-scope fallback, or traversal syntax. Full
canonical references are also checked for correct kind and local ownership.
Source YAML and input dictionaries remain untouched; normalized results include
canonical references. Files and collection entries need not be dependency ordered.
Duplicate reference checks run after canonicalization.

New semantic diagnostic codes include `cpu_topology`, `duplicate_numa_id`,
`numa_socket`, `numa_node_count`, `numa_core_count`, `reference_scope`,
`duplicate_reference`, `storage_controller_conflict`, `storage_placement`,
`invalid_storage_configuration`, `duplicate_partition_number`, `multiple_grow`,
`duplicate_pv_source`, and `pv_multiple_vgs`. Existing `schema`,
`duplicate_component_name`, `unresolved_reference`, `incompatible_resource`,
and `unevaluated_requirement` conventions continue to apply.

## Filesystem policy boundary

The [complete bare-metal example](../examples/baremetal/infrastructure.yaml)
includes an EFI-classified partition, boot-role partition, LVM partition,
controller-volume and raw-device PVs, VGs, fixed-size LVs, and grow LVs.
It deliberately stops at the block-device/LVM layer.

A conventional later configuration policy might choose:

| Block object | Filesystem | Mount point |
| --- | --- | --- |
| `lv_root` | XFS | `/` |
| `lv_var` | XFS | `/var` |
| `lv_home` | XFS | `/home` |
| `boot` | XFS | `/boot` |
| `efi` | vfat | `/boot/efi` |

This is documentation of a possible future Ansible/Nix policy, not schema data
or an implicit default. Filesystem creation, formatting, mount points, fstab,
and NixOS filesystem declarations remain outside the infrastructure core.
All additional deferred features are listed in [decisions.md](decisions.md#intentionally-deferred).

## Source-model migration

This remains v0alpha1 with clean schema corrections and no deprecated aliases:

| Previous source field | Current source field |
| --- | --- |
| `spec.capabilities.compute.sockets: {value: 2, unit: socket}` | `spec.capabilities.compute.cpu.sockets: 2` |
| `spec.capabilities.compute.cores: {value: 128, unit: core}` | `spec.capabilities.compute.cpu.cores: 128` |
| `spec.capabilities.compute.threads: {value: 256, unit: thread}` | `spec.capabilities.compute.cpu.threads: 256` |
| `Server.spec.storageDevices` | `Server.spec.storage.devices` |
| StorageDevice `manufacturer` | StorageDevice `vendor` |

CPU changes apply wherever inventory capabilities are authored, including
profiles and NetworkDevices. `compute.architecture`, `compute.frequency`,
CPU requirement `{count, unit}`, aggregate `capabilities.storage`, and other
manufacturer fields retain their existing shapes. Old source shapes are rejected.
Existing canonical IDs and relations are retained; storage identity/relationship
additions and local-reference normalization are described above. No migration
command, source writer, provider adapter, or schema-version change is introduced.
