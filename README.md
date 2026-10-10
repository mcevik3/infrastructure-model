# Infrastructure Model v0alpha1

A first reference implementation of a provider-neutral infrastructure model.
Human-authored YAML describes physical inventory and logical deployment intent.
JSON Schema and semantic checks validate it; a dictionary API exposes a normalized
model and topology. This alpha implements the design in the project request;
provisional field shapes and unresolved policies are recorded in
[decisions](docs/decisions.md).

Requires Python 3.11 or newer. The schema API version `v0alpha1` and Python
distribution version `0.1.0a1` are separate version domains. The current
`infra.model` API namespace is provisional.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
infra-model validate examples/
python -m pytest
```

Expected example result:

```text
Valid: 24 documents, 78 entities, 4 warnings
```

The two FABRIC-like and two multi-site VM nodes produce UNKNOWN warnings because
site placement and requirements alone do not prove resource compatibility. The focused device
example proves its GPU, FPGA, NVMe, and interface requirements with zero warnings.

The CLI accepts one file or recursively loads `.yaml` and `.yml` files from a
directory. Files may contain multiple documents separated by `---`. References
resolve across the entire loaded set, regardless of file order. Exit codes are
`0` for valid models (possibly with warnings), `1` for load or validation errors,
and `2` for invalid command usage. Diagnostics include a filename, document
number, and field path where available. `python -m infra_model validate <path>`
is an equivalent entry point.

```python
from infra_model import InfrastructureModel, ValidationError

model = InfrastructureModel.load("examples/")
report = model.validate()
server = model.get("server/amst-w2")
servers = model.servers()
networks = model.networks()
topology = model.topology

interface_id = "server/amst-w2/network-adapter/slot2/interface/p1"
peers = topology.neighbors(interface_id, relation="physical_link")
nodes = model.of_kind("ClusterNode")
capabilities = model.effective_capabilities("server/amst-w2")
```

`load()` parses YAML; `validate()` performs all checks and returns a
`ValidationReport`. Queries also validate before exposing data. Validation
failures raise `ValidationError` with structured `.issues`; malformed YAML and
unreadable input raise `LoadError`. A successful report's `.warnings` identifies
checks that lack generic evidence (UNKNOWN). Known satisfied requirements are
SATISFIED; known failures are UNSATISFIED errors. Unplaced intent is valid without
matching inventory, with an UNKNOWN placement warning per node. Successful validation does
not prove deployability.

Returned resources are defensive copies of normalized dictionaries, with
canonical `id` fields and embedded `kind` fields. They are query results, not
source documents for reloading. The model does not provide mutation or save
operations. `InfrastructureModel.from_documents([...])` accepts source
dictionaries for applications that already parsed their input.
`InfrastructureModel.load(...)` and `InfrastructureModel.from_documents(...)`
are the supported public construction APIs; direct construction from internal
`SourceDocument` objects is implementation behavior.

The topology API offers `nodes(kind=None)`, `edges(relation=None)`, and
`neighbors(id, relation=None, direction="out")`. It returns plain dictionaries
and IDs. A NetworkX `MultiDiGraph` is private to the implementation.

Examples are illustrative, not discovered inventories or provider adapter output:

| Directory | Contents |
| --- | --- |
| [chameleon-like](examples/chameleon-like/inventory.yaml) | UC, compute-cascadelake-r, P3-CPU-004 |
| [amst](examples/amst/inventory.yaml) | AMST, Dell R7525 profile, amst-w2, switch, physical Link |
| [baremetal](examples/baremetal/infrastructure.yaml) | Dual-socket EPYC, SMT/NUMA, controller/disks/RAID volumes, GPT and LVM with exact bare-metal placement |
| [fabric-like](examples/fabric-like/cluster.yaml) | Layer2 management/storage Networks, shared management gateway/DNS, explicitly no storage gateway, Rocky Linux 9 VMs, namespaced OpenStack roles, Ethernet requirements, static IPv4 attachments |
| [multi-site](examples/multi-site/infrastructure.yaml) | One Cluster with a default site, one inheriting VM, one VM overriding its site, and a shared layer2 multipoint Network |
| [device-requirements](examples/device-requirements/infrastructure.yaml) | GPU/FPGA inventory and intent, two NVMe devices, 100 Gbps Ethernet/RDMA SmartNIC attachment, exact placement with zero warnings |

Each example directory also validates independently. Inventory capacities and
identifiers are illustrative. Provider names occur only in example descriptions
and namespaced extension payloads, never as core fields or enums.

The normative implementation contract is documented in
[model-v0alpha1.md](docs/model-v0alpha1.md). The eight JSON Schema files live in
[schema/v0alpha1](schema/v0alpha1); `common.json` contains embedded entity types,
including ClusterNode. The build packages those same files as
`infra_model._schemas`, so installed wheels validate offline without finding a
repository checkout or downloading schemas.

An [offline FABRIC adapter](docs/fabric-adapter.md) renders supported VM intent
into historical fabric-generic-cluster YAML. No live provider operations,
provisioning engine, scheduling, aggregate allocation, resource delegation,
FIM NetworkService, or graph database integration are implemented.
Exact placement checks compare each node with declared capabilities;
they do not prove live availability, image compatibility, routing, or deployability.

ClusterNode execution form is `realization.type: vm` or `baremetal`. CPU demand
uses `requirements.compute.cpu: {count: 4, unit: vcpu}` (`core` and `thread`
express physical demand). `placement.resourceRef` selects an existing Server
only for bare metal. VM `placement.hostRef` optionally selects the exact physical
Server/hypervisor requested to host the VM; it is mutually exclusive with
`resourceRef`. `placement.siteRef` remains desired site placement, overriding the
Cluster default. An exact host must match that effective site when supplied.
Neither `hostRef` nor `resourceRef` derives a missing effective site; both possible
derivations remain deferred. Host identity does not prove VM capacity compatibility
or availability, and physical cores do not prove vCPU capacity.
The graph uses `targets_host` for desired VM host placement, `targets_site` for
effective desired site placement, and `located_at` for physical inventory location.
Storage uses independent optional `medium` and
`protocol` fields. Provider observations belong in `status.extensions`.

Server CPU inventory lives under `spec.capabilities.compute.cpu`: integer
`sockets`, total physical `cores`, and total supported hardware `threads`, plus
optional `topology`, `smt`, and `numa`. Disabling SMT does not change `threads`.
Profiles provide defaults and CPU support information; Server state is authoritative.

Hardware storage lives under `Server.spec.storage` (`controllers`, `devices`,
`volumes`). Block-device intent lives under `ClusterNode.configuration.storage`
(`partitionTables`, optional `lvm`). Local references normalize to canonical IDs,
and the topology exposes controller, device, partition, PV, VG, and LV relationships.
Filesystem and mount-point policy is deliberately outside v0alpha1; the core
stops at block devices and LVM.

Server accelerator inventory lives alongside storage and network adapters in
`spec.accelerators`. Entries require `name` and `type` (`gpu`, `fpga`, `other`),
with optional `vendor`, `model`, `pciAddress`, and `capabilities.features`.
DPU inventory remains under `networkAdapters` with `class: dpu`.

This alpha evolution removes the old `spec.storageDevices` collection and moves
inventory CPU counts from `compute.{sockets,cores,threads}: {value, unit}` to
`compute.cpu.{sockets,cores,threads}: <integer>`. Generic hardware identity now
consistently uses `vendor`: `manufacturer` is rejected on HardwareProfile,
NetworkAdapter, and StorageDevice, with no backward-compatible aliases.
See the [migration notes](docs/model-v0alpha1.md#source-model-migration) for the
complete compatibility and canonical identity contract.

Nodes can request independent hardware with `requirements.devices` (`gpu`,
`fpga`, `dpu`, `storage`, `other`). Each entry has a unique name and defaults to
one component; NVMe is `type: storage` with `constraints.protocol: nvme`.
Logical network attachments can request `interfaceRequirements`, from basic
`{type: ethernet}` to speed, features (`rdma`, `sriov`), and adapter class.
Exact placement checks use Server accelerators for GPU/FPGA requirements, storage
devices for storage requirements, and network adapters for DPU requirements;
they do not allocate components or bind attachments to physical interfaces.
Omitted inventory collections mean not reported/UNKNOWN; explicitly empty lists
mean known to contain none and make requirements for that family UNSATISFIED.
See [device and interface requirements](docs/model-v0alpha1.md#device-and-interface-requirements)
for fields, evidence rules, and the historical FABRIC vocabulary mapping.

`Network.spec.siteRefs` is an optional allowed site scope: a nonempty list of
unique canonical Site references that must resolve. Omission means unconstrained
by the Network; `siteRefs: []` is invalid. Authored order is preserved but has no
semantic significance, and listed sites need not all be occupied. This intentionally
replaces `Network.spec.siteRef` in v0alpha1, with no compatibility alias.
`Cluster.spec.siteRef` remains the default placement site for ClusterNodes;
`ClusterNode.placement.siteRef` is the per-node placement override. Attached nodes
with a resolved effective site must fall within the Network's declared scope.
Unresolved node sites add no redundant Network warning.

Topology uses Network `scoped_to` edges for declared allowed scope, ClusterNode
`targets_site` edges for effective desired placement, and `located_at` for
physical/inventory location. Network scope edges do not assert current realization;
no additional Network → Site edges are derived from endpoint placement.

Optional Network `connectivity` is `multipoint` (a shared connectivity domain)
or `point-to-point` (at most two NetworkAttachment endpoints, including zero or
one). Omission means multipoint semantically, without a schema default or inserted
value; multipoint does not mean three or more. These semantics apply to both
layers, and address counts do not change endpoint counts. Effective network sites
are the unique resolved effective sites of attached endpoints, not the declared
`siteRefs` list. Future provider adapters must use those actual sites for network
realization and may impose stronger realizability constraints, such as exactly
two endpoints. FABRIC service selection will consider layer, connectivity, effective
endpoint sites, and resolved endpoint/interface capabilities. The generic model
describes required network capability; the adapter chooses the NIC/component
model and must not assume one attachment equals one physical NIC.

Networks can declare shared `defaultGateways` (one per declared prefix) and
ordered `dns.servers`. Gateway families must match their prefixes; gateway
containment is not required, allowing IPv6 link-local gateways. Omitted fields
are unspecified, while explicit empty lists mean no gateways or DNS servers.
Node-specific addresses remain on network attachments. See
[logical networking](docs/model-v0alpha1.md#logical-networking-and-deployment-intent)
for the dual-stack example, validation rules, and future adapter consumption.

Offline FABRIC rendering uses a separate identity-mapping configuration:

```sh
infra-model render fabric examples/fabric-like/ --config adapter-configs/fabric.yaml
infra-model render fabric examples/multi-site/ --config adapter-configs/fabric.yaml
```

The adapter exposes `FabricAdapterConfig`, `FabricTranslationError`, and
`render_fabric()` from `infra_model.adapters.fabric`. It renders plain Ethernet
VMs with exact image/capacity mappings, using actual endpoint sites to select
`L2Bridge` or `L2STS`. Unsupported intent fails explicitly. YAML goes to stdout;
warnings and errors go to stderr. The [config example](adapter-configs/fabric.yaml)
lives outside the generic examples tree so recursive validation is unchanged.
See the [adapter guide](docs/fabric-adapter.md) for limits, the private rendering
pipeline, and optional `workers` identity mappings for VM `placement.hostRef`.
The adapter accepts x86_64 requirements and tolerates opaque namespaced extensions;
the FABRIC-like source example and generic core semantics remain unchanged.
