# v0alpha1 implementation decisions and review items

This document records the targeted architecture-review corrections for v0alpha1.

## FABNet Phase A: generic Layer-3 intent and offline internal services

`Network.spec.addressFamily` (`ipv4`, `ipv6`), `addressAllocation`
(`provider-managed`), and `reachability` (`provider-internal`, `public`) are
optional provider-neutral requirements. Any authored field requires explicit
`layer: layer3`; nothing infers or inserts that layer. Family selects no subnet.
Provider-managed allocation delegates prefix and endpoint-address selection to
the provider and deployment workflow, without promising automatic control-plane
assignment of every VM address. Provider-internal requests the provider's routed
infrastructure, without promising a private or isolated tenant network. Public
requests external reachability without promising routing activation.

Core validation checks family consistency against authored prefixes and attachment
addresses. Provider-managed allocation requires explicit family and omission of
prefixes and attachment addresses, including empty lists. Gateway/DNS semantics
remain independent, including existing declared-prefix and duplicate checks.
Partial Layer-3 intent and public reachability remain generic-valid. The generic
model gains no provider service, endpoint-site/cardinality, NIC, VM, or per-node
attachment restrictions. Scope, connectivity, placement, and adapterGroup retain
their meanings. Normalized views preserve explicit fields and omit absent ones.
`common.json` needs no change: the new fields belong only to Network.spec.

A1 was validated before changing the adapter: 189 focused networking tests and
953 generic-core tests passed. The existing generic dictionary/IR representation
already preserves the new attributes; no additional public model types are needed.

A2 resolves complete explicit Layer-3 + provider-managed + provider-internal
intent to `IPv4` (FABNetv4) or `IPv6` (FABNetv6) according to addressFamily.
Public/external FABNet (`IPv4Ext`, `IPv6Ext`) is deferred to Phase B with an explicit
translation error. Names and prefixes do not select services. Existing L2Bridge,
L2STS, and addressless ConnectX-6-only L2PTP selection remains unchanged.

One selected Network realizes one service at exactly one distinct mapped endpoint
site. Allowed `siteRefs` are not a service count. A multi-site Cluster uses separate
independent Networks; no tunnels, routes, services, placement, or workers are
synthesized. Selected Networks shared across Clusters fail completeness checks.
Only NIC_Basic attachments qualify, with at most one FABNet attachment per VM;
other eligible L2 attachments can coexist. Dedicated FABNet, DPU-backed and
FPGA-backed attachments remain deferred, while allocation-only devices are unchanged.

The immutable private Network plan adds `emit_empty_subnet`; the interface plan
adds `emit_empty_address_families`. Both default false. FABNet plans hold no
addresses and explicitly request `subnet: {}`, `ipv4: {}`, and `ipv6: {}`. The
renderer mechanically emits these placeholders without changing existing L2
bytes, grouping, or naming. Authored Network prefixes/gateways/DNS and attachment
addresses are rejected by presence, even when empty; none is silently discarded.
No runtime allocation model, public API, or FABlib/FIM dependency is introduced.

In the reviewed downstream workflow, FABRIC supplies allocated prefix/gateway
information and fabric-generic-cluster selects and configures guest addresses
after submission. DHCP is not assumed; candidate generation is not reservation-aware,
IPv6 address rediscovery may be imperfect, and persistent guest interface/routing
configuration requires verification. Successful service creation does not prove
guest connectivity. The first-matching-interface and persistent route limitations
motivate the one-FABNet-attachment restriction. Compatibility is limited to the
reviewed fabric-generic-cluster 1.0.20, FABlib 1.9.5, and fabric-fim 1.9.0 context.

Three generic acceptance fixtures correspond to `_slice_topology_7-1.yml`,
`_slice_topology_7-2.yml`, and `m5-v4.yml`, compared semantically with the actual
read-only notebook files. The focused fixture config preserves explicit workers
and historical images without changing the standard adapter configuration.
No live operations, inventory preflight, scheduling, downstream changes, or
external FABNet implementation are included. See [the adapter contract](fabric-adapter.md#internal-fabnet-phase-a).

The schema API remains `v0alpha1`; the Python distribution remains `0.1.0a1`.
These are separate version domains. The existing `infra.model` API namespace
is provisional; this pass establishes no permanent organizational namespace.

## Preserved architecture

- YAML is the authored source; Draft 2020-12 JSON Schema describes its structure.
- Physical inventory and logical deployment intent remain separate. ClusterNode
  is embedded in Cluster, with an identity and requirements of its own.
- Server is physical. A NetworkAdapter is a physical component containing
  Interface entities. NetworkDevice exposes its own interfaces.
- Link joins exactly two physical interfaces. Network is a logical domain;
  a network attachment does not imply a cable, VLAN, route, or service implementation.
- Inventory capabilities advertise resources; node requirements request them.
- Provider payloads are opaque namespaced extensions. Status holds observed or
  transient information and is excluded from placement validation.
- Resource quantities have explicit values and units. Source schemas and
  semantic validation have no dependency on NetworkX representations.
- Thin normalized dictionaries and a private MultiDiGraph provide the public API.

## Current decisions and remaining review scope

| Topic | Implemented behavior | Review question |
| --- | --- | --- |
| Envelope | `apiVersion: infra.model/v0alpha1`, `kind`, `metadata`, `spec`; optional `status` and `extensions` | Confirm version spelling and field names against any earlier design. |
| Reference syntax | Canonical IDs with case-sensitive names; documented storage-local syntax expands deterministically | Confirm case sensitivity and allowed name characters. |
| Embedded fields | `accelerators`, `networkAdapters`, `interfaces`, `storage.{controllers,devices,volumes}`, `nodes`, `networkAttachments`, node `requirements.devices`, and node partition/LVM collections; named embedded entities have `name` directly | Confirm field shapes; embedded objects do not add another `metadata/spec` envelope. |
| Component uniqueness | Names unique within each collection under a parent; accelerator/adapter/storage names may coincide | Canonical IDs retain kind-specific segments, including the existing `storage-device` segment. |
| Profile inheritance | Profile capabilities supply defaults; actual resource values are authoritative. Mappings merge recursively, lists replace, omitted values inherit | Components/status are not inherited. No delete/unset operation or partial quantity overlay is provided. |
| Exact resource placement | `placement.resourceRef` selects an existing `server/<name>` only for `realization.type: baremetal` | VM `resourceRef` remains rejected and never aliases `hostRef`. |
| Exact VM host placement | Optional `placement.hostRef` requests an existing physical Server/hypervisor only for `realization.type: vm`; it must match an already effective node site | Desired identity only, with `targets_host` edges; no capacity evaluation, scheduling, availability proof, or site derivation. Host and resource references are mutually exclusive. |
| Realization | `realization.type` is `vm` or `baremetal`; inventory `capabilities.nodeTypes` uses the same spellings | Explicit inventory advertisement is retained; Server type alone does not imply allocatability. Node-level legacy placement/type fields are rejected. |
| CPU semantics | Integer inventory `compute.cpu.sockets/cores/threads` describe physical packages/cores and supported hardware threads. SMT enabled state is separate. Requirements use `compute.cpu: {count, unit}` with `vcpu`, `core`, or `thread` | Only exact bare-metal core/thread comparison is supported. Physical inventory never proves vCPU capacity; allocation/overcommit evidence is outside this version. |
| Storage vocabulary | Independent optional `medium` and `protocol`; NVMe SSD is `ssd` plus `nvme` | `media` and `mixed` are removed. Unknown composition is omitted. Hardware controllers/devices/volumes now form explicit topology, with node partition/LVM intent separate. |
| Compatibility | SATISFIED proves a requirement is met; UNSATISFIED proves it is not; UNKNOWN lacks enough generic evidence | Known failures are errors; UNKNOWN yields warnings. Site-only/unplaced nodes get one UNKNOWN placement warning each; storage sources may add availability warnings; successful validation is not proof of deployability. Component checks treat an authored collection as complete, with missing properties unknown. |
| Placement scope | Compare each node independently; no capacity subtraction, reservations, or exclusive bare-metal binding | Confirm later admission/allocation policy separately from this static model. |
| Quantities | SI and IEC byte units; positive integer physical CPU counts and explicit CPU requirement counts; frequency and bandwidth units; all values strictly positive | Confirm whether zero quantities, alternate spellings, overcommit ratios, or fractional CPU requests are needed later. |
| PCI scope | Uniqueness within a Server across accelerators, adapters, storage controllers, and storage devices; absent domain means `0000`; hex is case-insensitive | A global uniqueness check would incorrectly reject ordinary PCI address reuse on different machines. |
| MAC scope | Uniqueness across every modeled inventory Interface, case-insensitive | Intentional shared virtual/anycast MACs are outside this physical inventory pass. |
| Address shape | Attachment `addresses` are plain IPv4/IPv6 literals; Network `prefixes` use strict CIDR prefix-length notation, rejecting dotted netmasks/hostmasks | Confirm whether future addresses should include per-address metadata or allocation modes. |
| Network prefix semantics | An attachment address must belong to a prefix of its referenced Network; an attachment address with no declared prefix fails | Overlapping prefixes, duplicate host IPs, reserved/broadcast addresses, and routing policies are not validated. |
| Network gateway/DNS configuration | Optional shared `defaultGateways` and `dns.servers`; one gateway per declared prefix, matching IP family without requiring containment; DNS addresses are unique and ordered | Omission is unspecified; explicit empty lists mean none. Arbitrary static routes, metrics, and attachment overrides are deferred. |
| Site inheritance | `Cluster.spec.siteRef` is the default placement site for ClusterNodes; `ClusterNode.placement.siteRef` is the per-node override; otherwise the effective site is unresolved | Exact resources must match a supplied effective site. Nodes in one Cluster may use different sites. |
| Network site scope | Optional `Network.spec.siteRefs` is a nonempty unique list of resolving canonical Site references; attached nodes with resolved effective sites must be within it | Omission is unconstrained. Unused allowed sites are valid; authored order is preserved but has no semantic significance. Unresolved node sites add no redundant Network warning. |
| Network connectivity | Omitted or explicit `multipoint` means a shared connectivity domain; `point-to-point` permits at most two NetworkAttachments across all nodes and Clusters, independent of layer | No schema default, minimum of three for multipoint, or exactly-two requirement in core. Provider adapters may enforce stronger realizability constraints. |
| OS image and roles | Cluster image supplies descriptive default intent; nodes may override image; roles are free strings | Image resolution, OS suitability, role definitions, and configuration generation are deferred. |
| Extensions/status | Generic status fields are `availability`, `operationalState`, `powerState`, `observedAt`, and namespaced `extensions`; shared where entity schemas support status. Accelerator inventory adds no status or provider-specific fields | Provider transient/discovered state belongs under `status.extensions` where supported. Payloads stay opaque; no provider-specific status schemas. |
| Example fidelity | Hardware values and IDs are illustrative | Confirm authoritative inventory data separately; these examples are not live provider claims. |

## Loading, packaging, and diagnostics

The supported public constructors are `InfrastructureModel.load(...)` and
`InfrastructureModel.from_documents(...)`. Direct construction from
`SourceDocument` records remains internal plumbing, without a public API redesign.

Quantity comparisons use exact rational scaling instead of ambient Decimal
arithmetic. This preserves SI/IEC distinctions and arbitrarily large supported
integers. Input parsing remains JSON-compatible; finite float values use their
decimal spelling for comparison. No global Decimal context is modified.

The loader accepts one document per file, multi-document YAML, and recursive
directories in sorted path order. Empty document separators are ignored; an
entirely empty input is an error. It rejects duplicate mapping keys, unsafe YAML
tags, non-string keys, non-JSON values, non-finite numbers, and recursive aliases.
Aliases without cycles are copied. Implicit timestamps remain strings. PyYAML's
other SafeLoader scalar conventions apply; quote ambiguous values such as `on`
or `yes` when they must be strings. Merge keys are expanded, but duplicate keys
after expansion are errors rather than silent overrides.

Validation runs in phases: schema, identity registry, semantics. It aggregates
issues within each phase, then stops if that phase fails to prevent misleading
downstream diagnostics. Source paths use JSON Pointer escaping, prefixed with
the filename and YAML document number. Validation is cached because the public
API exposes defensive copies and no mutation operations.

Schemas are stored once in `schema/v0alpha1/`. Setuptools maps this directory to
the private `infra_model._schemas` resource package in distributions; it is not a
second generated schema copy checked into source control. `$id` values under
`https://infra-model.invalid/` identify schemas, not a hosted service. References
resolve through a local registry. Custom `ip-address` and `ip-prefix` formats are
enforced by the Python validator; external JSON Schema tools must register those
formats for equivalent address/prefix syntax checks and still need semantic checks.
Observation `date-time` checking is also local, without an optional dependency;
timezones are required and leap seconds are unsupported.

## CPU topology and storage extension decisions

The existing capabilities envelope is retained. Inventory counts move to
`spec.capabilities.compute.cpu` as integers; `compute.architecture` and
`compute.frequency` remain in place. Profiles may supply hardware defaults,
SMT support, and `numa.supportedNodesPerSocket`, but cannot claim configured
`smt.enabled`, `numa.mode`, `numa.nodesPerSocket`, or detailed NUMA nodes.
Server effective CPU values are validated after the existing recursive merge.
Instance overrides cannot hide explicit profile support restrictions. Missing
support evidence gives UNKNOWN, not invented capability.

`threads` always means processor-supported hardware execution threads. It never
means OS-visible CPUs after SMT is enabled or disabled, and it never implies
`smt.enabled`. NUMA is generic: `numa` and `interleaved` modes, with positive
`nodesPerSocket` where recorded. AMD NPS names belong to future platform adapters.
A supplied detailed nodes list is treated as complete; `id` and `socket` are
required, while core counts may be unknown/omitted. This resolves the requested
conditional core-sum rule without adding a separate completeness flag. Numeric
NUMA IDs remain inventory values, not separate canonical graph identities.

Hardware storage moves from `Server.spec.storageDevices` to
`Server.spec.storage.{controllers,devices,volumes}`. Devices retain the
`storage-device` canonical segment. Controllers use `mode`; volumes use
`raid.level`, with no redundant controller type. Optional storage-device
`manufacturer` becomes `vendor` to match the storage inventory vocabulary.
The accelerator correction extends this rename to HardwareProfile and
NetworkAdapter, so generic hardware consistently uses `vendor`.
All old alpha source shapes are rejected, without duplicate aliases.

`ClusterNode.configuration.storage` holds partition tables and optional LVM.
Sources resolve to the selected bare-metal Server’s devices/volumes, or to a
local partition for PVs. Known ownership conflicts are errors. Without exact
placement, valid sources may be recorded, but availability to the node is
UNKNOWN; references do not infer placement. Physical-source storage configuration
is not supported for VM nodes in this pass.

Storage-local references expand in the registry before semantic validation.
There is no global name search or cross-scope fallback. Canonical/local spellings
of one target count as duplicates. Existing graph containment remains, augmented
with controller, backing-device, partition, and LVM relations using the existing
lowercase relation API. See the [model contract](model-v0alpha1.md) for exact
identities, relationships, field rules, and migration paths.

## Filesystem and mount-point policy boundary

Filesystem and mount-point intent is deliberately outside the v0alpha1
infrastructure core. The infrastructure model stops at the block-device/LVM
layer.

Filesystem creation, formatting, mount-point selection, fstab generation, and
equivalent NixOS filesystem declarations are configuration policy and belong
to future Ansible/Nix adapters. Partition `type` and `role`, and names such as
`lv_root`, do not imply filesystem or mount-point defaults. The conventional
mapping documented in the model guide is illustrative policy, never schema data.

## Device and network-interface extension decisions

Inspection of committed revision `ff0d48c` identified two scope conflicts before
editing. First, node-level image overrides were already accepted and documented,
despite this extension request describing them as future work. Their existing
schema and behavior are preserved without expansion. Second, Server had no
GPU/FPGA inventory collection and no generic interface type, feature evidence,
or adapter class. The extension adds the network/storage evidence fields and,
following review of the device example, a generic accelerator collection. It
retains existing storage and network inventory representations.

`requirements.devices` accepts GPU, FPGA, DPU, storage, and other independent
component intent. Count defaults semantically to 1 without inserting a value.
Vendor/model/features apply to all types; protocol/minCapacity apply only to
storage and other combinations fail schema validation. Each requirement has
kind `DeviceRequirement`, canonical path
`cluster/<cluster>/node/<node>/requirement/device/<name>`, and `contains` plus
`requires_device` edges from its ClusterNode. Grouping segments have no entities.

Storage requests match existing StorageDevices, not volumes or aggregate
capacity. DPU requests match NetworkAdapters with `class: dpu`, counting adapters
rather than ports. GPU/FPGA requests match `Server.spec.accelerators` entries of
the same type, using the existing tri-state constraint and count evaluator.
The broad `other` device requirement remains UNKNOWN because it has no generic
inventory mapping. Opaque provider payloads are not used as a substitute.

`Server.spec.accelerators` is optional and is a sibling of `storage` and
`networkAdapters`. Each entry requires `name` and `type` (`gpu`, `fpga`, `other`),
with optional `vendor`, `model`, `pciAddress`, and `capabilities.features`.
Names are unique within a Server's accelerator collection, regardless of type.
The embedded kind is `Accelerator`, with identity
`server/<server>/accelerator/<name>` and Server → Accelerator `contains` and
`has_accelerator` relationships. Existing Server-scoped PCI uniqueness applies.
DPU remains exclusively a network-adapter class, not an accelerator type.
No accelerator allocation, GPU memory, MIG, FPGA bitstreams, NUMA/device
affinity, per-device operating state, or provider-specific properties are added.

This correction intentionally breaks old v0alpha1 source documents using
`HardwareProfile.spec.manufacturer` or `Server.spec.networkAdapters[].manufacturer`.
Both must use `vendor`, matching StorageController, StorageDevice, Accelerator,
and device/interface constraints. No deprecated aliases or automatic migration
are provided. The API version stays v0alpha1; see the
[migration table](model-v0alpha1.md#source-model-migration).

NetworkAttachment `interfaceRequirements` stays an attribute object. Type is
required when the object is supplied. Optional adapter requirements require a
class; basic Ethernet needs no adapter object. Existing Interface gains optional
`type` and `capabilities.features`; NetworkAdapter gains optional `class` and
`features`; StorageDevice gains optional `features`. Existing bandwidth is
preserved; requested adapter/DPU `vendor` directly matches NetworkAdapter
`vendor`. Adapter features describe independently requested
DPU capability and do not imply features on every interface.

For component matching, supplied collections and supplied feature lists are
complete; empty lists explicitly mean none, while omission means unknown.
For GPU/FPGA requests, omitted `accelerators` is UNKNOWN, while an empty
collection or a collection lacking the requested type is UNSATISFIED.
Incomplete candidate fields remain unknown. A candidate with a known mismatch
cannot match; enough proven matches satisfy a request; too few candidates even
including unknown matches disprove it. No per-node or cross-node reservation,
count subtraction, or attachment/interface allocation follows from these checks.
Different requests can independently be satisfied by the same component; that
compatibility evidence alone does not establish grouping or allocation.

Feature names are unique normalized lowercase strings, with initial network
vocabulary `rdma` and `sriov`. Other generic names are allowed without changing
the schema. Uppercase/whitespace forms fail rather than being rewritten. The
core does not infer capabilities from model strings or expand provider aliases.

There is no binding between device requests and interface requests. Independent
DPU demand and a DPU-backed attachment are distinct intent; v0alpha1 neither
requires two descriptions of an ordinary NIC nor establishes whether two
requests use one physical component. Device/PCI/NUMA affinity and allocation
must be reviewed before introducing such cross-references.

The [model guide](model-v0alpha1.md#historical-fabric-topology-vocabulary) records
historical FABRIC mappings explicitly. The VM example keeps Cluster Rocky Linux
9 image, CPU/memory/storage demand, and addresses, and adds Ethernet attachment
requirements, layer2 Networks, and namespaced OpenStack roles. Provider service
and component choices remain future adapter responsibilities. The separate
device example proves GPU, FPGA, NVMe, and interface requirements from generic
inventory, with explicit GPU/FPGA counts of 1 and zero compatibility warnings.

All new source fields are optional additions. Previously valid documents remain
valid; no old field, canonical ID, relation, or schema/distribution version is
removed or renamed by the requirement additions. Hostnames and image
resolution/override policy remain future review items. Shared Network
gateway/DNS configuration is specified below.

## Node-local network adapter grouping

Optional `ClusterNode.networkAttachments[].adapterGroup` declares that members
of one explicit group must share an allocatable network adapter/component.
It uses the existing case-sensitive local-name syntax, has no default, and
permits single-member groups. It is separate from `interfaceRequirements.adapter`,
which describes adapter capability/identity selectors.

Keys are local to one ClusterNode: equal strings on different nodes are unrelated.
Different explicit values identify different requested groups and must not be
merged into one requested group. Omission declares no grouping relationship;
identical interface requirements do not imply sharing. Source and normalized
data preserve explicit keys and omission without synthesizing groups.

Core validation reports `adapter_group_conflict` only for conflicting explicit
adapter `class`, `vendor`, or `model` values within the same node/group. Partial
selectors are compatible when they do not contradict; speeds, features, and
other interface requirements need not be identical. Existing per-attachment
inventory compatibility checks remain independent. Providers must resolve joint
capabilities and sufficient interfaces; core adds no port-count knowledge,
hardware scheduling, allocation, or availability checks.

Groups are normalized attachment attributes, not globally addressable resources:
there is no new kind, canonical ID, or graph edge. Two grouped attachments remain
two logical Network endpoints. Network scope/connectivity, placement, and
independent device requirements are unchanged. Network capability continues to
belong in `interfaceRequirements`, without duplicating NIC requests under
`requirements.devices`.

The [model guide](model-v0alpha1.md#node-local-network-adapter-grouping) shows
grouped data/storage interfaces alongside ungrouped management. The generic model
describes WHAT network capability/topology is required; provider adapters decide
HOW provider resources realize it. Provider adapter implementations are unchanged
in this core increment.

## Shared Network default gateways and DNS

`Network.spec.prefixes` retains its existing strict IP/CIDR shape and describes
the Network's IP prefixes/subnets. Optional `defaultGateways` associates a
gateway `address` with a required `prefix` declared on that same Network.
Semantic IP-network comparison handles equivalent IPv6 spellings. The gateway
must use the prefix's IP family, but need not be contained in that prefix;
IPv6 link-local gateways and IPv4 gateways outside the prefix are valid intent.
At most one gateway per equivalent prefix is allowed in v0alpha1, so duplicate
definitions and competing gateways for that prefix are rejected.

Optional `dns` requires an ordered `servers` array of IPv4/IPv6 addresses,
allowing mixed families and empty lists. Duplicate addresses, including
semantically equivalent IPv6 spellings, are rejected. Resolver order and source
spellings remain intact; DNS addresses need not belong to a Network prefix.
The model guide includes a [dual-stack example with an IPv6 link-local gateway](model-v0alpha1.md#logical-networking-and-deployment-intent).

Omitted `defaultGateways` means unspecified gateway configuration;
`defaultGateways: []` intentionally means no default gateway. Omitted `dns`
means unspecified DNS; `dns: {servers: []}` intentionally means no DNS servers.
Neither validation nor normalized dictionary/graph views insert defaults or
convert omission into empty values. Missing configuration produces no warning.
These optional additions keep existing Network documents valid and create no
new canonical identities or graph relationships.

Network owns shared `prefixes`, `defaultGateways`, and `dns`. Node-specific host
addresses remain on `networkAttachments[].addresses`, alongside `networkRef`
and `interfaceRequirements`. No gateway/DNS fields or overrides are added to
attachments. The FABRIC-like example now gives management `192.0.2.1` as gateway
for `192.0.2.0/24` and `192.0.2.53` as DNS. Storage explicitly declares no gateway
and leaves DNS unspecified; all Cluster node addresses are preserved.

A future provider adapter can consume Network prefixes/default gateways/DNS
plus attachment addresses to render interface address/prefix, default gateway,
and DNS server configuration. Expanding shared configuration into provider-specific
per-interface settings is an adapter responsibility, not another core source
representation. This change includes no FABRIC adapter or FABlib calls.

Preserve this NIC adapter design rule:

> The generic model says what network capability is required; the provider
> adapter decides which provider-specific NIC/component model satisfies that
> requirement.

For FABRIC, ordinary Ethernet may initially map to `NIC_Basic`. Future adapter
design must also handle dedicated NIC components such as ConnectX-6 and a single
physical NIC/component exposing multiple interfaces. It must not assume
`one networkAttachment == one FABRIC NIC component`. No such mapping is implemented.

Arbitrary static routes, route metrics/priorities, multiple gateways per prefix,
ECMP, failover, policy routing, DHCP, DNS search domains, DNS priority, per-interface
or split DNS, DNS-over-TLS/HTTPS, and attachment gateway/DNS overrides are deferred.

## Multi-site Network scope and generic connectivity

This is an intentional v0alpha1 source incompatibility: replace
`Network.spec.siteRef: site/example` with `Network.spec.siteRefs: [site/example]`.
The old Network field is rejected without a compatibility alias. Cluster and
node placement fields remain singular. Omission of Network `siteRefs` leaves
scope unconstrained; a supplied list must contain at least one unique resolving
Site reference, so `siteRefs: []` is invalid. Scope lists declare allowed sites,
not an obligation to occupy every listed site. The topology uses `scoped_to`
edges from Network to each declared allowed Site. These edges do not assert
physical location or current realization. ClusterNode `targets_site` edges
represent effective desired node placement; `located_at` remains physical/inventory
location. No additional Network → Site edges are derived from endpoint sites.

Effective node site uses `ClusterNode.placement.siteRef` first, then the parent
`Cluster.spec.siteRef`, otherwise it is unresolved. Network scope validation
reuses that placement logic. Resolved sites outside the Network's scope fail;
unresolved placement retains existing diagnostics without an extra Network warning.

Optional `connectivity` distinguishes a shared multipoint domain from a
point-to-point domain with at most two logical endpoints. Omission means
multipoint semantically, with no schema default or inserted value. Multipoint
does not require three or more attachments. Point-to-point permits zero, one,
or two attachments; each NetworkAttachment counts once regardless of address
count, including dual-stack addresses. Connectivity is independent of layer.
No gateway/DNS, route, addressing, device, or interface requirement policy changes.

Effective network sites are the unique resolved effective sites of the endpoints
actually attached to the Network. No new persisted field or public helper is
needed: existing attachment and node `targets_site` topology queries expose the
necessary information. Provider adapters must use actual effective endpoint
sites for realization selection, not simply the length of `Network.siteRefs`.

Future FABRIC network service resolution will consider `Network.layer`,
`Network.connectivity`, effective endpoint sites, and resolved endpoint/interface
capabilities before choosing an appropriate service. Layer alone does not select
a service. Provider adapters may require exactly two endpoints or other stronger
realizability constraints for a particular service. No FABRIC adapter, service
selection, or provider service names are added to the generic schema.

The NIC bookmark above still applies: the generic model describes required
network capability; the provider adapter chooses the provider NIC/component
model. One attachment must not be assumed to equal one physical NIC.

The FABRIC-like example stays single-site. The separate
[multi-site example](../examples/multi-site/infrastructure.yaml) has one Cluster,
an inheriting VM, an overriding VM, and one layer2 multipoint Network spanning
the two allowed sites, with only the existing abstract-placement UNKNOWN warnings.

## Intentionally deferred

- Filesystem definitions, creation/formatting/provisioning, mount points, fstab,
  NixOS filesystem configuration, Ansible storage roles, and Ansible/Nix renderers.
- Live FABRIC operations; Redfish, Dell/iDRAC, AMD firmware, NetBox, Chameleon, and FIM adapters.
- Neo4j backend, FIM NetworkService, and resource delegation concepts.
- Provider discovery, provisioning, lifecycle reconciliation, and persistence.
- Effective-site derivation from VM `hostRef → Server → Site` or bare-metal
  `resourceRef → Server → Site`; neither reference currently supplies a missing site.
- CPU pinning, Linux logical CPU IDs, affinity masks/cpusets, VM NUMA, huge pages,
  NUMA-aware scheduling, and scheduler topology.
- RAID capacity/parity calculations, stripe size, cache policy, rebuild policy,
  hot-spare scheduling, hot spares, and drive rebuild state.
- Disk encryption, mdraid, multipath, and explicit partition start/end sectors.
- LVM thin provisioning/pools, snapshots, RAID LVs, cache volumes, mirroring,
  PE-size tuning, tags, and activation policies.
- Scheduling, VM overcommit, reservations, aggregate capacity accounting,
  partition/LVM allocation math, source exclusivity across tables/whole devices,
  physical port occupancy, and proof of live availability.
- Broader `other` device requirement mapping, attachment-to-NIC binding, network services,
  static routes, routing policy, gateway/DNS rendering, DHCP, and address allocation.
- Accelerator allocation, GPU memory requirements, per-accelerator operating state,
  PCI placement, NUMA/device affinity, GPU partitioning/MIG, FPGA bitstreams,
  DPU OS configuration, RDMA configuration, SR-IOV VF allocation, guest interface
  names, and NetworkManager connection names.
- Hostname policy and node image override policy review (existing node `image`
  remains accepted); postboot provisioning and SELinux/OS configuration policy.
- Image catalogs, role execution, OS compatibility, and extension-specific validation.
- Export/save, migration tooling, schema version negotiation, and elaborate Python
  domain classes while field shapes remain alpha.

No Git commit is created as part of this implementation.

## Provider-neutral exact VM host placement

`ClusterNode.placement.hostRef` uses the existing canonical Server-reference
syntax and core reference resolver. It identifies the exact physical
Server/hypervisor desired for a VM. `placement.siteRef` remains desired site
placement; `placement.resourceRef` identifies an infrastructure resource realized
directly, currently bare metal. Host and resource references cannot coexist.
Non-VM host requests fail semantic validation; unknown references and kind
mismatches use the normal core diagnostics.

Host consistency checks compare `Server.spec.siteRef` to the existing effective
site: node override, then Cluster default, otherwise unresolved. Neither host nor
bare-metal resource references derive an effective site. Both possible derivations
remain future design questions. Network scope behavior therefore remains unchanged,
including for host-only VMs whose site is unresolved.

The graph adds `ClusterNode --targets_host--> Server` for desired exact VM host
placement. Existing `targets_site`, bare-metal `placed_on`, Network `scoped_to`,
and inventory `located_at` relationships retain their meanings. Host identity
does not imply runtime placement or prove compatibility/available VM capacity;
the existing UNKNOWN compatibility behavior remains. This change adds no provider
adapter behavior, hypervisor scheduling, or observed host state.

## Offline FABRIC adapter v0.1

The offline adapter lives in `infra_model.adapters.fabric`, separately from core
schema, reference resolution, and placement semantics. Its public API is
`FabricAdapterConfig`, `FabricTranslationError`, and `render_fabric()`. The
translator resolves supported intent into private immutable plan records; the
renderer formats the historical fabric-generic-cluster dictionary. CLI YAML goes
to stdout, with warnings/errors on stderr. No live provider operations occur.

Omitted architecture and explicit x86_64 are accepted as compatibility conditions.
No architecture output field is added. Other explicit architectures, CPU frequency,
and unsupported storage constraints fail. Unconsumed namespaced extensions are
opaque metadata: tolerated, uninterpreted, omitted from target output, and preserved
in source. The adapter assigns no semantics to `fabric.example/intent`. These rules
allow the unchanged FABRIC-like example to render with its architecture and experiment
extension intact. Actual unsupported generic core intent still fails explicitly.

Optional `FabricAdapterConfig.workers` maps canonical Server references to nonempty
FABRIC worker identifiers. VM `placement.hostRef` must have an explicit mapping
when rendered; its mapped value becomes historical `worker`. With no hostRef the
private node plan stores `None` and the renderer emits `worker: ''`. Unused mappings
are allowed, and there is no defaultWorker or implicit mapping from Server names,
Sites, inventory, or extensions.

The exact-VM-host decisions above remain authoritative. Core validation resolves
host references and checks host/site consistency. The adapter uses the existing
node-site override/Cluster-default rule without deriving Site from hostRef or
worker mappings. Identity translation neither chooses hosts nor proves capacity,
availability, or schedulability. Live worker discovery remains deferred.

The v0.1 contract uses actual mapped endpoint sites to select `L2Bridge` for
one site and `L2STS` for two, independently of Network.siteRefs length. Plain
Ethernet maps to NIC_Basic. Dedicated NICs, device requirements, layer3,
point-to-point, excessive prefix/address/DNS multiplicity, and unrepresentable
capacities fail explicitly. Components contain interface tuples; a network
attachment is not universally equivalent to a physical NIC/component.

See the [adapter guide](fabric-adapter.md) for configuration, CLI usage, and the
complete offline rendering contract.

Manual downstream acceptance confirmed that generated `_slice_topology_*.yml`
documents were accepted by the existing fabric-generic-cluster workflow without
hand modification. This records structural/parser compatibility for the documents
tested, separately from automated test results; see the
[acceptance note](fabric-adapter.md#manual-downstream-acceptance) for the verified
path and the limits of this evidence.

## Offline FABRIC dedicated NIC realization v0.2

The adapter now resolves exact generic `ConnectX-6` identity to `NIC_ConnectX_6`,
with vendor omitted or exactly `NVIDIA`. Core's required adapter class remains
`standard` for this resolver; SmartNIC/DPU and other classes are unsupported.
No fuzzy matching, vendor-only model inference, or configurable NIC-model mapping
is introduced. Generic schema, validation, and public adapter config are unchanged.

Ungrouped dedicated attachments each create a separate component. For explicit
node-local `adapterGroup`, the translator first collects all members and merges
compatible identity selectors, then resolves one shared component. Partial
selectors or members without adapter identity are accepted when the group
identifies ConnectX-6. Identical requirements never imply grouping; equal group
strings on different nodes are independent, and different groups stay separate.

This FABRIC component supports one or two grouped interfaces; more than two fail
without splitting the group. The limit belongs solely to provider realization.
Groups without a supported dedicated model fail rather than falling back to
separate NIC_Basic components or inventing a multi-interface NIC_Basic. Plain
ungrouped Ethernet behavior is unchanged.

Private component plans continue to contain interface tuples. Each interface now
carries its authored node-global attachment ordinal. Components are numbered by
first creation, interfaces within each component by member order, and guest
`ethN`/`conn-ethN` by original attachment order even when groups are interleaved.
Bindings and address/prefix/gateway/DNS data remain independent per interface.

The existing L2Bridge/L2STS resolver is unchanged. Speed/features, RDMA, SR-IOV,
automatic component selection, other dedicated models, devices, L2PTP, layer3,
live discovery, availability checks, and submission remain deferred. The generic
model describes WHAT is required; the FABRIC adapter decides HOW it is realized.

A provider-neutral [fixture](../tests/fixtures/fabric-dedicated-nic.yaml) exercises
ungrouped management and grouped data/storage using existing Site/image mappings.
Keeping it outside the recursively loaded examples avoids duplicate Site identity.
The v0.1 manual acceptance record above is unchanged; it is not evidence of
downstream acceptance or provisioning of this new dedicated-NIC realization.

## Offline FABRIC L2PTP v0.1

Generic point-to-point semantics and core schemas remain unchanged: zero, one,
or two NetworkAttachments are valid partial intent. The FABRIC adapter requires
an explicit layer2 point-to-point Network with exactly two rendered attachments
in the selected Cluster, exactly two distinct effective FABRIC sites after
mapping, and supported dedicated interfaces at both endpoints. Currently only
resolved NIC_ConnectX_6 interfaces qualify; NIC_Basic does not. Successful
eligibility produces `L2PTP`, never an automatic multipoint downgrade or NIC upgrade.

The translator now resolves private component/endpoint realization records before
Network service selection. The resolver reads each endpoint's mapped site and
resolved component, including identity supplied by another adapterGroup member.
Component resolution is not repeated in the renderer. Existing private render
plan fields, public API/config, and deterministic node, Network, component,
interface, and guest naming rules remain unchanged.

Endpoint counts are logical NetworkAttachments, not adapter groups/components.
A grouped ConnectX-6 may use its other interface for another Network; the whole
component need not be exclusive to L2PTP. A selected Cluster containing only one
of two global endpoints fails with an explicit completeness diagnostic. Networks
used exclusively by other Clusters are not selected. Completely unattached
Networks, including layer2 point-to-point declarations, are also unselected and
ignored. Only Networks referenced by the selected Cluster undergo provider
eligibility checks; a selected one-endpoint point-to-point Network still fails.
Multipoint Network selection and L2Bridge/L2STS service behavior remain unchanged.

This first provider realization is addressless/topology-only because L2PTP
traffic arrives VLAN-tagged and guest VLAN subinterfaces are not yet modeled or
rendered. Any declared Network prefixes/defaultGateways/dns or endpoint addresses,
including explicit empty configuration, fail with a VLAN/IP support diagnostic.
Output has no subnet, IP-family blocks, VLAN IDs, or VLAN configuration. These
are adapter restrictions, not new generic point-to-point rules.

Future design: model/render guest VLAN subinterfaces for tagged L2PTP traffic.
No static IP handling, VLAN allocation, live discovery, availability checks, or
submission is added. The [fixture](../tests/fixtures/fabric-l2ptp.yaml) uses existing
Site/image mappings and provider-neutral source intent. Earlier manual downstream
acceptance does not establish acceptance or runtime correctness for L2PTP.

## Offline FABRIC GPU, FPGA, and NVMe realization

The adapter consumes existing `requirements.devices` without changing core
schema/validation, public adapter API, or Site/image/worker configuration.
GPU models map exactly: `Tesla T4` → `GPU_TeslaT4`, `RTX6000` → `GPU_RTX6000`,
`A30` → `GPU_A30`, and `A40` → `GPU_A40`. Vendor may be omitted or exactly
`NVIDIA`; `GPU_Tesla_T4` is not a valid emitted identifier.

FPGA `U280` and explicit product alias `Alveo U280` map to `FPGA_Xilinx_U280`;
`SN1022` maps to `FPGA_Xilinx_SN1022`. Both accept vendor omitted or explicit
`AMD`/`Xilinx` identities. These are explicit resolver entries, not fuzzy matching.
FPGA components are allocated without the optional downstream interface bindings.

NVMe requires `type: storage`, `constraints.protocol: nvme`, and exact model
`P4510`, with vendor omitted or `Intel`, and maps to `NVME_P4510`. Generic storage
alone is insufficient. This does not consume root-disk capacity, persistent
volumes, or filesystem/mount intent. Nonempty device features and `minCapacity`
fail until a capability/capacity resolver exists. DPU/BlueField and `other` device
requirements still fail explicitly. Unsupported vendors/models never fall back.

The translator resolves each selected node's requirements and expands existing
count semantics, using one when omitted. It creates private immutable device
records with generated name and resolved model in per-node GPU/FPGA/NVMe tuples.
Within each family, authored request order is retained and counts expand in place.
Names are `gpuN`, `fpgaN`, and `nvmeN`, independently numbered per node/family;
they cannot collide with existing `nicN` component names. The renderer emits each
record as `{name, model}`, using the same name for its map key, with no `count`.
Unrequested families and DPU remain `{}`. Network/NIC/service behavior is unchanged.

Compatibility is based on the supplied downstream review: fabric-generic-cluster
1.0.20, branch `main`, revision `802a91190528c4e9d2f238464ee1bbcdd654ec97`, and
inspected fabrictestbed-extensions/FABlib 1.9.5, fabrictestbed 1.9.0, fabric-fim 1.9.0.
The downstream dependency is not exactly pinned, so this is a version-specific
compatibility context rather than a timeless FABRIC contract.

The reviewed downstream parser is permissive; deployment helpers may catch
component-add errors and continue. Offline translation must validate supported
models itself to avoid knowingly emitting intent that could be dropped downstream.
Map keys and inner names must agree because probing and deployment use different
ones. SN1022 is allocatable in the inspected APIs, but downstream host discovery
does not reliably include its requirements in candidate-host filtering. No live
workaround or availability claim is added.

GPU/FPGA/NVMe may require project permissions and site availability; these remain
runtime concerns. The adapter performs no discovery, permission checks, scheduling,
or submission. The [PCI fixture](../tests/fixtures/fabric-pci-devices.yaml) is
provider-neutral intent with count expansion and ordinary management Ethernet;
it requires no live hardware. Existing manual acceptance does not establish
downstream provisioning success for these newly rendered devices.

## Offline FABRIC allocation-only DPU realization

Independent `requirements.devices[type=dpu]` now resolves exact model identities:
`BlueField-2-ConnectX-6` → `NIC_BlueField_2_ConnectX_6`, `ConnectX-7-100` →
`NIC_ConnectX_7_100`, and `ConnectX-7-400` → `NIC_ConnectX_7_400`. Vendor may be
omitted or exactly `NVIDIA`; other explicit vendors fail. There are no shorthand
aliases or fuzzy matching. In particular, `BlueField-3` cannot choose between the
100G and 400G forms associated with that generation in the inspected FIM catalog.
Missing models and nonempty features fail; empty/omitted features are allowed.
Core continues to forbid DPU protocol/minCapacity constraints.

The private node plan gains a `dpus` tuple of existing immutable device records.
Counts expand in authored order into independent per-node `dpuN` names, using one
when omitted. Each `pci.dpu` entry contains exactly `name` and `model`, with map
key equal to inner name and no count or interfaces. No demand keeps `dpu: {}`.
This uses the downstream DPU parser's optional interfaces and allocation path
through `add_dpus_to_node()` and `Node.add_component(name, model)`.

DPU NetworkAttachments remain unsupported even when a device request matches
their name/vendor/model or they use adapterGroup. Existing core intent provides
no explicit relationship between the two requests. No implicit binding, merging,
or deduplication is introduced. Core schema/validation, network plans/services,
GPU/FPGA/NVMe resolution, configuration, and public API remain unchanged.

Compatibility follows fabric-generic-cluster 1.0.20, `main` revision
`802a91190528c4e9d2f238464ee1bbcdd654ec97`, FABlib 1.9.5, fabrictestbed 1.9.0, and
FIM 1.9.0. The dependency is not exactly pinned; these are reviewed-version facts.
The adapter resolves supported models itself because downstream parsing is
permissive and deployment can swallow component-add exceptions. BlueField-2 is
allocatable in the inspected APIs, but host discovery lacks the equivalent
filtering coverage of the two ConnectX-7 DPU forms. Offline rendering makes no
host-discovery guarantee and adds no live workaround.

The [DPU fixture](../tests/fixtures/fabric-dpu.yaml) requests two independent DPUs
with ordinary NIC_Basic management. Allocation implies no ARM OS, RShim, BMC,
tmfifo_net0, management addressing/topology, bundle installation, operating mode,
or DPU-side configuration. FABlib's reviewed `Component.configure()` behavior
does not prove automatic ARM initialization downstream. Permissions, inventory,
availability, host selection, scheduling, and submission remain runtime concerns.
Earlier manual acceptance does not establish provisioning for this increment.

Future design: an explicit node-local relationship between a requested DPU device
instance and NetworkAttachments/adapterGroup if unified DPU networking is desired.

## Offline FABRIC dedicated ConnectX-5 realization

The existing generic selector `interfaceRequirements: {type: ethernet, adapter:
{class: standard, vendor: NVIDIA, model: ConnectX-5}}` now resolves exactly to
`NIC_ConnectX_5`. Vendor may be omitted; an authored adapter still requires class
`standard`. Other explicit vendors, aliases, fuzzy spellings, and vendor-only
model inference remain unsupported. The resolver uses a static map for
ConnectX-5 and ConnectX-6, with no core schema or validation changes.

ConnectX-5 reuses the existing ConnectX-6 adapterGroup semantics: compatible
selectors merge within a node-local group, including omitted vendor alongside
explicit NVIDIA. Contradictory models or vendors fail core validation. One or
two grouped attachments create one component; more than two fail without
splitting. Ungrouped attachments and distinct groups remain separate even when
their requirements match. The limit is provider-specific, not a core group rule.

Existing private network component/interface plans and the renderer are reused.
Component numbering follows first creation across NIC_Basic, ConnectX-5, and
ConnectX-6. Interfaces retain independent binding/address data and authored
attachment ordinals for ethN/conn-ethN. Target pci.network entries retain matching
map key/inner name and the existing name/model/interfaces shape.

Plain ungrouped Ethernet retains implicit NIC_Basic. L2Bridge/L2STS behavior is
unchanged; L2PTP eligibility remains NIC_ConnectX_6-only and rejects ConnectX-5,
including identity inherited from another group member. Other device mappings,
including A30 and BlueField-2-ConnectX-6, public API/config, placement mappings,
FABNet, and PTP time-service semantics are unchanged.

Compatibility follows the read-only audit of fabric-generic-cluster 1.0.20,
FABlib/fabrictestbed-extensions 1.9.5, fabrictestbed 1.9.0, and FIM 1.9.0.
NIC_ConnectX_5 maps to FIM SmartNIC_ConnectX_5 and exposes two provider ports.
The [focused fixture](../tests/fixtures/fabric-connectx5.yaml) exercises ordinary
management plus grouped ConnectX-5 data/storage. Offline rendering does not
establish current availability or provisioning success; no inventory-aware
preflight, discovery, scheduling, or downstream package changes are introduced.
