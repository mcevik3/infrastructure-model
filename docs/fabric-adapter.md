# Offline FABRIC adapter v0.1

The adapter renders one validated Cluster into an ordinary Python dictionary
using the historical `fabric-generic-cluster` YAML structure. This increment is
offline only: no FABlib imports, discovery, authentication, project lookup,
availability checks, slice submission, or other live operations. A successful
render describes intent and does not prove deployability.

The adapter either represents supported intent faithfully or raises
`FabricTranslationError`. It never rounds capacities, guesses placement/images,
falls back from specialized NIC requests, or emits partial/degraded YAML.

## API and pipeline

```python
from infra_model import InfrastructureModel
from infra_model.adapters.fabric import (
    FabricAdapterConfig,
    FabricTranslationError,
    render_fabric,
)

model = InfrastructureModel.load("examples/fabric-like")
model.validate()
config = FabricAdapterConfig.from_file("adapter-configs/fabric.yaml")
document = render_fabric(model, cluster="openstack-lab", config=config)
```

These three names are the public FABRIC API. `FabricAdapterConfig.from_dict()`
also accepts a parsed configuration document. `render_fabric()` validates the
model if needed and returns a fresh dictionary. A Cluster can be selected by
metadata name or canonical `cluster/<name>` reference; selection can be omitted
only when the model contains exactly one Cluster.

The pipeline is validated `InfrastructureModel` → translator → private immutable
`_FabricRenderPlan` → renderer → dictionary → CLI YAML serializer. Provider and
topology decisions belong to the translator. The renderer only formats resolved
plan data; it does not resolve references, select services, match prefixes, or
choose images. No new infrastructure schema, NetworkX object, or provider SDK
object is exposed.

The private plan contains frozen node, network, gateway, prefix, component,
interface, and address records using tuples. A network component contains an
`interfaces` tuple, allowing a future component to expose multiple interfaces.
Network `default_gateways` and `dns_servers` preserve `None` for unspecified,
`()` for explicitly empty, and tuples for configured values. Resolved addresses
already contain their prefix length, gateway, and family-specific DNS server.
The private node plan carries `worker: str | None`: a resolved FABRIC worker
identifier, or `None` when no exact host is requested. No plan type becomes public API.

Errors carry `entity`, `path`, `source`, and `reason` attributes where available.
Invalid core models also fail before rendering; the Python adapter wraps those
errors with their original exception as the cause. The existing validation API
and `validate` command keep their behavior.

## Provider config and generic placement

The [example configuration](../adapter-configs/fabric.yaml) is deliberately outside
the generic `examples/` tree so `infra-model validate examples/` still loads only
generic topology documents. Configuration has a separate provider namespace:

```yaml
apiVersion: infra.model.fabric/v0alpha1
kind: FabricAdapterConfig
sites:
  site/lab-site: SRI
  site/site-a: SRI
  site/site-b: UKY
images:
  - name: Rocky Linux
    version: "9"
    fabricImage: default_rocky_9
```

The document requires exactly this API version and kind, a `sites` mapping, and
an `images` list. Site keys are canonical generic Site references and values are
nonempty FABRIC names. Image name/version pairs must be unique, with nonempty
string `name`, `version`, and `fabricImage` values. Unknown fields, duplicate YAML
keys, malformed values, and multiple configuration documents are rejected.
Mappings are immutable after validation. There is no `defaultSite`, `defaultWorker`, or discovery
fallback; unused generic Sites do not need mappings.

An optional `workers` mapping translates canonical generic Server identities
to nonempty FABRIC worker/hypervisor identifiers:

```yaml
workers:
  server/sri-worker-1: sri-worker-1
  server/uky-worker-3: uky-worker-3
```

Worker keys use the core canonical Server-reference definition. The mapping may
be omitted or empty, and unused mappings are allowed. Every supplied entry must
still have a valid key and nonempty string value. Only hosts explicitly referenced
by the selected Cluster require a corresponding mapping. The main example config
needs no workers because the existing examples do not request exact hosts.

Generic node placement remains authoritative: `ClusterNode.placement.siteRef`
overrides `Cluster.spec.siteRef`. Otherwise the site is unresolved and translation
fails. The adapter maps the resolved reference to its FABRIC name; it does not
choose placement or infer a VM site from inventory. Bare-metal exact-resource
placement may later derive a site from `Server.siteRef`, but this VM adapter does
not implement that rule.

For a VM with `placement.hostRef`, the validated reference identifies a Server.
The adapter resolves that exact reference through `FabricAdapterConfig.workers`
and emits the mapped value as historical `worker`. An explicit host without a
mapping raises `FabricTranslationError`; no hostRef emits `worker: ''`. The adapter
never substitutes Server metadata.name, site, hostname, or an extension value.

Site and worker mappings translate independent identities. Worker mappings do not
choose placement, infer a Site, alter inventory, or prove capacity/availability.
The core validator owns host/site consistency. Neither it nor this adapter derives
effective Site from `hostRef → Server → Site`; a host-only VM without node/Cluster
siteRef still fails translation. No Site is inferred from a worker name, and no
worker is inferred from a Site mapping. Live worker discovery remains out of scope.

Node-level image identity replaces the Cluster image default when supplied.
Each effective image requires an exact name/version mapping. A missing version,
missing mapping, or different spelling fails; no fuzzy matching or version
inheritance into a partial node image is performed.

## Supported intent and target restrictions

Only VM realization is supported. Every VM needs explicit CPU, memory, and disk
requirements. CPU must use `vcpu` and becomes integer `capacity.cpu`. Memory and
disk are converted exactly to integral GiB for `capacity.ram` and `capacity.disk`,
using rational arithmetic for SI/IEC quantities, with no rounding. The image
mapping supplies `capacity.os`.

Omitted architecture and `compute.architecture: x86_64` are supported. Explicit
x86_64 is a compatibility precondition for the v0.1 FABRIC target; no architecture
field is fabricated in the historical output. Other explicit architectures, such
as aarch64, fail. Explicit CPU frequency and disk medium/protocol constraints
remain unsupported and fail, as do device requirements, persistent/block-storage
configuration, filesystem/mount intent, and unsupported semantic roles.

Unconsumed namespaced extensions are opaque metadata. Their presence alone does
not prevent rendering: the adapter does not interpret them, copy them into the
target, or mutate/strip them from source. It assigns no meaning to
`fabric.example/intent`. Extensions never override or suppress failures for
unsupported generic core intent. A future adapter-owned extension namespace may
define its own validation rules; there is no requirement for every adapter to
understand every extension. Descriptive metadata and observed status likewise
are not rendering requests.

The existing FABRIC-like example remains unchanged, including its x86_64
requirement and `fabric.example/intent: {experimentName: openstack-lab}` extension,
along with CPU/memory/disk demands, images, roles, attachments, and addresses.
The multi-site example supplies explicit Rocky Linux 9 image and
2-vCPU/4-GiB/20-GiB VM capacities. Neither example requires an exact host.

Plain `interfaceRequirements: {type: ethernet}` produces a `NIC_Basic` component.
An empty `features` list adds no constraint and is also accepted. Missing interface
type, non-Ethernet types, speed constraints, nonempty features (including RDMA or
SR-IOV), and any adapter class/vendor/model constraint fail. No device requirements
are supported, including GPU, FPGA, storage/NVMe, or DPU requests; an empty devices
list is valid.

> The generic model describes WHAT network capability is required; the FABRIC
> adapter decides HOW that capability is realized.

One networkAttachment must not be assumed universally to equal one physical
NIC/component. This translator currently creates one `NIC_Basic` component per
plain Ethernet attachment. Dedicated NIC/ConnectX-6 allocation and multi-interface
physical NIC grouping remain deferred; the private component structure and guest
naming policy already permit multiple interfaces without changing the renderer's
topology responsibilities.

## Network service and addressing

Only Networks referenced by the selected Cluster's attachments are rendered.
The translator uses the actual effective sites of those rendered endpoints,
translated through the config to FABRIC identities. Multiple generic references
mapped to the same FABRIC site count as one provider site. Unrelated Clusters,
inventory, and Networks do not determine this rendered topology's service.

| Network intent | Rendered service |
| --- | --- |
| Explicit layer2, omitted/explicit multipoint, one effective FABRIC endpoint site | `L2Bridge` |
| Explicit layer2, omitted/explicit multipoint, two effective FABRIC endpoint sites | `L2STS` |
| More than two effective endpoint sites | Translation error |
| Point-to-point | Translation error; L2PTP and dedicated-interface realization are deferred |
| Layer3 or unspecified layer | Translation error |

`Network.siteRefs` declares allowed scope, already enforced by core validation.
Its length is never the service-selection site count. Unused allowed sites have
no effect: two listed Sites with endpoints at just one site yield `L2Bridge`.
Core graph semantics remain `scoped_to` for Network scope, `targets_site` for
effective desired node placement, and `located_at` for physical inventory.
Provider service names stay out of core schemas.

Each rendered Network supports at most one IPv4 and one IPv6 prefix. Each
attachment supports at most one address of each family. Every address must match
exactly one declared prefix of the same family; no longest-prefix fallback exists.
Zero addresses is valid for L2. Core-invalid addresses are rejected before
translation; otherwise unsupported prefix/address multiplicity fails in the adapter.

Gateways are matched to prefixes using IP-network equivalence, then copied to
the Network subnet and any corresponding interface addresses. Omitted or explicitly
empty gateways cause no gateway to be invented. IPv6 link-local gateways remain
valid. DNS is partitioned by address family: zero servers omit that family's DNS,
one supplies the scalar, and more than one fails. Mixed-family DNS works. DNS
is applied only to explicitly addressed interface families; addressless interfaces
have no family blocks. The private Network plan retains omitted/empty configuration
even where the historical output cannot distinguish the two.

## Historical rendering and CLI

Output contains `site_topology_nodes.nodes`, `site_topology_networks.networks`,
and `site_topology_facility_ports.facility_ports: {}`. Nodes use `nodeN` keys in
Cluster order and Networks use `netN` keys in first-attachment-use order. Network
entries retain the generic name; interface `binding` uses that name, never `netN`.
NIC keys/names follow attachment order (`nic1`, `nic2`, …), currently each with
`iface1`. A small renderer policy counts guest interfaces across components to
produce `eth1`/`conn-eth1`, `eth2`/`conn-eth2`, etc. No generic hostname or guest
interface naming fields are added.

Each node emits its generic name as `name` and `hostname`, mapped `site`, a resolved
`worker` (or `''` without hostRef), capacities, empty PCI `dpu`/`fpga`/`gpu`/`nvme` dictionaries, and
`persistent_storage: {volume: {}}`. All four OpenStack flags are string
`'true'`/`'false'` values for the supported `openstack.control`, `.network`,
`.compute`, and `.storage` roles. No Ansible, postboot, SELinux, or generated
topology header is emitted. Networks without prefixes omit `subnet`; interfaces
without addresses omit IP-family blocks.

```sh
infra-model render fabric examples/fabric-like/ \
  --config adapter-configs/fabric.yaml
infra-model render fabric examples/multi-site/ \
  --config adapter-configs/fabric.yaml --cluster cluster/multi-site
infra-model render fabric examples/ \
  --config adapter-configs/fabric.yaml --cluster openstack-lab > _slice_topology.yml
```

The CLI writes deterministic YAML only to stdout, and validation warnings/errors
to stderr. It creates no output file itself. Exit status is 0 for successful
rendering, 1 for input/configuration/translation failure, and 2 for CLI usage
errors. Rendering the same normalized model and config twice produces equal
dictionaries and byte-identical CLI YAML.

Live FABRIC operations, facility ports, L2PTP, dedicated NICs, device rendering,
Layer-3 services, provider-managed addressing, DHCP, Ansible, postboot, SELinux,
and filesystem/mount rendering remain outside this increment.
