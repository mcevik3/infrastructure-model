"""Mechanical historical YAML dictionary rendering; no topology resolution."""

from .plan import _FabricNodePlan, _FabricRenderPlan


def _guest_interface_names(ordinal: int) -> tuple[str, str]:
    """Guest names count interfaces across components, including future multi-port NICs."""
    return f"eth{ordinal}", f"conn-eth{ordinal}"


def _node(plan: _FabricNodePlan) -> dict:
    components = {}
    ordinal = 0
    for index, component in enumerate(plan.components, 1):
        name = f"nic{index}"
        interfaces = {}
        for interface_index, interface in enumerate(component.interfaces, 1):
            ordinal += 1
            device, connection = _guest_interface_names(ordinal)
            data = {"device": device, "connection": connection, "binding": interface.network_name}
            for address in interface.addresses:
                family = {"address": f"{address.address}/{address.prefix_length}"}
                if address.gateway is not None:
                    family["gateway"] = address.gateway
                if address.dns is not None:
                    family["dns"] = address.dns
                data[f"ipv{address.family}"] = family
            interfaces[f"iface{interface_index}"] = data
        components[name] = {"name": name, "model": component.model, "interfaces": interfaces}
    return {
        "name": plan.name, "hostname": plan.name, "site": plan.site,
        "worker": plan.worker if plan.worker is not None else "",
        "capacity": {"cpu": plan.cpu, "ram": plan.ram, "disk": plan.disk, "os": plan.image},
        "pci": {"dpu": {}, "fpga": {}, "gpu": {}, "nvme": {}, "network": components},
        "persistent_storage": {"volume": {}},
        "specific": {"openstack": {role: "true" if role in plan.roles else "false"
                                   for role in ("control", "network", "compute", "storage")}},
    }


def _render(plan: _FabricRenderPlan) -> dict:
    networks = {}
    for index, network in enumerate(plan.networks, 1):
        data = {"name": network.name, "type": network.service}
        if network.prefixes:
            subnet = {}
            for prefix in network.prefixes:
                family = {"address": prefix.address}
                if prefix.gateway is not None:
                    family["gateway"] = prefix.gateway
                subnet[f"ipv{prefix.family}"] = family
            data["subnet"] = subnet
        networks[f"net{index}"] = data
    return {
        "site_topology_nodes": {"nodes": {f"node{index}": _node(node) for index, node in enumerate(plan.nodes, 1)}},
        "site_topology_networks": {"networks": networks},
        "site_topology_facility_ports": {"facility_ports": {}},
    }
