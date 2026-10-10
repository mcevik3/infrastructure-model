# v0alpha1 implementation decisions and review items

This document records the targeted architecture-review corrections for v0alpha1.
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
Different requests can independently be satisfied by the same component.

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

Existing v0.1 limits remain: actual mapped endpoint sites select `L2Bridge` for
one site and `L2STS` for two, independently of Network.siteRefs length. Plain
Ethernet maps to NIC_Basic. Dedicated NICs, device requirements, layer3,
point-to-point, excessive prefix/address/DNS multiplicity, and unrepresentable
capacities fail explicitly. Components contain interface tuples; a network
attachment is not universally equivalent to a physical NIC/component.

See the [adapter guide](fabric-adapter.md) for configuration, CLI usage, and the
complete offline rendering contract.
