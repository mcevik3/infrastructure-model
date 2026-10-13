# Offline FABRIC adapter v0.2

The adapter renders one validated Cluster into an ordinary Python dictionary
using the historical `fabric-generic-cluster` YAML structure. This increment is
offline only: no FABlib imports, discovery, authentication, project lookup,
availability checks, slice submission, or other live operations. A successful
render describes intent and does not prove deployability.

Version 0.2 adds exact ConnectX-6 component realization and consumes node-local
`adapterGroup` intent; dedicated realization also supports exact ConnectX-5.
Existing plain Ethernet renders and the public API/config
remain unchanged. The first L2PTP increment adds addressless point-to-point
topology for two dedicated endpoints at two sites. PCI device realization adds
exact supported GPU, FPGA, and NVMe models with generic count expansion.
Independent DPU requests now support allocation only, without DPU interfaces.
Phase A adds internal IPv4/IPv6 FABNet from explicit provider-neutral Layer-3 intent.
Phase B1 adds external IPv4/IPv6 FABNet for explicit `reachability: public`,
preserving internal service selection and the same site-local restrictions.

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
Private frozen device records contain generated `name` and resolved `model`,
held in the node plan's `gpus`, `fpgas`, `nvmes`, and `dpus` tuples. The translator resolves
each device request and expands its count; the renderer only serializes those
records. Network component/interface plans are independent of these devices.
The Network flag `emit_empty_subnet` and interface flag
`emit_empty_address_families` request the historical FABNet placeholders. Both
default to false for existing L2 plans. They carry target-format information,
without runtime allocation state; the renderer only emits the requested blocks.

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
as aarch64, fail. Explicit CPU frequency and root-disk medium/protocol constraints
remain unsupported and fail, as do unsupported device requirements, persistent/block-storage
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
identity. Independent GPU/FPGA/NVMe and allocation-only DPU requests use the
resolvers below; `other` devices remain unsupported. Omitted or empty device lists retain
the historical empty device-family dictionaries.

## GPU, FPGA, and NVMe components

Device intent stays in `ClusterNode.requirements.devices`. Each entry requires
its generic `name` and `type`; selectors belong under `constraints`. The adapter
accepts only these exact model mappings:

| Generic type | `constraints.model` | Accepted explicit `constraints.vendor` | FABRIC target model |
| --- | --- | --- | --- |
| `gpu` | `Tesla T4` | `NVIDIA` | `GPU_TeslaT4` |
| `gpu` | `RTX6000` | `NVIDIA` | `GPU_RTX6000` |
| `gpu` | `A30` | `NVIDIA` | `GPU_A30` |
| `gpu` | `A40` | `NVIDIA` | `GPU_A40` |
| `fpga` | `U280`, explicit alias `Alveo U280` | `AMD`, `Xilinx` | `FPGA_Xilinx_U280` |
| `fpga` | `SN1022` | `AMD`, `Xilinx` | `FPGA_Xilinx_SN1022` |
| `storage` with `constraints.protocol: nvme` | `P4510` | `Intel` | `NVME_P4510` |

Vendor omission is allowed with an exact supported model. Listed vendor/product
aliases are explicit, case-sensitive resolver entries: there is no substring,
case-folding, vendor-only selection, or automatic model fallback. In particular,
the Tesla target is `GPU_TeslaT4`, never `GPU_Tesla_T4`. Unsupported models,
wrong-family models, incompatible vendors, and missing model identity fail with
a diagnostic identifying the generic device requirement and source path.

NVMe needs all of `type: storage`, `constraints.protocol: nvme`, and
`constraints.model: P4510`. Storage without a protocol, or with another protocol,
does not imply NVMe. For example:

```yaml
requirements:
  devices:
    - name: local-nvme
      type: storage
      constraints: {protocol: nvme, vendor: Intel, model: P4510}
```

This is an independently allocated PCI component. It neither supplies root-disk
`capacity.disk` nor populates `persistent_storage.volume`, filesystem, or mount
configuration. `medium` is not a core device-constraint field. Explicit
`minCapacity` and nonempty device `features` requests fail because this increment
has no static capacity/feature resolver; an exact model does not silently satisfy
those extra constraints. Empty feature lists impose no constraint. Opaque
extensions keep the adapter-wide uninterpreted, unmodified-source behavior.

Generic `count` is interpreted as one when omitted and expanded into repeated
device records when present. No downstream `count` field is emitted. Numbering
is independent per node and family: `gpu1`, `gpu2`, …; `fpga1`, `fpga2`, …;
`nvme1`, `nvme2`, …. Within each family, requests retain authored order and each
count expands in place. Identical requests are not deduplicated. These generated
prefixes are distinct from network `nicN` names, regardless of source request names.

Each family is a mapping whose entries contain exactly `name` and `model`, with
the map key equal to the inner name:

```yaml
pci:
  gpu:
    gpu1:
      name: gpu1
      model: GPU_A40
    gpu2:
      name: gpu2
      model: GPU_A40
```

FPGA rendering allocates the component only; it omits the optional downstream
`interfaces` field. No PCI device request is converted into a network attachment
or changes network-service resolution.

### Reviewed downstream compatibility context

This device contract follows the supplied read-only review of
`fabric-generic-cluster` **1.0.20**, branch `main`, revision
`802a91190528c4e9d2f238464ee1bbcdd654ec97`, with locally inspected
`fabrictestbed-extensions`/FABlib **1.9.5**, `fabrictestbed` **1.9.0**, and
`fabric-fim` **1.9.0**. The downstream dependency is not exactly pinned; these
identifiers and behaviors are compatibility evidence for that context, not a
universal contract across FABRIC versions.

Downstream probing uses the family map key while deployment uses the inner
`name`, so both must match. The reviewed parser is permissive and deployment
helpers can catch component-add exceptions and continue, potentially submitting
a slice missing requested components. This adapter therefore validates every
supported mapping before emitting YAML rather than relying on downstream rejection.

`FPGA_Xilinx_SN1022` is allocatable through the inspected component APIs, but the
reviewed downstream host-discovery logic does not reliably include SN1022 when
deriving host requirements. Rendering it does not claim reliable candidate-host
filtering; no live workaround is implemented.

GPU/FPGA/NVMe allocation may require FABRIC project permissions and availability
at the requested Site. The offline adapter checks neither. It performs no site
inventory lookup, host discovery, scheduling, or slice submission, and an exact
hostRef is not evidence that requested devices are available on that host.

## Allocation-only DPU components

Independent `requirements.devices` entries with `type: dpu` support exactly:

| `constraints.model` | FABRIC target model |
| --- | --- |
| `BlueField-2-ConnectX-6` | `NIC_BlueField_2_ConnectX_6` |
| `ConnectX-7-100` | `NIC_ConnectX_7_100` |
| `ConnectX-7-400` | `NIC_ConnectX_7_400` |

Vendor may be omitted or exactly `NVIDIA`; every other explicit vendor fails.
Model is required, with no fuzzy matching or aliases. `BlueField`, `BlueField-2`,
and `BlueField-3` are insufficient. The inspected FIM catalog associates the two
ConnectX-7 forms with BlueField-3-generation hardware; the exact selector must
distinguish 100G from 400G, with no automatic default.

Features may be omitted or empty. Nonempty `constraints.features` fails because
model identity does not prove arbitrary capabilities. Core already forbids
`protocol` and `minCapacity` for DPU requests; no provider semantics are added.

Count defaults semantically to one and expands in authored requirement order
using an independent per-node `dpu1`, `dpu2`, … counter. Each private device
record renders exactly `name` and `model`; map key equals inner name. No downstream
`count` or `interfaces` field is emitted. Without DPU demand, `pci.dpu` remains `{}`.
For example, `ConnectX-7-100` with `count: 2` renders:

```yaml
pci:
  dpu:
    dpu1:
      name: dpu1
      model: NIC_ConnectX_7_100
    dpu2:
      name: dpu2
      model: NIC_ConnectX_7_100
```

The reviewed downstream path is YAML → DPU model → `add_dpus_to_node()` →
`Node.add_component(name, model)`. Its parser requires name/model and permits
interfaces to be omitted. The adapter validates exact mappings before rendering;
permissive parsing and swallowed component-add exceptions cannot prove success.

DPU-backed NetworkAttachments remain unsupported: `adapter.class: dpu` fails
even with a matching device request or `adapterGroup`. Device and interface
requirements have no explicit link. Matching name/vendor/model selectors do not
establish one, and neither implicit binding nor deduplication occurs.

Compatibility uses the reviewed fabric-generic-cluster **1.0.20**, `main` revision
`802a91190528c4e9d2f238464ee1bbcdd654ec97`, FABlib **1.9.5**, fabrictestbed **1.9.0**,
and FIM **1.9.0**. These are version-specific observations. BlueField-2 is a verified
allocatable component identifier, but downstream host discovery lacks equivalent
filtering coverage to the two ConnectX-7 forms. Rendering does not promise reliable
host pre-filtering; no live workaround is added.

Allocation does not imply DPU configuration. ARM OS, `tmfifo_net0`, RShim, BMC,
management addresses, bundle installation, operating mode, DPU-side Linux, and
host/DPU management topology remain out of scope. The reviewed FABlib
`Component.configure()` behavior does not establish automatic ARM initialization
by fabric-generic-cluster. Project permissions, inventory, site availability,
host selection, scheduling, and submission remain runtime concerns, with no
offline checks. Earlier manual acceptance does not establish DPU provisioning.

Future design: an explicit node-local relationship between a requested DPU device
instance and NetworkAttachments/adapterGroup if unified DPU networking is desired.

## Dedicated ConnectX-5, ConnectX-6, and adapter groups

The exact generic adapter models `ConnectX-5` and `ConnectX-6`, with vendor omitted
or exactly `NVIDIA`, resolve to `NIC_ConnectX_5` and `NIC_ConnectX_6`, respectively.
This is adapter capability resolution, not an external identity mapping; there is no `nicModels`
configuration field. FABRIC component names stay out of the generic core schema.
The existing core schema requires `class` whenever an adapter selector is present;
this resolver accepts `standard` and rejects SmartNIC, DPU, and other classes.

For a single ConnectX-5 attachment, the exact selector is:

```yaml
interfaceRequirements:
  type: ethernet
  adapter:
    class: standard
    vendor: NVIDIA
    model: ConnectX-5
```

Omitting `vendor` is also supported: `{class: standard, model: ConnectX-5}`.
The same adapterGroup behavior applies to both models:

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
`data` and `storage`. Replacing its model selector with `ConnectX-5` produces
one `NIC_ConnectX_5` with the same interfaces. Core validation still owns
identity-conflict checks: a group cannot mix ConnectX-5 and ConnectX-6 or
contradictory explicit vendors. An omitted vendor remains compatible with NVIDIA.

Each ungrouped dedicated attachment gets its own component, using one interface;
the second physical port may remain unused. Identical requirements do not imply
sharing. Explicit groups on one node create separate components, and the same
group string on another node is unrelated. One-member groups are supported.

Both FABRIC components are dual-port: an explicit group may contain at most two
attachments. Larger groups fail rather than being split. This is a provider
realization limit, not a generic group cardinality rule. A group lacking a
supported dedicated model also fails, even with only one member. Grouped plain
Ethernet cannot be represented faithfully by the current one-interface
`NIC_Basic` realization; the translator neither splits that group nor invents a
dedicated component.

Matching is exact. Incompatible vendors, vendor-only selection, ConnectX-7,
aliases, fuzzy spellings, and other models fail. A model identity does not prove requested speed
or features: `minSpeed`, RDMA, SR-IOV, and other nonempty feature requirements
remain unsupported on every group member. Automatic model selection, SmartNIC,
DPU interfaces, port availability scheduling, and live inventory remain out of scope.

> The generic model describes WHAT network capability is required; the FABRIC
> adapter decides HOW that capability is realized.

One networkAttachment must not be assumed universally to equal one physical
NIC/component. Grouping changes component realization only; logical endpoint
counts, network bindings, addresses, prefixes, gateways, and DNS remain independent
for each attachment. Dedicated interfaces use the same network-service resolver
as ordinary Ethernet for multipoint L2Bridge/L2STS. L2PTP remains restricted to
resolved `NIC_ConnectX_6` interfaces; ConnectX-5 does not qualify.

Component numbers follow first creation across both dedicated models and
`NIC_Basic`. Guest `ethN`/`conn-ethN` names follow original attachment order,
including interleaved groups. Plain Ethernet without a dedicated selector or
adapterGroup continues to use implicit `NIC_Basic`.

ConnectX-5 compatibility was verified against fabric-generic-cluster **1.0.20**,
FABlib/fabrictestbed-extensions **1.9.5**, fabrictestbed **1.9.0**, and FIM **1.9.0**.
The exact FABlib model `NIC_ConnectX_5` maps to FIM `SmartNIC_ConnectX_5`, with two
provider interfaces. The [ConnectX-5 fixture](../tests/fixtures/fabric-connectx5.yaml)
uses ordinary management plus grouped data/storage; its target differs from the
ConnectX-6 fixture only in the dedicated component model. Rendering remains
offline and establishes neither current availability nor successful provisioning.

## Network service and addressing

Only Networks referenced by the selected Cluster's attachments are rendered.
The translator uses the actual effective sites of those rendered endpoints,
translated through the config to FABRIC identities. Multiple generic references
mapped to the same FABRIC site count as one provider site. Inventory and declared
scope do not determine endpoint occupancy. Layer-2 multipoint continues to use only the
selected Cluster's endpoints, even when other Clusters use the same Network.

| Network intent | Rendered service |
| --- | --- |
| Explicit layer2, omitted/explicit multipoint, one effective FABRIC endpoint site | `L2Bridge` |
| Explicit layer2, omitted/explicit multipoint, two effective FABRIC endpoint sites | `L2STS` |
| Layer2 multipoint with more than two effective endpoint sites | Translation error |
| Explicit layer2, point-to-point, exactly two complete dedicated endpoints at exactly two effective FABRIC sites, no L3 configuration | `L2PTP` |
| Other point-to-point combinations | Translation error; no downgrade to a multipoint service |
| Layer3, IPv4, provider-managed, provider-internal, multipoint, one mapped endpoint site, NIC_Basic | `IPv4` (FABNetv4) |
| Layer3, IPv6, provider-managed, provider-internal, multipoint, one mapped endpoint site, NIC_Basic | `IPv6` (FABNetv6) |
| Layer3, IPv4, provider-managed, public, multipoint, one mapped endpoint site, NIC_Basic | `IPv4Ext` (FABNetv4Ext) |
| Layer3, IPv6, provider-managed, public, multipoint, one mapped endpoint site, NIC_Basic | `IPv6Ext` (FABNetv6Ext) |
| Partial/unsupported Layer3 intent or unspecified layer | Translation error |

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
Neither NIC_Basic nor NIC_ConnectX_5 qualifies, and plain Ethernet is never upgraded automatically.
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

### Layer-2 multipoint addressing

Each rendered Layer-2 multipoint Network supports at most one IPv4 and one IPv6 prefix. Each
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

## Internal and external FABNet

Service selection requires all four explicit properties:

```yaml
layer: layer3
addressFamily: ipv4 # or ipv6
addressAllocation: provider-managed
reachability: provider-internal # or public
```

Service selection uses the explicit family and reachability together:

| `addressFamily` | `reachability` | Historical target type |
| --- | --- | --- |
| `ipv4` | `provider-internal` | `IPv4` (FABNetv4) |
| `ipv6` | `provider-internal` | `IPv6` (FABNetv6) |
| `ipv4` | `public` | `IPv4Ext` (FABNetv4Ext) |
| `ipv6` | `public` | `IPv6Ext` (FABNetv6Ext) |

The internal Phase A mappings remain unchanged. Phase B1 uses existing generic
public intent; no FABRIC service names enter the generic schema. `connectivity`
must be `multipoint` or omitted with its existing multipoint meaning. Network
names and address formats never select a service or imply public reachability.
Partial Layer-3 requirements remain generic-valid but fail translation when they
cannot unambiguously select a service. Missing properties are not inferred, and
unsupported combinations never fall back to another service.

Each selected generic Network becomes one site-local NetworkService. All of its
endpoints must occupy exactly one distinct **mapped** FABRIC site, using existing
effective-site resolution. `siteRefs` still describes allowed scope: additional
unoccupied allowed sites do not create services, and multiple generic Sites mapped
to one FABRIC site still count as one. No site or worker is inferred. A multi-site
Cluster can declare independent site-local Networks; three Networks at UCSD,
LOSA, and SALT produce three independent services, without synthesized inter-site
tunnels, routes, or Networks. There is no exactly-two-endpoint requirement.

The initial adapter restrictions apply to all four services:

- Every FABNet attachment must resolve to `NIC_Basic` through existing ordinary
  Ethernet requirements. Dedicated ConnectX-5/ConnectX-6 FABNet bindings, DPU
  interfaces, and FPGA interfaces remain unsupported. Existing L2 component
  resolution and allocation-only devices are unchanged.
- At most one FABNet attachment per VM, including repeated attachments to the
  same Network. Other eligible L2 attachments may coexist. This reflects the
  reviewed workflow's first-matching-interface selection and persistent route
  configuration limitations; it is not a generic model constraint.
- Every attachment on a selected FABNet Network must belong to the selected
  Cluster. Shared cross-Cluster FABNet is rejected explicitly. Existing L2
  selected-Cluster rendering is unchanged.
- Network `prefixes`, `defaultGateways`, and `dns`, and attachment `addresses`,
  must be omitted. Authored presence fails even for `[]` or `dns: {servers: []}`.
  Core rejects prefixes/addresses with provider-managed allocation; the adapter
  additionally rejects gateway/DNS information the target cannot honor. Core
  gateway consistency rules still apply before translation.

The internal target retains these placeholders (IPv6 uses `type: IPv6`):

```yaml
site_topology_networks:
  networks:
    net1:
      name: research-network
      type: IPv4
      subnet: {}
```

The external fixtures produce the following respective Network entries:

```yaml
site_topology_networks:
  networks:
    net1:
      name: research-public-v4
      type: IPv4Ext
      subnet: {}
```

```yaml
site_topology_networks:
  networks:
    net1:
      name: research-public-v6
      type: IPv6Ext
      subnet: {}
```

All FABNet interfaces retain both empty family blocks. The binding always follows
the generic Network name (`research-network` in this internal example):

```yaml
pci:
  network:
    nic1:
      name: nic1
      model: NIC_Basic
      interfaces:
        iface1:
          device: eth1
          connection: conn-eth1
          binding: research-network
          ipv4: {}
          ipv6: {}
```

Both empty family blocks are historical format placeholders. They do not request
DHCP, a zero-valued static address, or an addressless runtime interface, and do not
report allocation during rendering. No concrete subnet, interface address, gateway,
DNS server, or route is generated. Existing `netN`, `nicN`, `ifaceN`, `ethN`, and
`conn-ethN` ordering is preserved; existing L2 and internal FABNet output remain
byte-identical. The Phase A private plan and renderer handle all four service
strings without modification.

Provider-managed allocation delegates prefix/address selection to the provider
and deployment workflow. In the reviewed FABRIC workflow, FABRIC supplies the
allocated prefix and gateway information; fabric-generic-cluster selects and
configures guest addresses. Runtime discovery, address selection, guest interface
and routing configuration happen **after slice submission**, outside this adapter.
DHCP, DHCPv6, and SLAAC configuration are not assumed or implemented here.

### External public routing and runtime boundary

The offline adapter stops at historical topology YAML. Rendering an Ext service
does not activate public routing or promise public Internet connectivity. The
reviewed notebook separates the downstream operations:

1. Step 7.1: `deploy_topology_to_fabric()` creates/submits the topology.
2. Step 7.2: `configure_l3_networks()` handles runtime Layer-3 addressing.
3. Step 7.3: `enable_public_routing()` activates external public routing.
4. Step 7.4: `configure_node_interfaces()` configures guest interfaces.

For external services, Step 7.3 calls `make_ip_publicly_routable()` for the
selected addresses and submits a slice modification with `slice.submit()`.
Successful NetworkService creation alone does not prove public-routing activation.
FABRIC project/account entitlements may be required for external public routing;
the adapter neither requests authorization nor verifies permissions.

Even successful public-routing activation does not establish correct guest
default routes, source-based routing policies, or end-to-end Internet access.
Guest routing configuration and connectivity verification are separate downstream
responsibilities. This adapter does not authenticate, call FABRIC, submit slices,
allocate addresses, configure guest interfaces/routes, choose default routes,
modify source-based routing, or verify Internet reachability. It performs no
inventory-aware preflight, scheduling, or automatic worker selection.

Known downstream caveats: address candidate generation is not reservation-aware;
IPv6 address rediscovery may be imperfect; persistent guest configuration and
multi-interface routing require verification. Successful NetworkService creation
does not prove guest connectivity. No downstream workflow is changed here.
Compatibility context is the reviewed fabric-generic-cluster **1.0.20**,
FABlib **1.9.5**, and fabric-fim **1.9.0**, not a universal compatibility claim.

Acceptance fixtures are [IPv4](../tests/fixtures/fabric-fabnet-ipv4.yaml),
[IPv6](../tests/fixtures/fabric-fabnet-ipv6.yaml),
[IPv4Ext](../tests/fixtures/fabric-fabnet-ipv4ext.yaml),
[IPv6Ext](../tests/fixtures/fabric-fabnet-ipv6ext.yaml), and
[three-site IPv4](../tests/fixtures/fabric-fabnet-multisite.yaml), with a
[focused config](../tests/fixtures/fabric-fabnet-config.yaml). They preserve the
service, placement, workers, capacities, images, NICs, bindings, and empty-block
semantics of `_slice_topology_7-1.yml`, `_slice_topology_7-2.yml`,
`_slice_topology_7-3.yml`, `_slice_topology_7-4.yml`, and `m5-v4.yml`, respectively.
The historical comparison tests use the read-only sibling notebooks checkout or
`FABRIC_NOTEBOOK_MODELS`; they skip only those comparisons if it is unavailable.
Ordinary fixture assertions and CLI determinism tests run without reference repos.

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
`worker` (or `''` without hostRef), capacities, PCI device/network mappings,
and `persistent_storage: {volume: {}}`. Unrequested GPU/FPGA/NVMe/DPU
families remain empty dictionaries. All four OpenStack flags are string
`'true'`/`'false'` values for the supported `openstack.control`, `.network`,
`.compute`, and `.storage` roles. No Ansible, postboot, SELinux, or generated
topology header is emitted. L2 Networks without prefixes omit `subnet`; L2
interfaces without addresses omit IP-family blocks. FABNet explicitly emits the
empty placeholders described above.

```sh
infra-model render fabric examples/fabric-like/ \
  --config adapter-configs/fabric.yaml
infra-model render fabric examples/multi-site/ \
  --config adapter-configs/fabric.yaml --cluster cluster/multi-site
infra-model render fabric tests/fixtures/fabric-dedicated-nic.yaml \
  --config adapter-configs/fabric.yaml
infra-model render fabric tests/fixtures/fabric-l2ptp.yaml \
  --config adapter-configs/fabric.yaml
infra-model render fabric tests/fixtures/fabric-pci-devices.yaml \
  --config adapter-configs/fabric.yaml
infra-model render fabric tests/fixtures/fabric-dpu.yaml \
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

The [PCI-device fixture](../tests/fixtures/fabric-pci-devices.yaml) uses an existing
Site/image mapping for one VM with ordinary Ethernet management, two A40 GPUs,
one U280 FPGA, and one P4510 NVMe. It demonstrates `gpu1`, `gpu2`, `fpga1`, and
`nvme1` without asserting live hardware availability. It is outside recursive
examples to avoid duplicate Site identities.

The [DPU fixture](../tests/fixtures/fabric-dpu.yaml) uses the same Site/image mapping
for one VM with ordinary Ethernet management and two independent `ConnectX-7-100`
DPU allocations. It renders `dpu1` and `dpu2` without DPU interfaces or live
hardware requirements, and stays outside recursive examples.

Live FABRIC operations, facility ports, L2PTP guest VLAN/IP configuration, other dedicated NIC models, DPU interface/unsupported device rendering,
public-routing activation, runtime address allocation, DHCP, Ansible, postboot, SELinux,
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
