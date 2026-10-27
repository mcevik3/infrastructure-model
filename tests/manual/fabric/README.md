# Manual FABRIC Acceptance Topologies

These topology files exercise the provider-neutral infrastructure model against
the offline FABRIC adapter and the existing fabric-generic-cluster workflow.

They are kept outside the normal `examples/` tree because successful deployment
depends on current FABRIC resource availability, project permissions, and site
inventory.

## Topologies

### gpu-basic.yaml

One Rocky Linux 9 VM with:

- 4 vCPU
- 16 GiB RAM
- 20 GiB root disk
- NVIDIA A30 GPU
- ordinary Ethernet management attachment

Expected FABRIC components:

- GPU_A30
- NIC_Basic

### gpu-dedicated-nic.yaml

One Rocky Linux 9 VM with:

- NVIDIA A30 GPU
- ordinary management Ethernet
- dedicated ConnectX-6 data interface

Expected FABRIC components:

- GPU_A30
- NIC_Basic
- NIC_ConnectX_6

### dpu-allocation.yaml

One Rocky Linux 9 VM with:

- allocation-only NVIDIA ConnectX-7-100 DPU
- ordinary management Ethernet

Expected FABRIC components:

- NIC_ConnectX_7_100 under pci.dpu
- NIC_Basic under pci.network

DPU-backed NetworkAttachments are intentionally not modeled here.

## Validate

Run each topology independently:

    infra-model validate tests/manual/fabric/gpu-basic.yaml

    infra-model validate tests/manual/fabric/gpu-dedicated-nic.yaml

    infra-model validate tests/manual/fabric/dpu-allocation.yaml

## Render

GPU + Basic NIC:

    infra-model render fabric tests/manual/fabric/gpu-basic.yaml \
      --config adapter-configs/fabric.yaml \
      > /tmp/fabric-gpu-basic.yml

GPU + dedicated ConnectX-6:

    infra-model render fabric tests/manual/fabric/gpu-dedicated-nic.yaml \
      --config adapter-configs/fabric.yaml \
      > /tmp/fabric-gpu-dedicated-nic.yml

DPU allocation:

    infra-model render fabric tests/manual/fabric/dpu-allocation.yaml \
      --config adapter-configs/fabric.yaml \
      > /tmp/fabric-dpu-allocation.yml

The generated YAML can then be supplied directly to the existing
fabric-generic-cluster workflow.

## Site selection

The source topology uses the generic identity:

    site/lab-site

The FABRIC site is selected through:

    adapter-configs/fabric.yaml

Change the adapter mapping when testing at another FABRIC site rather than
changing provider-neutral placement semantics in these topology files.

For hardware-specific tests, choose a FABRIC site where all requested resources
are currently available and permitted for the project.

A successful offline render does not prove:

- hardware availability
- project permission for GPUs/DPUs
- host schedulability
- successful slice provisioning

## FPGA binding acceptance (C3.2)

C3.2 is offline allocation identity and NetworkService binding support, based on
the approved C3.1 read-only review. No live FPGA acceptance was performed.
The focused fixtures are:

- [fabric-fpga-l2bridge.yaml](../../fixtures/fabric-fpga-l2bridge.yaml): U280, one logical interface, addressless L2Bridge.
- [fabric-fpga-l2sts.yaml](../../fixtures/fabric-fpga-l2sts.yaml): SN1022, one logical interface, addressless L2STS.
- [fabric-fpga-fabnet-l2bridge.yaml](../../fixtures/fabric-fpga-fabnet-l2bridge.yaml): SN1022, FABNet plus authored L2 addressing on two logical interfaces.
- [fabric-fpga-two-devices.yaml](../../fixtures/fabric-fpga-two-devices.yaml): two named identical U280 allocations with interleaved attachments.
- [fabric-fpga-allocation-only.yaml](../../fixtures/fabric-fpga-allocation-only.yaml): unbound FPGAs with count expansion and other PCI families; its preserved golden was rendered before C3.2.

For example, validate and render offline:

```sh
python -m infra_model validate tests/fixtures/fabric-fpga-l2bridge.yaml
python -m infra_model render fabric tests/fixtures/fabric-fpga-l2bridge.yaml \
  --config tests/adapter-configs/fabric.yaml > /tmp/fabric-fpga-l2bridge.yml
```

`deviceRequirementRef` must resolve locally or canonically to a same-node FPGA
requirement with effective count one. Attachments reuse that exact allocation;
two references do not request two cards. Separately named identical models remain
separate cards. `fpgaN` retains device requirement order, `ifaceN` follows attachment
order within the FPGA, and `ethN`/`conn-ethN` follow node-wide attachment order.
Unbound FPGA output still omits `interfaces` and preserves allocation-only bytes.

Supported selectors are exactly `U280`, existing alias `Alveo U280`, and `SN1022`,
mapping to `FPGA_Xilinx_U280` and `FPGA_Xilinx_SN1022`. Vendor is omitted, `AMD`, or
`Xilinx`. Attachments require Ethernet and omit `adapter`: no explicit adapter
class is supported for FPGA. Contradictory selectors, speeds, nonempty features,
unsupported models/services, and a third bound interface are rejected.

The inspected FIM 1.9.0 catalog exposes two network endpoints per model. These
support two logical positional bindings, not a guarantee of two working FPGA
datapaths. Downstream enumerates provider interfaces without a guaranteed physical
`p1`/`p2` order; `iface1` may bind the interface returned as `p2`. There is no
physical-port selector. SN1022's second-port dataplane behavior remains uncertain.

The six offline representations `L2Bridge`, `L2STS`, `IPv4`, `IPv6`, `IPv4Ext`, and
`IPv6Ext` each need separate live acceptance under their existing site, endpoint,
and addressing rules. At most one FABNet NetworkAttachment per VM is supported
across FPGA, DPU, and NIC families. FPGA L2PTP remains unsupported; L2PTP requires
addressless ConnectX-6 interfaces, exactly two endpoints, and two mapped sites.
Addressless L2 requires no fabricated addresses; authored L2 addresses are preserved.

When separately authorized live acceptance is possible, inspect the actual
component names/models/counts and each NetworkService's interface memberships.
Downstream deployment can log and skip component or binding failures; a returned
topology or successful submission alone is insufficient. Inspect actual interface
enumeration and record which endpoint was bound without assuming physical port
order. Allocation and membership checks do not establish FPGA activation or
packet flow; pre-activation link-down alone does not disprove allocation/binding.
Pydantic parsing and mocked downstream calls establish only offline behavior.

Bitstream installation/programming, FPGA MAC/packet-processing pipelines,
drivers/kernel modules, FPGA ARM management, guest interface discovery and IP
configuration, persistent routes/VLANs, public-routing activation, forwarding,
and throughput testing are excluded from C3.2. Availability, permissions, and
SN1022 host-discovery behavior require independent validation. C2.4.5's two-DPU
live test remains **hardware-blocked and partially verified**; these offline FPGA
tests do not complete it.
