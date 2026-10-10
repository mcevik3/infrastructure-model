# Offline FABRIC adapter v0.2

The adapter renders one validated Cluster into an ordinary Python dictionary
using the historical `fabric-generic-cluster` YAML structure. This increment is
offline only: no FABlib imports, discovery, authentication, project lookup,
availability checks, slice submission, or other live operations. A successful
render describes intent and does not prove deployability.

Version 0.2 adds exact ConnectX-6 component realization and consumes node-local
`adapterGroup` intent. Existing plain Ethernet renders and the public API/config
remain unchanged. The first L2PTP increment adds addressless point-to-point
topology for two dedicated endpoints at two sites.

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

Within the translator, private component and endpoint realization records are
resolved before Network service selection. Each endpoint identifies its mapped
site and resolved component. This lets L2PTP inspect actual provider realization
without guessing from generic Ethernet requirements or rendered YAML. Components
are resolved once; interface addressing is completed after service selection.

The private plan contains frozen node, network, gateway, prefix, component,
interface, and address records using tuples. A network component contains an
`interfaces` tuple: a dedicated component can now carry two logical interfaces.
Each interface retains its node-global authored attachment ordinal for guest
naming, independently of component grouping.
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
x86_64 is a compatibility precondition for the FABRIC target; no architecture
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

Plain `interfaceRequirements: {type: ethernet}` without `adapterGroup` continues
to produce one `NIC_Basic` component. An empty `features` list adds no constraint
and is also accepted. Ordinary Ethernet is never upgraded to dedicated hardware.
Missing interface type, non-Ethernet types, speed constraints, and nonempty
features (including RDMA or SR-IOV) still fail, even with a supported dedicated
identity. No device requirements are supported, including GPU, FPGA,
storage/NVMe, or DPU requests; an empty devices list is valid.

## Dedicated ConnectX-6 and adapter groups

The exact generic adapter model `ConnectX-6`, with vendor omitted or exactly
`NVIDIA`, resolves to the FABRIC component model `NIC_ConnectX_6`. This is adapter
capability resolution, not an external identity mapping; there is no `nicModels`
configuration field. FABRIC component names stay out of the generic core schema.
The existing core schema requires `class` whenever an adapter selector is present;
this resolver accepts `standard` and rejects SmartNIC, DPU, and other classes.

```yaml
networkAttachments:
  - name: data
    networkRef: network/data
    adapterGroup: highspeed-1
    interfaceRequirements:
      type: ethernet
      adapter: {class: standard, vendor: NVIDIA, model: ConnectX-6}
  - name: storage
    networkRef: network/storage
    adapterGroup: highspeed-1
    interfaceRequirements:
      type: ethernet
      adapter: {class: standard, vendor: NVIDIA}
```

The translator merges compatible explicit identity selectors across all members
of one node-local group before choosing its component. A member may omit adapter
identity entirely if another identifies the supported model. The example above
produces one `NIC_ConnectX_6` with `iface1` and `iface2`, bound independently to
`data` and `storage`. Core validation still owns identity-conflict checks.

Each ungrouped ConnectX-6 attachment gets its own component, using one interface;
the second physical port may remain unused. Identical requirements do not imply
sharing. Explicit groups on one node create separate components, and the same
group string on another node is unrelated. One-member groups are supported.

FABRIC `NIC_ConnectX_6` is dual-port: an explicit group may contain at most two
attachments. Larger groups fail rather than being split. This is a provider
realization limit, not a generic group cardinality rule. A group lacking a
supported dedicated model also fails, even with only one member. Grouped plain
Ethernet cannot be represented faithfully by the current one-interface
`NIC_Basic` realization; the translator neither splits that group nor invents a
dedicated component.

Matching is exact. Incompatible vendors, vendor-only selection, ConnectX-5,
ConnectX-7, and other models fail. A model identity does not prove requested speed
or features: `minSpeed`, RDMA, SR-IOV, and other nonempty feature requirements
remain unsupported on every group member. Automatic model selection, SmartNIC,
DPU, port availability scheduling, and live inventory remain out of scope.

> The generic model describes WHAT network capability is required; the FABRIC
> adapter decides HOW that capability is realized.

One networkAttachment must not be assumed universally to equal one physical
NIC/component. Grouping changes component realization only; logical endpoint
counts, network bindings, addresses, prefixes, gateways, and DNS remain independent
for each attachment. Dedicated interfaces use the same network-service resolver
as ordinary Ethernet; only supported dedicated interfaces qualify for L2PTP.

## Network service and addressing

Only Networks referenced by the selected Cluster's attachments are rendered.
The translator uses the actual effective sites of those rendered endpoints,
translated through the config to FABRIC identities. Multiple generic references
mapped to the same FABRIC site count as one provider site. Inventory and declared
scope do not determine endpoint occupancy. Multipoint continues to use only the
selected Cluster's endpoints, even when other Clusters use the same Network.

| Network intent | Rendered service |
| --- | --- |
| Explicit layer2, omitted/explicit multipoint, one effective FABRIC endpoint site | `L2Bridge` |
| Explicit layer2, omitted/explicit multipoint, two effective FABRIC endpoint sites | `L2STS` |
| More than two effective endpoint sites | Translation error |
| Explicit layer2, point-to-point, exactly two complete dedicated endpoints at exactly two effective FABRIC sites, no L3 configuration | `L2PTP` |
| Other point-to-point combinations | Translation error; no downgrade to a multipoint service |
| Layer3 or unspecified layer | Translation error |

`Network.siteRefs` declares allowed scope, already enforced by core validation.
Its length is never the service-selection site count. Unused allowed sites have
no effect: two listed Sites with endpoints at just one site yield `L2Bridge`.
Core graph semantics remain `scoped_to` for Network scope, `targets_site` for
effective desired node placement, and `located_at` for physical inventory.
Provider service names stay out of core schemas.

### L2PTP v0.1: addressless topology

Generic `point-to-point` remains provider-neutral and allows zero, one, or two
NetworkAttachments for partial composition. FABRIC L2PTP requires exactly two
rendered attachments, both in the selected Cluster, at exactly two distinct
effective FABRIC sites. Site mapping happens before distinctness is checked;
two generic Sites mapped to one FABRIC Site do not qualify. Network.siteRefs
length does not establish eligibility.

If a referenced point-to-point Network has another endpoint in an unselected
Cluster, rendering fails with a selected-Cluster completeness error. Networks
used only by other Clusters remain outside the render. Completely unattached
Networks, including point-to-point declarations, are unreferenced and ignored.
A selected point-to-point Network with one endpoint still fails provider
eligibility. Multipoint selection is unchanged.

Both endpoint interfaces must belong to resolved `NIC_ConnectX_6` components.
NIC_Basic does not qualify, and plain Ethernet is never upgraded automatically.
An endpoint may be one port of a grouped component whose other port serves a
different Network. Its dedicated identity may come from a compatible selector
on another group member. Counts always use NetworkAttachments referencing this
Network, not adapter groups/components; whole-component exclusivity is not
required. Invalid point-to-point intent is never downgraded to L2Bridge or L2STS.

FABRIC L2PTP traffic arrives VLAN-tagged and requires a guest VLAN subinterface.
The current model/renderer does not configure that subinterface, so this first
L2PTP realization is **topology-only and addressless**. Rendering fails if the
Network declares `prefixes`, `defaultGateways`, or `dns`, or either endpoint
declares `addresses`. Presence is rejected even for empty lists/configuration.
These restrictions belong only to FABRIC L2PTP v0.1; generic point-to-point
addressing remains valid in core.

The Network output contains `name` and `type: L2PTP`, with no `subnet`. Endpoint
interfaces retain normal `device`, `connection`, and Network-name `binding`
fields, without IP blocks or VLAN configuration. No VLAN ID is invented.
Future design: model/render guest VLAN subinterfaces for tagged L2PTP traffic
before supporting static IP configuration. Offline eligibility does not prove
availability, provisioning, or runtime network correctness.

### Multipoint addressing

Each rendered multipoint Network supports at most one IPv4 and one IPv6 prefix. Each
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
Component keys/names (`nic1`, `nic2`, …) follow first-creation order: each ungrouped
attachment or first occurrence of a group creates one component, and later group
members append to it. `iface1`/`iface2` follow attachment order within that component.
A small renderer policy uses each interface's original node-global attachment
ordinal for `ethN`/`conn-ethN`. For authored order data (group A), management
(ungrouped), storage (group A), `nic1` has `eth1` and `eth3`, and `nic2` has `eth2`.
No generic hostname or guest interface naming fields are added.

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
infra-model render fabric tests/fixtures/fabric-dedicated-nic.yaml \
  --config adapter-configs/fabric.yaml
infra-model render fabric tests/fixtures/fabric-l2ptp.yaml \
  --config adapter-configs/fabric.yaml
infra-model render fabric examples/ \
  --config adapter-configs/fabric.yaml --cluster openstack-lab > _slice_topology.yml
```

The CLI writes deterministic YAML only to stdout, and validation warnings/errors
to stderr. It creates no output file itself. Exit status is 0 for successful
rendering, 1 for input/configuration/translation failure, and 2 for CLI usage
errors. Rendering the same normalized model and config twice produces equal
dictionaries and byte-identical CLI YAML.

The [dedicated-NIC fixture](../tests/fixtures/fabric-dedicated-nic.yaml) uses only
provider-neutral source intent: one VM, ungrouped management, and grouped
data/storage attachments on three layer2 Networks. It renders `nic1` as
`NIC_Basic` with `iface1`, and `nic2` as `NIC_ConnectX_6` with `iface1`/`iface2`.
It reuses the existing Site/image mappings without changing adapter config.
It lives outside `examples/` because its self-contained `site/lab-site` document
would duplicate the existing Site during recursive example loading.

The [L2PTP fixture](../tests/fixtures/fabric-l2ptp.yaml) contains two VMs at
`site/site-a` and `site/site-b`, each with a ConnectX-6 attachment to one addressless
point-to-point Network. It uses the existing Site/image configuration and renders
one L2PTP service with SRI and UKY endpoints.

Live FABRIC operations, facility ports, L2PTP guest VLAN/IP configuration, other dedicated NIC models, device rendering,
Layer-3 services, provider-managed addressing, DHCP, Ansible, postboot, SELinux,
and filesystem/mount rendering remain outside this increment.

## Manual downstream acceptance

FABRIC adapter v0.1 was manually verified successfully through this workflow:

```text
provider-neutral infrastructure model
    -> infra-model validation
    -> infra-model render fabric
    -> generated _slice_topology_*.yml
    -> existing fabric-generic-cluster downstream tooling/notebook/library
```

The rendered YAML was accepted by the existing downstream consumer without
manual edits. For the documents exercised, this demonstrates structural
compatibility with the existing fabric-generic-cluster input format and
successful consumption by the downstream workflow. This manual acceptance is
recorded separately from automated unit/integration test counts.

It does not establish live FABRIC resource availability, FABRIC project
permissions, successful slice submission, successful physical resource
provisioning, runtime network correctness, or support for features outside the
current adapter scope.
