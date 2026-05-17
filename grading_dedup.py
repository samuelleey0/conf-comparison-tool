"""Grading summary and Layer 1/Layer 2 deduplication helpers."""

import re


def is_verification_feature(feature: str) -> bool:
    return str(feature or "").startswith("verification.")


def empty_phase1_summary():
    return {
        "correct": 0,
        "missing": 0,
        "extra": 0,
        "mismatch": 0,
        "major": 0,
        "minor": 0,
        "skipped": 0,
        "config_correct": 0,
        "config_mismatch": 0,
        "config_missing": 0,
        "config_extra": 0,
        "config_skipped": 0,
        "verify_correct": 0,
        "verify_failed": 0,
        "verify_skipped": 0,
        "verify_deduplicated": 0,
    }


def _expand_interface_name(name: str) -> str:
    text = str(name or "").strip()
    full_names = (
        "GigabitEthernet",
        "FastEthernet",
        "Serial",
        "Loopback",
        "Vlan",
        "Port-channel",
        "Tunnel",
    )
    if text.startswith(full_names):
        return text

    replacements = (
        ("Gi", "GigabitEthernet"),
        ("Fa", "FastEthernet"),
        ("Se", "Serial"),
        ("Lo", "Loopback"),
        ("Vl", "Vlan"),
        ("Po", "Port-channel"),
        ("Tu", "Tunnel"),
    )
    for short, long_name in replacements:
        match = re.match(rf"^{re.escape(short)}(?=\d)", text)
        if match:
            return long_name + text[match.end() :]
    return text


def config_block_ref(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if not parts:
        return None
    if parts[0] != "show_running_config":
        return None

    if len(parts) >= 3 and parts[1] == "interfaces":
        return ".".join(parts[:3])
    if (
        len(parts) >= 5
        and parts[1] == "switching"
        and parts[2] == "etherchannel"
        and parts[3] == "groups"
    ):
        return ".".join(parts[:5])
    if len(parts) >= 3 and parts[1] in {
        "routing",
        "access_lists",
        "users",
        "dhcp_pools",
    }:
        return ".".join(parts[:3])
    if len(parts) >= 3 and parts[1] == "switching":
        return ".".join(parts[:3])
    if len(parts) >= 3 and parts[1] == "vty":
        return ".".join(parts[:3])
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return ".".join(parts)


def _verification_layer1_ref(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 2 or parts[0] != "verification":
        return None

    routing_command_map = {
        "show_ip_eigrp_neighbor": "show_running_config.routing.eigrp",
        "show_ip_eigrp_topology": "show_running_config.routing.eigrp",
        "show_ip_eigrp_interfaces": "show_running_config.routing.eigrp",
        "show_ip_ospf_neighbor": "show_running_config.routing.ospf",
        "show_ip_ospf_database": "show_running_config.routing.ospf",
        "show_ip_ospf_interface": "show_running_config.routing.ospf",
        "show_ip_rip_database": "show_running_config.routing.rip",
        "show_ip_route_static": "show_running_config.routing.static_routes",
    }
    if parts[1] in routing_command_map:
        return routing_command_map[parts[1]]

    if (
        len(parts) >= 4
        and parts[1] == "show_ip_interface_brief"
        and parts[2] == "interfaces"
    ):
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    if len(parts) >= 4 and parts[1] == "show_interfaces_trunk" and parts[2] == "trunks":
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    if (
        len(parts) >= 4
        and parts[1] == "show_port_security"
        and parts[2] == "interfaces"
    ):
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    if len(parts) >= 4 and parts[1] == "show_access_lists" and parts[2] == "acls":
        return f"show_running_config.access_lists.{parts[3]}"

    if (
        len(parts) >= 4
        and parts[1] == "show_etherchannel_summary"
        and parts[2] == "groups"
    ):
        return f"show_running_config.switching.etherchannel.groups.{parts[3]}"

    if parts[1] in {"show_ip_nat_statistics", "show_ip_nat_translations"}:
        return "show_running_config.nat"

    if parts[1] in {"show_ip_dhcp_binding", "show_ip_dhcp_pool"}:
        return "show_running_config.dhcp_pools"

    if parts[1] == "show_ip_route":
        if len(parts) >= 3 and parts[2] in {"eigrp", "ospf", "rip"}:
            return f"show_running_config.routing.{parts[2]}"
        if len(parts) >= 3 and parts[2] == "static":
            return "show_running_config.routing.static_routes"
        return "show_running_config.routing"

    if parts[1] == "show_vlan_brief":
        return "show_running_config.__vlan_scheme__"

    return None


def verification_block_name(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 2:
        return str(feature or "")

    command = parts[1]
    if command in {
        "show_ip_eigrp_neighbor",
        "show_ip_eigrp_topology",
        "show_ip_eigrp_interfaces",
        "show_ip_ospf_neighbor",
        "show_ip_ospf_database",
        "show_ip_ospf_interface",
        "show_ip_rip_database",
        "show_ip_route_static",
    }:
        if len(parts) >= 3 and parts[2] in {"eigrp", "ospf", "rip", "static"}:
            return f"{command}.{parts[2]}"
        return command
    if (
        command
        in {"show_ip_interface_brief", "show_interfaces_trunk", "show_port_security"}
        and len(parts) >= 4
    ):
        return f"{command}.{parts[3]}"
    if (
        command in {"show_access_lists", "show_etherchannel_summary"}
        and len(parts) >= 4
    ):
        return f"{command}.{parts[3]}"
    if command == "show_vlan_brief":
        return "show_vlan_brief.vlan_scheme"
    if command == "show_ip_route":
        if len(parts) >= 3 and parts[2] in {
            "eigrp",
            "ospf",
            "rip",
            "static",
            "gateway",
        }:
            return f"show_ip_route.{parts[2]}"
        return "show_ip_route.routing_table"
    return command


def verification_is_deduplicated(feature: str, failed_config_refs, failed_config_features):
    layer1_ref = _verification_layer1_ref(feature)
    if layer1_ref and layer1_ref.startswith("show_running_config.routing."):
        if any(
            failed_ref == "show_running_config.routing.protocol"
            for failed_ref in failed_config_refs
        ) or any(
            str(failed_feature).startswith("show_running_config.routing.protocol")
            for failed_feature in failed_config_features
        ):
            return True, layer1_ref
    if layer1_ref and layer1_ref != "show_running_config.__vlan_scheme__":
        for failed_ref in failed_config_refs:
            if (
                failed_ref == layer1_ref
                or failed_ref.startswith(layer1_ref)
                or layer1_ref.startswith(failed_ref)
            ):
                return True, layer1_ref

    if str(feature or "").startswith("verification.show_vlan_brief."):
        for failed_feature in failed_config_features:
            if any(
                token in failed_feature
                for token in [
                    ".access_vlan",
                    ".switchport_mode",
                    ".trunk_native_vlan",
                    ".trunk_allowed_vlans",
                    ".Vlan.interface",
                    ".subinterface",
                ]
            ):
                return True, "show_running_config.__vlan_scheme__"

    if str(feature or "").startswith("verification.show_ip_route."):
        for failed_feature in failed_config_features:
            if failed_feature.startswith(
                "show_running_config.routing"
            ) or failed_feature.startswith(
                "show_running_config.switching.default_gateway"
            ):
                return True, "show_running_config.routing"

    return False, layer1_ref
