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
