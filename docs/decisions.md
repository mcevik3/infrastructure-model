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
| Reference syntax | Full canonical IDs with case-sensitive names; paths use kind-specific segments | Confirm case sensitivity and allowed name characters. |
| Embedded fields | `networkAdapters`, `interfaces`, `storageDevices`, `nodes`, `networkAttachments`; embedded entities have `name` directly | Confirm field shapes; embedded objects do not add another `metadata/spec` envelope. |
| Component uniqueness | Names unique within each collection under a parent; adapter/storage names may coincide | Canonical IDs retain kind-specific segments, including the existing `storage-device` segment. |
| Profile inheritance | Profile capabilities supply defaults; actual resource values are authoritative. Mappings merge recursively, lists replace, omitted values inherit | Components/status are not inherited. No delete/unset operation or partial quantity overlay is provided. |
| Exact resource placement | `placement.resourceRef` selects an existing `server/<name>` only for `realization.type: baremetal` | VM exact references are rejected. VM host affinity may use namespaced extensions pending a future generic concept. |
| Realization | `realization.type` is `vm` or `baremetal`; inventory `capabilities.nodeTypes` uses the same spellings | Explicit inventory advertisement is retained; Server type alone does not imply allocatability. Node-level legacy placement/type fields are rejected. |
| CPU semantics | Inventory sockets/cores/threads are physical. Requirements use `compute.cpu: {count, unit}` with `vcpu`, `core`, or `thread` | Only exact bare-metal core/thread comparison is supported. Physical inventory never proves vCPU capacity; allocation/overcommit evidence is outside this version. |
| Storage vocabulary | Independent optional `medium` and `protocol`; NVMe SSD is `ssd` plus `nvme` | `media` and `mixed` are removed. Unknown composition is omitted. No storage topology expansion. |
| Compatibility | SATISFIED proves a requirement is met; UNSATISFIED proves it is not; UNKNOWN lacks enough generic evidence | Known failures are errors; UNKNOWN yields warnings. Site-only/unplaced nodes get one UNKNOWN warning each; successful validation is not proof of deployability. |
| Placement scope | Compare each node independently; no capacity subtraction, reservations, or exclusive bare-metal binding | Confirm later admission/allocation policy separately from this static model. |
| Quantities | SI and IEC byte units; integral physical sockets/cores/threads and explicit CPU requirement counts; frequency and bandwidth units; all values strictly positive | Confirm whether zero quantities, alternate spellings, overcommit ratios, or fractional CPU requests are needed later. |
| PCI scope | Uniqueness within a Server across adapters and storage devices; absent domain means `0000`; hex is case-insensitive | A global uniqueness check would incorrectly reject ordinary PCI address reuse on different machines. |
| MAC scope | Uniqueness across every modeled inventory Interface, case-insensitive | Intentional shared virtual/anycast MACs are outside this physical inventory pass. |
| Address shape | Attachment `addresses` are plain IPv4/IPv6 literals; Network `prefixes` use strict CIDR prefix-length notation, rejecting dotted netmasks/hostmasks | Confirm whether future addresses should include per-address metadata or allocation modes. |
| Network prefix semantics | An address must belong to a prefix of its referenced Network; an address with no declared prefix fails | Overlapping prefixes, duplicate IPs, gateways, reserved/broadcast addresses, and routing policies are not validated. |
| Site inheritance | Node `placement.siteRef` overrides the Cluster default; exact resources must match the effective site when one is supplied | Network site does not restrict attachments; cross-site connectivity is not inferred. |
| OS image and roles | Cluster image supplies descriptive default intent; nodes may override image; roles are free strings | Image resolution, OS suitability, role definitions, and configuration generation are deferred. |
| Extensions/status | Generic status fields are `availability`, `operationalState`, `powerState`, `observedAt`, and namespaced `extensions`; shared across top-level and embedded entities | Provider transient/discovered state belongs under `status.extensions`. Payloads stay opaque; no provider-specific status schemas. |
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

## Intentionally deferred

- FABRIC, Chameleon, FIM, Redfish, and NetBox adapters.
- Ansible and Nix renderers; Neo4j backend.
- FIM NetworkService and resource delegation concepts.
- Provider discovery, provisioning, lifecycle reconciliation, and persistence.
- Scheduling, VM overcommit policy, resource reservation, aggregate capacity accounting, physical port
  occupancy, and proof of live availability.
- Additional component families, accelerators, storage topology, attachment-to-NIC
  binding, network services, routing, and address allocation.
- Image catalogs, role execution, OS compatibility, and extension-specific validation.
- Export/save, migration tooling, schema version negotiation, and elaborate Python
  domain classes while field shapes remain alpha.

No Git commit is created as part of this implementation.
