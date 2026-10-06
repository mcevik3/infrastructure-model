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
Valid: 16 documents, 55 entities, 2 warnings
```

The two FABRIC-like VM nodes produce UNKNOWN warnings because site placement
and requirements alone do not prove resource compatibility.

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
| [fabric-like](examples/fabric-like/cluster.yaml) | Site, management/storage Networks, Rocky Linux VM nodes, roles, static IPv4 attachments |

Each example directory also validates independently. Inventory capacities and
identifiers are illustrative. Provider names occur only in example descriptions
and namespaced extension payloads, never as core fields or enums.

The normative implementation contract is documented in
[model-v0alpha1.md](docs/model-v0alpha1.md). The eight JSON Schema files live in
[schema/v0alpha1](schema/v0alpha1); `common.json` contains embedded entity types,
including ClusterNode. The build packages those same files as
`infra_model._schemas`, so installed wheels validate offline without finding a
repository checkout or downloading schemas.

No provider adapters, provisioning engine, scheduling, aggregate allocation,
resource delegation, FIM NetworkService, or graph database integration are
implemented. Exact placement checks compare each node with declared capabilities;
they do not prove live availability, image compatibility, routing, or deployability.

ClusterNode execution form is `realization.type: vm` or `baremetal`. CPU demand
uses `requirements.compute.cpu: {count: 4, unit: vcpu}` (`core` and `thread`
express physical demand). `placement.resourceRef` selects an existing Server
only for bare metal. VMs use site placement and requirements; physical cores
do not prove vCPU capacity. Storage uses independent optional `medium` and
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

This alpha evolution removes the old `spec.storageDevices` collection and moves
inventory CPU counts from `compute.{sockets,cores,threads}: {value, unit}` to
`compute.cpu.{sockets,cores,threads}: <integer>`. Storage device `manufacturer`
is now `vendor`; other component/profile manufacturer fields are unchanged.
See the [migration notes](docs/model-v0alpha1.md#source-model-migration) for the
complete compatibility and canonical identity contract.
