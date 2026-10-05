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

There is no top-level ClusterNode document. IDs are derived, never authored.
Examples of embedded IDs:

```text
server/amst-w2/network-adapter/slot2
server/amst-w2/network-adapter/slot2/interface/p1
server/amst-w2/storage-device/nvme0
network-device/amst-data-sw/interface/swp1
cluster/openstack-lab/node/controller-1
cluster/openstack-lab/node/controller-1/network-attachment/management
```

Embedded entities have a direct `name` property and optional `extensions` and
`status`. Names must be unique within each collection under a parent. For
example, `server/x/storage-device/nvme-1` and
`server/x/network-adapter/nvme-1` may coexist; two storage devices named
`nvme-1` under `server/x` may not. The existing `storage-device` canonical
segment is retained. The same interface name may appear under different adapters.

## Quantities, capabilities, and requirements

Inventory quantities and non-CPU requirements use `{value, unit}` objects.
Values must be positive finite numbers; socket, core, and thread counts must be
integral. ClusterNode CPU requirements instead use `{count, unit}`, with a
positive integer count. Units are
case-sensitive and dimension-specific:

| Quantity | Units |
| --- | --- |
| Memory/storage capacity | `B`, `kB`, `MB`, `GB`, `TB`, `KiB`, `MiB`, `GiB`, `TiB` |
| Physical CPU sockets | `socket` |
| Physical CPU cores | `core` |
| Hardware CPU threads | `thread` |
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
Unitless shorthand is rejected.

Inventory capabilities may declare:

```yaml
capabilities:
  nodeTypes: [baremetal, vm]
  compute:
    architecture: x86_64
    sockets: {value: 2, unit: socket}
    cores: {value: 64, unit: core}
    threads: {value: 128, unit: thread}
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
physical sockets, `cores` physical cores, and `threads` hardware threads.
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

A Server may contain `spec.networkAdapters` and `spec.storageDevices`:

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
storageDevices:
  - name: nvme0
    pciAddress: '0000:42:00.0'
    capacity: {value: 2, unit: TB}
    medium: ssd
    protocol: nvme
```

A NetworkAdapter requires one or more interfaces. Adapter `manufacturer`, `model`,
and `pciAddress` are optional. An Interface requires only a name and may declare
`macAddress` and `capabilities.bandwidth`. An adapter is not itself a Link endpoint.
Storage devices may declare `manufacturer`, `model`, `pciAddress`, `capacity`,
`medium`, and `protocol`, using the same independent vocabularies as storage
capabilities.

PCI addresses have `[domain:]bus:device.function` hexadecimal syntax (device
`00`–`1f`, function `0`–`7`); an omitted
domain is treated as `0000` for duplicate detection. They must be unique across
all adapter/storage components within one Server. Different Servers may reuse
PCI addresses. MAC addresses use six colon-separated hexadecimal octets and must
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
Optional fields are `placement`, `requirements`, `roles`, `image`, and
`networkAttachments`. `placement.siteRef` overrides the Cluster's `spec.siteRef`
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
unplaced intent remains valid, with one UNKNOWN warning per node; validation
does not search for or require matching inventory.

A successfully validated model is not necessarily proven deployable. Image
compatibility, live availability, network reachability, allocation across nodes,
and exclusivity are not evaluated. Each exact placement is checked independently;
no scheduling, reservations, or VM overcommit policy is implemented.

## Validation and normalization

The processing pipeline is:

1. Safely parse files; reject ambiguous/non-JSON-compatible YAML.
2. Validate all source documents against the bundled schemas.
3. Derive canonical IDs, enforce name uniqueness, and index embedded entities.
4. Resolve references and check physical identity, IP membership, and exact placement.
5. Construct topology lazily when requested.

Files and documents need not be dependency ordered. Schema and semantic errors
carry stable codes such as `schema`, `duplicate_name`, `duplicate_component_name`,
`unresolved_reference`, `duplicate_mac`, `duplicate_pci`, `address_outside_prefix`,
and `incompatible_resource`. A failed phase raises `ValidationError` and prevents
invalid data or an incomplete graph from being returned. A successful immutable
`ValidationReport` includes document/entity counts and warning issues.

Normalized results add `id` to every entity and `kind` to embedded entities, leaving
authored spec/status/extensions separate. Public query results are copies. Names,
units, MAC/PCI spelling, and references retain authored representations; ID
derivation and comparison-specific canonicalization are separate operations.

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

`topology.nodes(kind=None)` returns normalized dictionaries;
`topology.edges(relation=None)` returns dictionaries with `source`, `target`,
`key`, and `relation`, plus `link` or `attachment` IDs for their respective edges.
`topology.neighbors(id, relation=None, direction="out")` returns sorted unique
IDs; direction may be `out`, `in`, or `both`. `len(topology)` counts entities and
`id in topology` tests membership. Neighbors collapse duplicate peers, while
edge queries retain parallel connections. No logical connectivity is inferred
from physical paths or vice versa.
