# C4.5 — FABRIC integration stabilization checkpoint

Checkpoint date: 2026-10-28. This records the completed adapter and downstream
provisioning integration for review; it is not a new feature milestone or release.

## 1. Baseline overview and supported scope

The supported path is provider-neutral VM intent in `infrastructure-model`,
offline rendering to the historical `fabric-generic-cluster` topology format,
and downstream strict, provision-only FABRIC deployment. The checkpoint covers
component allocation and NetworkService bindings for the reported scenarios.
It does not establish guest networking, packet forwarding, device activation,
application readiness, or live coverage of every renderable resource variant.

Evidence has three distinct sources:

- **Reported live acceptance:** the C2, C3, and C4 outcomes supplied for this
  checkpoint. None was independently executed during this documentation pass.
- **Existing recorded evidence:** earlier compatibility reviews, downstream
  documentation, and committed fixture provenance, attributed below.
- **Documentation-pass inspection:** local Git revisions, file paths, API
  signatures, and implementation semantics checked without live FABRIC calls.

In this pass, the two C4 example inputs in section 11 were rendered offline and
matched the downstream committed fixtures byte-for-byte. They retained the
expected UNKNOWN warnings for site-only placement without exact inventory
evidence. Links, Python example syntax, and documented source references were
also checked. No production test suite or live acceptance scenario was rerun.

The [adapter guide](fabric-adapter.md), [model contract](model-v0alpha1.md), and
[decisions](decisions.md) retain the detailed modeling rules. Earlier statements
that C2/C3 live acceptance remains required describe those implementation stages;
sections 6–8 record the later reported outcomes. Likewise, the downstream README's
historical C4.2b deferral is superseded by the reported notebook invocation in
section 8. This does not broaden the adapter contract or change downstream code.

## 2. Source repositories and exact revision evidence

The following full SHAs were verified in the local repositories. A checkout HEAD
is a source reference, not proof that a particular live run used it.

| Repository | Inspected revision | Evidence and role |
| --- | --- | --- |
| `infrastructure-model` | `aeb54aa4b98757f7b7783dfca18588e524ff2215` | HEAD of the existing `docs/c4-fabric-stabilization` branch before these documentation edits. Also the explicitly recorded source revision for the downstream C4 offline fixtures. |
| `fabric-generic-cluster` | `338566526c4ea47ed200493f00b6adeba5167d2e` | Inspected `main` HEAD, including the C4.2a.1 FABlib Interface accessor correction. |
| `fabric-generic-cluster-notebooks` | `c49db48f93fca5fab08672b884bfc3ca8e3683bd` | Inspected `feature/add-instruction-file` HEAD containing `notebooks/create-slice.ipynb`; not an archived copy of the reported JupyterHub session. |

Additional exact source references:

| Revision | Meaning |
| --- | --- |
| `43472d3faace2042d3d9d1a0454bd7ebe117cca7` in `fabric-generic-cluster` | C4.2a strict provision-only implementation; ancestor of the inspected downstream HEAD. |
| `802a91190528c4e9d2f238464ee1bbcdd654ec97` in `fabric-generic-cluster` | Earlier 1.0.20 compatibility-review baseline recorded in the adapter guide; predates the C4 strict implementation. |

The downstream `tests/fixtures/README.md` explicitly identifies
`aeb54aa4b98757f7b7783dfca18588e524ff2215` as the source of its rendered DPU IPv4
and U280 L2Bridge offline test fixtures. The downstream README records offline
testing of the strict path and accessor correction. Those historical tests were
not rerun here, and their counts are not new checkpoint test results.

**Exact live-tested Git revisions: pending capture for all three repositories.**
The supplied C2/C3/C4 outcomes do not include per-run SHAs, dirty-tree state, or an
archived executed notebook. Do not substitute the inspected HEADs for those
missing values. The notebooks checkout also contains pre-existing untracked
models; they were not treated as acceptance artifacts or changed in this pass.

Repository-relative evidence paths in the related repositories are:

- `fabric-generic-cluster`: `README.md`, `pyproject.toml`,
  `fabric_generic_cluster/deployment.py`, `fabric_generic_cluster/_realization.py`,
  `fabric_generic_cluster/models.py`, and `tests/fixtures/README.md`.
- `fabric-generic-cluster-notebooks`: `AGENTS.md`, `README.md`,
  `notebooks/create-slice.ipynb`, and `model/`.

## 3. Runtime and library versions

| Item | Recorded value | Provenance and limit |
| --- | --- | --- |
| Python | `3.13.2` | Downstream README's C4 offline test environment; exact live JupyterHub/kernel Python is **pending capture**. |
| FABlib / `fabrictestbed-extensions` | `1.9.5` | Earlier adapter review and downstream C4 offline test environment; live acceptance version is **pending capture**. |
| FIM / `fabric-fim` | `1.9.0` | Earlier adapter review and downstream C4 offline test environment; live acceptance version is **pending capture**. |
| `fabrictestbed` | `1.9.0` | Earlier adapter compatibility review; C4/live runtime value is **pending capture**. |
| `infra-model` | `0.1.0a1` | Source `pyproject.toml`; installed live version and source association are **pending capture**. |
| `fabric-generic-cluster` | `1.0.20` | Inspected source `pyproject.toml`, including the C4 changes; installed live version and source association are **pending capture**. |
| `fabric-generic-cluster-notebooks` | Git revision in section 2 | No package release version identified; executed notebook revision is **pending capture**. |
| Pydantic and other runtime dependencies | **Pending capture** | Dependency ranges are not exact installed versions. |

The generic schema API remains `infra.model/v0alpha1`; the separate adapter
configuration uses `infra.model.fabric/v0alpha1`. These are not package versions.
The combined Python path requires at least Python 3.11 because `infra-model`
requires `>=3.11`; downstream metadata alone permits `>=3.9`.

The downstream accessor review inspected FABlib `1.9.0`, `1.9.5`, and `2.0.6`
source for `Interface.get_fim()`. It records removal of the deprecated
`get_fim_interface()` alias in `2.0.6`. This is source compatibility evidence,
not a full runtime test matrix or proof of the live kernel's SDK version.
Package version `1.0.20` alone does not identify the strict implementation:
the earlier baseline has the same version, so the source revision matters.

## 4. Generic model to FABRIC provisioning pipeline

```text
Provider-neutral YAML
  -> InfrastructureModel.load() and validate()
  -> FabricAdapterConfig + render_fabric()
  -> historical fabric-generic-cluster dictionary / YAML
  -> load_topology_from_yaml_file() -> SiteTopology
  -> sd.deploy_topology_to_fabric(..., provision_only=True)
  -> strict construction and pre-submission verification
  -> FABRIC submission and explicit provider refresh
  -> strict allocation and NetworkService membership verification
  -> returned slice, with guest configuration deferred
```

The adapter's internal pipeline is translator → immutable private render plan →
renderer. Translation resolves identity, placement, components, service types,
and address representation. Rendering does not call FABlib. The output uses
`site_topology_nodes.nodes`, `site_topology_networks.networks`, and an empty
`site_topology_facility_ports.facility_ports` mapping. The downstream library
owns provider calls; it is not part of the generic schema or offline adapter.

Generic validation can leave UNKNOWN compatibility warnings; successful validation,
rendering, and `SiteTopology` parsing each establish a different, narrower fact
than live allocation. None establishes guest connectivity.

## 5. Device families and network service support

These are supported **adapter representations**, subject to the detailed
[adapter restrictions](fabric-adapter.md#supported-intent-and-target-restrictions).
Live acceptance is limited to the explicitly listed scenarios in sections 6–8.

| Family | Generic selector → FABRIC component | Binding scope |
| --- | --- | --- |
| Plain Ethernet | Unqualified Ethernet → `NIC_Basic` | One interface per component. |
| Dedicated NIC | `ConnectX-5` → `NIC_ConnectX_5`; `ConnectX-6` → `NIC_ConnectX_6` | Up to two attachments sharing an explicit node-local `adapterGroup`. |
| GPU | `Tesla T4` → `GPU_TeslaT4`; `RTX6000` → `GPU_RTX6000`; `A30` → `GPU_A30`; `A40` → `GPU_A40` | Independent allocation; no network binding through a GPU requirement. |
| FPGA | `U280` or explicit alias `Alveo U280` → `FPGA_Xilinx_U280`; `SN1022` → `FPGA_Xilinx_SN1022` | Allocation and up to two logical interfaces per referenced device. |
| DPU | `ConnectX-7-100` → `NIC_ConnectX_7_100`; `ConnectX-7-400` → `NIC_ConnectX_7_400` | Allocation and up to two logical interfaces per referenced device. |
| DPU, allocation only | `BlueField-2-ConnectX-6` → `NIC_BlueField_2_ConnectX_6` | Network attachment binding is unsupported. |
| NVMe | `type: storage`, `protocol: nvme`, `model: P4510` → `NVME_P4510` | Independent PCI allocation; no filesystem, mount, or root-disk configuration. |

FPGA/DPU attachments use `deviceRequirementRef` to select an existing same-node
requirement with effective count one. Separate names preserve separate allocations
even for identical models. Count expansion without bindings remains supported.
Model equality never substitutes for requirement identity. Exact selectors and
vendor rules apply; model names do not prove speed, RDMA, SR-IOV, or other features.

| Network representation | Restrictions |
| --- | --- |
| `L2Bridge` | Layer-2 multipoint at one effective mapped endpoint site. |
| `L2STS` | Layer-2 multipoint at two effective mapped endpoint sites; more than two is unsupported. |
| `L2PTP` | Addressless point-to-point, exactly two endpoints at two sites, both on `NIC_ConnectX_6`; no FPGA/DPU eligibility or guest VLAN/IP configuration. |
| `IPv4`, `IPv6` | Internal, provider-managed FABNet, one mapped endpoint site per Network. |
| `IPv4Ext`, `IPv6Ext` | Public-intent, provider-managed FABNet, with the same site-local restrictions; allocation does not establish public routing. |

The six non-L2PTP service representations are available for the supported
ConnectX-7 and FPGA bindings. At most **one FABNet NetworkAttachment per VM** is
allowed across NIC, DPU, and FPGA families, including repeated attachments to the
same Network. Other eligible L2 attachments may coexist. FABNet requires omitted
authored prefixes, gateways, DNS, and attachment addresses; empty subnet and
address-family blocks are target-format placeholders, not configured guest IPs.

`iface1`/`iface2` select interfaces by initial provider enumeration within the
allocated component. They do not select physical `p1`/`p2`. Generated `ethN` and
`conn-ethN` are labels, not discovered guest interface identities.

## 6. C2 DPU live acceptance summary

All outcomes in this table are **reported live acceptance**, not tests performed
in this documentation pass. Passing refers to the reported allocation/binding
scenario and does not establish guest IP configuration or DPU dataplane behavior.

| Scenario | Reported outcome |
| --- | --- |
| ConnectX-7-100 with internal IPv4 FABNet | Passed. |
| ConnectX-7-400 with internal IPv6 FABNet | Passed. |
| ConnectX-7-100 with L2STS / two-site scenario | Passed. |
| ConnectX-7-400 with IPv4Ext plus L2Bridge | Passed. |
| Two identical DPUs on one VM | Hardware-blocked; not accepted as a completed live scenario. Earlier documentation records partial verification. |

Exact run dates, source/configuration artifacts, live revisions, runtime versions,
and retained provider evidence are **pending capture**. Hardware blocking does
not negate the adapter's distinct-allocation representation or prove a software
defect in that representation.

## 7. C3 FPGA live acceptance summary

These are also **reported live acceptance** outcomes, not independently rerun.

| Scenario | Reported outcome |
| --- | --- |
| U280 with L2Bridge | Passed. |
| U280 with L2Bridge and L2STS | Passed. |
| U280 with internal IPv4 FABNet | Passed. |
| SN1022 with L2Bridge | Passed. |
| SN1022 with two independent L2Bridge services | Passed. |

Acceptance concerns allocation and NetworkService bindings. In particular, two
SN1022 service bindings do not establish two operational datapaths. FPGA
programming, driver readiness, packet processing, and forwarding remain outside
this evidence. Link-down before activation is not by itself an allocation failure.
Other FPGA/service combinations remain unverified live unless separately recorded.
Exact run dates, artifacts, revisions, runtime versions, and retained provider
evidence are **pending capture**.

## 8. C4 standard-path integration acceptance

| Checkpoint | Reported result |
| --- | --- |
| C4.1 | Investigation completed. |
| C4.2a | Strict provisioning implementation completed. Source reference: `43472d3faace2042d3d9d1a0454bd7ebe117cca7`. |
| C4.2a.1 | FABlib Interface accessor correction completed. Source reference: `338566526c4ea47ed200493f00b6adeba5167d2e`; strict resolution uses `Interface.get_fim()`. |
| C4.4.1 | U280 L2Bridge standard-path live acceptance passed. |
| C4.4.2 | ConnectX-7-100 IPv4 FABNet standard-path live acceptance passed. |
| Existing notebook workflow | Successfully invoked `provision_only=True`, as reported. |
| C4.2b | A separate feature implementation is not required. |

The two C4.4 passes and notebook invocation are supplied acceptance reports.
The source references identify implementation commits, not independently established
live-run SHAs. The inspected `notebooks/create-slice.ipynb` already imports
`deployment as sd` and calls `sd.deploy_topology_to_fabric(topology, slice_name)`;
its checked-in call does not include the new keyword. The successful provision-only
invocation is session evidence, with the executed notebook artifact **pending
capture**. No notebook or downstream library is modified by this checkpoint.

## 9. Provision-only deployment API and semantics

```python
from fabric_generic_cluster import deployment as sd

slice = sd.deploy_topology_to_fabric(
    topology,
    slice_name,
    provision_only=True,
)
```

`provision_only=True` implies strict provider verification, even if `strict=False`
is also supplied. Allocation and NetworkService binding are verified. Submission
uses `wait=True`, `progress=False`, `post_boot_config=False`, and `wait_ssh=False`:
provider resource-state waiting remains enabled; guest SSH waiting is skipped.
Post-boot command registration, guest interface discovery, IP/routes, persistent
networking, DPU configuration, FPGA programming, and public-routing activation
are skipped. A successful return does not establish packet forwarding, guest
interface state, guest network configuration, FPGA activation, or DPU dataplane
behavior.

Both deployment entry points accept keyword-only `provision_only` and `strict`
flags without changing existing positional arguments. The all-in-one
`sd.deploy_and_configure_slice(..., provision_only=True)` also returns immediately
after verified provisioning and skips its network, SSH, SELinux, and Ansible steps.
By contrast, `strict=True` alone retains the existing guest workflow; it adds no
guest health-verification contract. Defaults preserve legacy behavior.

## 10. Strict pre/post verification behavior

1. **Preflight:** derive a requested manifest from `SiteTopology`; reject duplicate
   effective identities, invalid component family/model mappings, unknown service
   types, and bindings to nonexistent services.
2. **Construction:** allocate the requested named components. Component-add errors
   that legacy helpers can log and skip become failures. Resolve requested
   interfaces within each specific allocation, reject missing or duplicate
   selections, and retain each selected FIM name and ID. Unused ports are allowed.
3. **Before submission:** create each service with its complete interface list;
   verify actual type and exact membership. Retain provider-normalized L3 service
   names and normalize IPv4/IPv6 versus FABNet type names, including Ext variants.
   A provider-changed service type fails verification; no silent substitution occurs.
4. **After submission:** explicitly call `update_slice()`, `update_slivers()`, and
   `update_topology()` and require a replaced topology snapshot. Refresh failure
   propagates. A cached request or `isStable()` alone is insufficient evidence.
5. **Allocation verification:** require `StableOK` or `ModifyOK`, active required
   node/service reservations, and matching active sliver evidence. Components are
   checked under their parent node reservation, not independent component slivers.
6. **Graph verification:** enumerate raw FIM resource IDs; verify component
   identities/models, retained selected interface identities, service types, and
   exact service-port peer membership. Refreshed enumeration order does not select
   replacement interfaces. Missing, duplicate, extra, ambiguous, stale, failed, or
   mismatched evidence fails verification. Internal node/component-owned services
   are excluded from the standalone service-set comparison by graph ownership;
   raw duplicate-service checks still include them.

These checks establish provider allocation and binding within the implemented
manifest contract. They do not independently verify every VM property (for
example, capacity, image, or placement), guest state, firmware, or traffic.
The downstream verifier also handles facility-port inputs, but facility-port
rendering is outside this adapter's supported scope.

## 11. Known-good model and rendering examples

The following committed inputs are reusable offline examples. Their existence
does not identify the exact files submitted in the reported live runs. Do not
assume that fixture site mappings are currently available hardware locations.

| Source in this repository | Use and evidence boundary |
| --- | --- |
| [fabric-fpga-l2bridge.yaml](../tests/fixtures/fabric-fpga-l2bridge.yaml) | U280 L2Bridge; source of the downstream C4 offline rendered fixture and the same scenario family as C4.4.1. |
| [fabric-dpu-fabnet-ipv4.yaml](../tests/fixtures/fabric-dpu-fabnet-ipv4.yaml) | ConnectX-7-100 internal IPv4; source of the downstream C4 offline rendered fixture and the same scenario family as C4.4.2. |
| [fabric-dpu-fabnet-ipv6.yaml](../tests/fixtures/fabric-dpu-fabnet-ipv6.yaml) | ConnectX-7-400 internal IPv6 representation. |
| [fabric-dpu-fabnet-l2sts.yaml](../tests/fixtures/fabric-dpu-fabnet-l2sts.yaml) | ConnectX-7-100 FABNet plus two-site L2STS representation. |
| [fabric-dpu-fabnet-l2bridge.yaml](../tests/fixtures/fabric-dpu-fabnet-l2bridge.yaml) | ConnectX-7-400 IPv4Ext plus L2Bridge representation. |
| [fabric-like](../examples/fabric-like/cluster.yaml) and [multi-site](../examples/multi-site/infrastructure.yaml) | Generic Ethernet VM and site-selection examples. |
| [fabric-fpga-l2sts.yaml](../tests/fixtures/fabric-fpga-l2sts.yaml) and [fabric-fpga-fabnet-l2bridge.yaml](../tests/fixtures/fabric-fpga-fabnet-l2bridge.yaml) | Additional SN1022 offline representations; these exact combinations are not in the supplied live acceptance list. |

From the `infrastructure-model` checkout with its package installed, these commands
only render YAML:

```sh
python -m infra_model render fabric tests/fixtures/fabric-fpga-l2bridge.yaml \
  --config adapter-configs/fabric.yaml > /tmp/fabric-fpga-l2bridge-rendered.yaml
python -m infra_model render fabric tests/fixtures/fabric-dpu-fabnet-ipv4.yaml \
  --config adapter-configs/fabric.yaml > /tmp/fabric-dpu-fabnet-ipv4-rendered.yaml
```

The downstream copies live at `tests/fixtures/fabric-fpga-l2bridge-rendered.yaml`
and `tests/fixtures/fabric-dpu-fabnet-ipv4-rendered.yaml` in
`fabric-generic-cluster`. [fabric.yaml](../adapter-configs/fabric.yaml) supplies
the committed fixture mappings. [fabric-live.yaml](../adapter-configs/fabric-live.yaml)
contains additional site/image identity mappings; its name does not imply live
discovery, current image availability, or an accepted deployment configuration.
Exact archived models/configurations for the remaining reported C3 combinations
are **pending capture**.

## 12. Reproducible JupyterHub invocation

Use the existing `fabric-generic-cluster-notebooks/notebooks/create-slice.ipynb`
workflow in a kernel containing the generic model package and downstream C4 code,
including the accessor correction. Existing FABRIC access must already be set up;
no credentials or authentication setup are recorded here. Record exact source
revisions and installed versions before a future authorized run. This procedure
reproduces the invocation shape; exact replay of earlier live environments remains
pending the missing baseline capture.

Capture kernel package versions without contacting FABRIC:

```python
import inspect
import platform
from importlib.metadata import PackageNotFoundError, version
from fabric_generic_cluster import deployment as sd

print("Python:", platform.python_version())
for distribution in (
    "infra-model", "fabric-generic-cluster", "fabrictestbed-extensions",
    "fabrictestbed", "fabric-fim", "pydantic",
):
    try:
        print(distribution, version(distribution))
    except PackageNotFoundError:
        print(distribution, "pending capture: distribution metadata unavailable")

parameters = inspect.signature(sd.deploy_topology_to_fabric).parameters
assert {"provision_only", "strict"} <= parameters.keys(), "C4 deployment API required"
```

For each of the three source checkouts, record `git rev-parse HEAD` and
`git status --short` separately, and verify that the kernel imports those intended
installations. A version string or a checkout unrelated to the import path cannot
establish the installed source revision. Restart the kernel after changing its
installed package so cached imports do not retain earlier code.

Render one selected model with section 11's command in the JupyterHub environment,
then use the existing loader and deployment module:

```python
from pathlib import Path
from fabric_generic_cluster import load_topology_from_yaml_file
from fabric_generic_cluster import deployment as sd

# Select one rendered file from section 11, on the JupyterHub filesystem.
site_topology_yaml = Path("/tmp/fabric-fpga-l2bridge-rendered.yaml")
assert site_topology_yaml.is_file(), "Render the selected model first"
topology = load_topology_from_yaml_file(str(site_topology_yaml))
slice_name = "c45-u280-l2bridge"
```

After reviewing the intended topology and placement, use the call in section 13
for an explicitly authorized live run. For the DPU case, select
`/tmp/fabric-dpu-fabnet-ipv4-rendered.yaml` and a corresponding slice name.
The library may suffix a colliding requested slice name; retain the actual name/ID.
Stop after provision-only deployment. Do not use “Run All”: subsequent existing
notebook cells invoke `configure_l3_networks()`, `enable_public_routing()`, and
`configure_node_interfaces()` independently of the flag passed to deployment.
Those cells are outside this checkpoint's invocation. No live cell is executed
as part of this documentation task.

## 13. Error diagnostics and partial slice recovery

The following deployment example retains error context without dumping the full
topology or automatically retrying, modifying, or deleting a slice:

```python
partial_slice = None
failure_context = None
try:
    slice = sd.deploy_topology_to_fabric(
        topology,
        slice_name,
        provision_only=True,
    )
except sd.SliceDeploymentError as error:
    partial_slice = error.slice  # May be None if construction never began.
    diagnostics = error.diagnostics or {}
    failure_context = {
        "phase": error.phase,
        "slice_name": error.slice_name,
        "slice_id": error.slice_id,
        "mismatches": diagnostics.get("mismatches", []),
        "collection_errors": diagnostics.get("collection_errors", {}),
        "diagnostics": diagnostics,  # Retain locally for selective inspection.
        "cause": error.__cause__,
    }
    print("Deployment verification failed in phase:", error.phase)
    print("Mismatch count:", len(failure_context["mismatches"]))
    print("Partial slice retained:", partial_slice is not None)
    raise  # Preserve the failure; do not continue into configuration cells.
```

The attributes are optional context: preflight failures may lack slice identity
and provider snapshots. Phases include `preflight`, `construction`,
`pre_submission_verification`, `submission`, `provider_refresh`, and
`post_submission_verification`. The full-configuration wrapper can additionally
report `guest_configuration`.

Where available, diagnostics contain `requested_topology`, `expected`, `actual`,
`mismatches`, `slice_state`, and `slivers`, plus best-effort `actual_at_failure`,
`serialized_topology`, `provider_notice`, and `slivers_at_failure`.
Diagnostic `collection_errors` do not replace the original chained exception.
Inspect selected fields locally and retain only reviewed, sanitized evidence in
shared records; avoid copying raw topology dumps or notebook outputs wholesale.

No failed slice is automatically deleted. A submission or refresh failure can
leave allocated resources, and a missing snapshot does not prove absence of an
allocation. Preserve the recorded name/ID and partial object. In a later authorized
provider session, inspect the retained slice or retrieve it by recorded ID before
deciding on repair or explicit deletion. Do not treat resubmitting the same input
as idempotent recovery: name collision handling may create another slice.

## 14. Supported and not-supported boundaries

The supported adapter scope is VM realization with explicit mapped sites/images,
exact representable vCPU/memory/disk demands, and the listed devices/services.
Optional exact VM hosts require `workers` mappings; the adapter does not discover
availability or schedule resources. Rendered OpenStack role flags express intent,
not an installed or operational OpenStack deployment.

Unsupported adapter intent includes bare-metal provisioning, other device models,
FPGA/DPU L2PTP, physical-port selectors, multiple FABNet attachments per VM,
facility-port rendering, unsupported speed/features, and persistent/block-storage
configuration. Generic core capabilities can exceed adapter support; unsupported
translation fails explicitly instead of degrading the request.

Untested live behavior includes resource/service combinations outside the reported
acceptance tables and behavior on other runtime versions. Untested does not mean
unrepresentable. Conversely, provider allocation does not validate guest network
configuration, external routing, FPGA activation, DPU dataplane behavior, or all
FABRIC variants. Observed defects and hardware blocks are tracked separately below.

## 15. Deferred-work register

| Work item | Classification and current boundary | Evidence needed to resume |
| --- | --- | --- |
| DPU guest interface/IP configuration | Deferred guest work, outside provision-only support; not validated by the reported passes. | Separate guest discovery/addressing contract and guest-level acceptance. |
| `configure_l3_networks()` `NoneType` / `len()` failure | Reported observed defect in the downstream guest configuration path; not fixed or reproduced in this pass. | Exact traceback, input, installed versions, source revision, and a focused reproducer; root cause remains pending capture. |
| Two identical DPUs on one VM | Hardware-blocked live acceptance, with earlier partial verification; distinct allocation is representable offline. | Suitable hardware allocation and independent identity/binding evidence for both devices. |
| Physical port selection | Unsupported selector; current bindings are positional. | Explicit generic/provider contract and verified mapping from logical identity to physical port. |
| Multiple FABNet interfaces per VM | Unsupported by the current adapter; one attachment maximum across families. | Downstream discovery/routing work and a separate multi-interface acceptance plan. |
| FPGA programming/dataplane activation | Deferred and outside supported provisioning semantics. | Device-specific programming, drivers, and packet-processing/forwarding acceptance. |
| Unverified FPGA network service combinations | Untested live behavior within the six supported non-L2PTP representations; FPGA L2PTP remains unsupported. | Per-model/service acceptance beyond the exact C3/C4 rows, with retained allocation/binding evidence. |
| Bare-metal modeling and provisioning | Generic bare-metal inventory/intent exists; physical-rack modeling work and provider provisioning remain future work. The FABRIC adapter is VM-only. | Physical inventory, placement/ownership/lifecycle contracts, and a separate provisioning design and acceptance plan. |
| SN1022 / BlueField-2 candidate-host filtering | Previously recorded downstream discovery limitation; successful selected allocations do not establish reliable filtering. | Reinspect discovery behavior at the intended downstream/SDK revision. |

No item in this register is implicitly completed by C4.5. A hardware block,
unsupported feature, untested combination, and observed software defect require
different follow-up evidence and must not be collapsed into one readiness label.

## 16. Guidance for resuming FABRIC work

Start from this checkpoint and the detailed adapter contract. Capture the missing
live baseline before claiming exact reproducibility: all three source revisions
and working-tree state, kernel and package versions, the executed notebook,
generic input, adapter configuration, rendered YAML, run date, and sanitized
provider allocation/membership results. Associate each record with its exact
scenario and distinguish source inspection, offline tests, live allocation, and
guest/dataplane checks.

Choose a specific deferred item and define its success criteria before expanding
scope. Revalidate existing offline behavior when code or dependencies change;
then run only explicitly authorized live acceptance for the relevant resource and
service combinations. Retain failure diagnostics and account for partial slices.
Preserve the provision-only success boundary unless a separately reviewed contract
adds guest or dataplane guarantees. Do not reopen C4.2b as a prerequisite: the
existing notebook invocation already supplies the required integration path.

## 17. Next project phase: physical OpenStack rack and FABRIC/Nix twin

The next phase is to model the physical OpenStack rack and its FABRIC/Nix twin,
building on the provider-neutral inventory/intent model and this bounded FABRIC
provisioning path. Capture actual servers, network adapters, accelerators,
storage, links, and placement constraints; define how physical roles and topology
correspond to a FABRIC VM realization and reproducible Nix configuration.

Treat the physical inventory, bare-metal lifecycle, OpenStack deployment,
Nix configuration, and physical-to-twin correspondence as new design and
acceptance work. This checkpoint supplies a documented provisioning baseline;
it does not claim that the rack, OpenStack services, or the twin are implemented
or operational. No schema, translation, or production Python changes belong to
this stabilization pass.
