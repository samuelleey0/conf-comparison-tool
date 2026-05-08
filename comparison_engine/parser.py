"""
Cisco command-output parser for the comparison engine.

This module detects Cisco show-command log types, parses show running-config and
verification command outputs, and normalizes the result into the canonical
schema consumed by comparator.py and server.py.
"""
import os
import re
import json
import ipaddress

from comparison_engine.compare_utils import should_ignore


PARSED_SCHEMA_VERSION = 3

COMMAND_ERROR_PATTERNS = [
    r"^%\s*incomplete command",
    r"^%\s*invalid input",
    r"^%\s*invalid command",
    r"^%\s*unrecognized command",
    r"^%\s*ambiguous command",
    r"^%\s*unknown command",
]


def _split_interface_list(value):
    """Normalize interface collections from strings, lists, or single values."""
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return sorted(set(cleaned))
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,]+", value)
        cleaned = [part.strip() for part in parts if part.strip()]
        return sorted(set(cleaned))
    return [str(value).strip()]


def normalize_parsed_config(parsed):
    """Normalize parsed output into the canonical comparison schema."""
    if not isinstance(parsed, dict):
        parsed = {}

    # Backward compatibility: older templates might store only show run object.
    if "show_running_config" not in parsed or "verification" not in parsed:
        parsed = {
            "show_running_config": parsed if isinstance(parsed, dict) else None,
            "verification": {},
        }

    # Work on a deep copy to avoid mutating caller references.
    normalized = json.loads(json.dumps(parsed))
    normalized.setdefault("show_running_config", None)
    normalized.setdefault("verification", {})

    verification = normalized.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}
        normalized["verification"] = verification

    # Normalize show access-lists rules by removing runtime match counters.
    acl_section = verification.get("show_access_lists", {})
    if isinstance(acl_section, dict):
        acls = acl_section.get("acls", {})
        if isinstance(acls, dict):
            for acl_name, rules in acls.items():
                if not isinstance(rules, list):
                    continue
                cleaned_rules = []
                for rule in rules:
                    rule_text = str(rule).strip()
                    rule_text = re.sub(r"\s*\(\d+\s+matches?\)\s*$", "", rule_text)
                    if rule_text:
                        cleaned_rules.append(rule_text)
                acls[acl_name] = sorted(cleaned_rules)

    # Normalize NAT statistics legacy and modern representation.
    nat_stats = verification.get("show_ip_nat_statistics", {})
    if isinstance(nat_stats, dict):
        volatile_keys = {
            "total_active_translations",
            "hits",
            "cef_translated_packets",
            "expired_translations",
            "dynamic_mappings",
            "id",
            "appl_doors",
            "normal_doors",
            "queued_packets",
        }
        for key in volatile_keys:
            nat_stats.pop(key, None)

        nat_stats["outside_interfaces"] = _split_interface_list(
            nat_stats.get("outside_interfaces", [])
        )
        nat_stats["inside_interfaces"] = _split_interface_list(
            nat_stats.get("inside_interfaces", [])
        )

        # Preserve pool netmask from legacy flattened key style (pool_<name>: "netmask ...").
        pools = nat_stats.get("pools", {})
        if not isinstance(pools, dict):
            pools = {}
        for key, value in list(nat_stats.items()):
            if not str(key).startswith("pool_"):
                continue
            pool_name = str(key).replace("pool_", "", 1)
            pool_info = pools.setdefault(pool_name, {})
            value_text = str(value)
            netmask_match = re.search(r"netmask\s+(\S+)", value_text, re.IGNORECASE)
            if netmask_match:
                pool_info["netmask"] = netmask_match.group(1)
            nat_stats.pop(key, None)
        nat_stats["pools"] = pools

        mappings = nat_stats.get("mappings", [])
        if isinstance(mappings, list):
            nat_stats["mappings"] = sorted(
                mappings, key=lambda item: (item.get("acl", ""), item.get("pool", ""))
            )
        else:
            nat_stats["mappings"] = []

    # Normalize route identity field name.
    ip_route = verification.get("show_ip_route", {})
    if isinstance(ip_route, dict):
        routes = ip_route.get("routes", [])
        if isinstance(routes, list):
            for route in routes:
                if (
                    isinstance(route, dict)
                    and "destination" not in route
                    and "prefix" in route
                ):
                    route["destination"] = route.pop("prefix")

    # Normalize NAT translations into lightweight "tested" signal.
    nat_translations = verification.get("show_ip_nat_translations", {})
    if isinstance(nat_translations, dict):
        if "tested" not in nat_translations:
            translations = nat_translations.get("translations", [])
            nat_translations = {
                "tested": bool(isinstance(translations, list) and translations),
            }
            verification["show_ip_nat_translations"] = nat_translations

    # Normalize DHCP bindings by keeping assigned IP evidence only.
    dhcp_binding = verification.get("show_ip_dhcp_binding", {})
    if isinstance(dhcp_binding, dict):
        if "assigned_ips" not in dhcp_binding:
            bindings = dhcp_binding.get("bindings", [])
            assigned_ips = []
            if isinstance(bindings, list):
                for entry in bindings:
                    if isinstance(entry, dict):
                        ip_value = str(entry.get("ip", "")).strip()
                    else:
                        ip_value = ""
                    if ip_value:
                        assigned_ips.append(ip_value)
            dhcp_binding = {
                "has_assignments": bool(assigned_ips),
                "assigned_ips": sorted(set(assigned_ips)),
            }
            verification["show_ip_dhcp_binding"] = dhcp_binding

    # Normalize DHCP pool verification into lightweight test signal.
    dhcp_pool = verification.get("show_ip_dhcp_pool", {})
    if isinstance(dhcp_pool, dict):
        if "tested" not in dhcp_pool:
            pools = dhcp_pool.get("pools", {})
            pool_names = []
            if isinstance(pools, dict):
                pool_names = sorted(str(name) for name in pools.keys())
            dhcp_pool = {
                "tested": bool(pool_names),
                "pool_names": pool_names,
            }
            verification["show_ip_dhcp_pool"] = dhcp_pool

    # Normalize EIGRP neighbors to stable identity fields only.
    eigrp_neighbor = verification.get("show_ip_eigrp_neighbor", {})
    if isinstance(eigrp_neighbor, dict):
        neighbors = eigrp_neighbor.get("neighbors", [])
        compact_neighbors = []
        if isinstance(neighbors, list):
            for item in neighbors:
                if not isinstance(item, dict):
                    continue
                address = str(item.get("address", "")).strip()
                interface = str(item.get("interface", "")).strip()
                if address and interface:
                    compact_neighbors.append(
                        {"address": address, "interface": interface}
                    )

        eigrp_neighbor["neighbors"] = sorted(
            compact_neighbors,
            key=lambda entry: (entry.get("address", ""), entry.get("interface", "")),
        )

    eigrp_topology = verification.get("show_ip_eigrp_topology", {})
    if isinstance(eigrp_topology, dict):
        routes = eigrp_topology.get("routes", [])
        compact_routes = []
        if isinstance(routes, list):
            for item in routes:
                if not isinstance(item, dict):
                    continue
                destination = str(item.get("destination", "")).strip()
                state = str(item.get("state", "")).strip()
                successors = item.get("successors")
                via_entries = []
                for via in item.get("via", []) or []:
                    if not isinstance(via, dict):
                        continue
                    interface = str(via.get("interface", "")).strip()
                    address = str(via.get("address", "")).strip()
                    if interface or address:
                        via_entries.append({"address": address, "interface": interface})
                if destination:
                    compact_routes.append(
                        {
                            "destination": destination,
                            "state": state,
                            "successors": successors,
                            "via": sorted(
                                via_entries,
                                key=lambda entry: (
                                    entry.get("interface", ""),
                                    entry.get("address", ""),
                                ),
                            ),
                        }
                    )
        eigrp_topology["routes"] = sorted(
            compact_routes,
            key=lambda entry: entry.get("destination", ""),
        )

    eigrp_interfaces = verification.get("show_ip_eigrp_interfaces", {})
    if isinstance(eigrp_interfaces, dict):
        interfaces = eigrp_interfaces.get("interfaces", [])
        compact_interfaces = []
        if isinstance(interfaces, list):
            for item in interfaces:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if name:
                    compact_interfaces.append(
                        {
                            "name": name,
                            "peers": item.get("peers"),
                        }
                    )
        eigrp_interfaces["interfaces"] = sorted(
            compact_interfaces, key=lambda entry: entry.get("name", "")
        )

    # Normalize OSPF neighbors to stable identity fields only.
    ospf_neighbor = verification.get("show_ip_ospf_neighbor", {})
    if isinstance(ospf_neighbor, dict):
        neighbors = ospf_neighbor.get("neighbors", [])
        compact_neighbors = []
        if isinstance(neighbors, list):
            for item in neighbors:
                if not isinstance(item, dict):
                    continue
                address = str(item.get("address", "")).strip()
                interface = str(item.get("interface", "")).strip()
                if address and interface:
                    compact_neighbors.append(
                        {
                            "address": address,
                            "interface": interface,
                            "state": str(item.get("state", "")).strip(),
                        }
                    )

        ospf_neighbor["neighbors"] = sorted(
            compact_neighbors,
            key=lambda entry: (
                entry.get("address", ""),
                entry.get("interface", ""),
                entry.get("state", ""),
            ),
        )

    ospf_database = verification.get("show_ip_ospf_database", {})
    if isinstance(ospf_database, dict):
        lsa_types = ospf_database.get("lsa_types", {})
        if not isinstance(lsa_types, dict):
            lsa_types = {}
        ospf_database["lsa_types"] = {
            "router": int(lsa_types.get("router", 0) or 0),
            "network": int(lsa_types.get("network", 0) or 0),
            "summary_net": int(lsa_types.get("summary_net", 0) or 0),
            "summary_asbr": int(lsa_types.get("summary_asbr", 0) or 0),
            "as_external": int(lsa_types.get("as_external", 0) or 0),
        }

    ospf_interface = verification.get("show_ip_ospf_interface", {})
    if isinstance(ospf_interface, dict):
        interfaces = ospf_interface.get("interfaces", [])
        compact_interfaces = []
        if isinstance(interfaces, list):
            for item in interfaces:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if name:
                    compact_interfaces.append(
                        {
                            "name": name,
                            "area": str(item.get("area", "")).strip(),
                            "network_type": str(item.get("network_type", "")).strip(),
                            "state": str(item.get("state", "")).strip(),
                        }
                    )
        ospf_interface["interfaces"] = sorted(
            compact_interfaces, key=lambda entry: entry.get("name", "")
        )

    rip_database = verification.get("show_ip_rip_database", {})
    if isinstance(rip_database, dict):
        routes = rip_database.get("routes", [])
        compact_routes = []
        if isinstance(routes, list):
            for item in routes:
                if not isinstance(item, dict):
                    continue
                destination = str(item.get("destination", "")).strip()
                if destination:
                    compact_routes.append(
                        {
                            "destination": destination,
                            "metric": int(item.get("metric", 0) or 0),
                            "possibly_down": bool(item.get("possibly_down", False)),
                        }
                    )
        rip_database["routes"] = sorted(
            compact_routes, key=lambda entry: entry.get("destination", "")
        )

    route_static = verification.get("show_ip_route_static", {})
    if isinstance(route_static, dict):
        routes = route_static.get("routes", [])
        compact_routes = []
        if isinstance(routes, list):
            for item in routes:
                if not isinstance(item, dict):
                    continue
                destination = str(item.get("destination", "")).strip()
                if destination:
                    compact_routes.append(
                        {
                            "destination": destination,
                            "mask": str(item.get("mask", "")).strip(),
                            "next_hop": str(item.get("next_hop", "")).strip(),
                            "interface": str(item.get("interface", "")).strip(),
                        }
                    )
        route_static["routes"] = sorted(
            compact_routes,
            key=lambda entry: (entry.get("destination", ""), entry.get("mask", "")),
        )

    normalized["schema_version"] = PARSED_SCHEMA_VERSION
    return normalized


COMMAND_TOKENS = {
    "show_running_config": [
        "show running-config",
        "show_running-config",
        "show_running_config",
        "show run",
        "sh run",
        "showrun",
        "shrun",
    ],
    "show_ip_interface_brief": [
        "show ip interface brief",
        "show_ip_interface_brief",
        "sh ip int br",
        "sh ip interface brief",
    ],
    "show_ip_route": ["show ip route", "show_ip_route", "sh ip route"],
    "show_access_lists": [
        "show access-lists",
        "show access list",
        "show_access_list",
        "show_access_lists",
        "sh access-list",
        "sh access list",
    ],
    "show_ip_nat_statistics": [
        "show ip nat statistics",
        "show_ip_nat_statistics",
        "sh ip nat statistics",
    ],
    "show_ip_nat_translations": [
        "show ip nat translations",
        "show_ip_nat_translations",
        "sh ip nat translations",
    ],
    "show_ip_dhcp_binding": [
        "show ip dhcp binding",
        "show_ip_dhcp_binding",
        "sh ip dhcp binding",
    ],
    "show_ip_dhcp_pool": [
        "show ip dhcp pool",
        "show_ip_dhcp_pool",
        "sh ip dhcp pool",
    ],
    "show_ip_eigrp": [
        "show ip eigrp",
        "show_ip_eigrp",
        "sh ip eigrp",
    ],
    "show_ip_eigrp_neighbor": [
        "show ip eigrp neighbor",
        "show_ip_eigrp_neighbor",
        "sh ip eigrp neighbor",
        "sh ip eigrp neigh",
    ],
    "show_ip_eigrp_topology": [
        "show ip eigrp topology",
        "show_ip_eigrp_topology",
        "sh ip eigrp topology",
    ],
    "show_ip_eigrp_interfaces": [
        "show ip eigrp interfaces",
        "show_ip_eigrp_interfaces",
        "sh ip eigrp interfaces",
    ],
    "show_ip_ospf": [
        "show ip ospf",
        "show_ip_ospf",
        "sh ip ospf",
    ],
    "show_ip_ospf_neighbor": [
        "show ip ospf neighbor",
        "show_ip_ospf_neighbor",
        "sh ip ospf neighbor",
        "sh ip ospf neigh",
    ],
    "show_ip_ospf_database": [
        "show ip ospf database",
        "show_ip_ospf_database",
        "sh ip ospf database",
    ],
    "show_ip_ospf_interface": [
        "show ip ospf interface",
        "show_ip_ospf_interface",
        "sh ip ospf interface",
    ],
    "show_ip_rip_database": [
        "show ip rip database",
        "show_ip_rip_database",
        "sh ip rip database",
    ],
    "show_ip_route_static": [
        "show ip route static",
        "show_ip_route_static",
        "sh ip route static",
    ],
    "show_interfaces_trunk": [
        "show interfaces trunk",
        "show_interfaces_trunk",
        "sh int trunk",
        "sh interfaces trunk",
    ],
    "show_vlan_brief": [
        "show vlan brief",
        "show_vlan_brief",
        "sh vlan brief",
        "sh vlan br",
    ],
    "show_port_security": [
        "show port-security",
        "show port security",
        "show_port_security",
        "sh port-security",
        "sh port security",
    ],
    "show_spanning_tree": [
        "show spanning-tree",
        "show spanning tree",
        "show_spanning_tree",
        "sh spanning-tree",
        "sh spanning tree",
    ],
    "show_etherchannel_summary": [
        "show etherchannel summary",
        "show_etherchannel_summary",
        "sh etherchannel summary",
    ],
    "show_pagp_neighbor": [
        "show pagp neighbor",
        "show_pagp_neighbor",
        "sh pagp neighbor",
    ],
    "show_lacp_neighbor": [
        "show lacp neighbor",
        "show_lacp_neighbor",
        "sh lacp neighbor",
    ],
}


def _normalize_text(value):
    """Normalize command/file text for flexible token matching."""
    lowered = value.lower()
    for char in ["_", "-", ".", "(", ")", "[", "]"]:
        lowered = lowered.replace(char, " ")
    return " ".join(lowered.split())


def _is_device_prompt(line):
    """Return True when a line looks like a Cisco CLI prompt."""
    stripped = line.strip()
    return stripped.endswith("#") or stripped.endswith(">")


def _clean_log_lines(file_path):
    """Read a log file using replacement decoding and return stripped lines."""
    # Some collected logs may contain non-UTF8 bytes.
    # Use replacement decoding so one bad byte does not break grading.
    with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
        lines = [line.rstrip("\n") for line in handle]
    return lines


def detect_command_type(file_path):
    """Infer which supported Cisco show command a log file contains."""
    name = _normalize_text(os.path.basename(file_path))
    token_candidates = []
    for command, tokens in COMMAND_TOKENS.items():
        for token in tokens:
            token_candidates.append((command, token))

    # Match more specific command tokens first to avoid prefix collisions
    # (e.g. "show ip eigrp neighbor" should not be detected as "show ip eigrp").
    token_candidates.sort(key=lambda item: len(item[1]), reverse=True)

    for command, token in token_candidates:
        if token in name:
            return command

    lines = _clean_log_lines(file_path)
    header = " ".join(line.strip().lower() for line in lines[:5] if line.strip())
    for command, token in token_candidates:
        if token in header:
            return command

    return None


def detect_command_error(file_path):
    """Return first command execution error line if the log captured a CLI error."""
    lines = _clean_log_lines(file_path)
    for line in lines[:25]:
        stripped = line.strip()
        lower = stripped.lower()
        if _is_device_prompt(stripped):
            continue
        if lower.startswith("show ") or lower.startswith("sh "):
            continue
        for pattern in COMMAND_ERROR_PATTERNS:
            if re.search(pattern, stripped, flags=re.IGNORECASE):
                return stripped
    return None


def parse_showrun(file_path):
    """Parses show running-config into a nested dictionary."""
    config = {
        "hostname": None,
        "interfaces": {},
        "banner_motd": None,
        "vty": {},
        "console": {},
        "users": [],
        "access_lists": {},
        "nat": {
            "inside_interfaces": [],
            "outside_interfaces": [],
            "pools": [],
            "inside_source": [],
        },
        "dhcp_pools": {},
        "dhcp_excluded": [],
        "routing": {"eigrp": [], "ospf": [], "rip": [], "static_routes": []},
        "http_server": {},
        "switching": {
            "default_gateway": None,
            "spanning_tree": {},
            "vlan_internal_allocation_policy": None,
            "etherchannel": {"groups": {}},
        },
        "etherchannel": {
            "load_balance": None,
        },
        "lacp_system_priority": None,
    }

    # Context variables to track which configuration block we're in
    current_interface = None
    current_pool = None
    current_acl = None
    current_eigrp = None
    current_ospf = None
    current_rip = None
    current_line = None

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or should_ignore(line):
                continue

            # Reset all context when encountering "!" (section delimiter)
            if line.startswith("!"):
                current_interface = None
                current_pool = None
                current_acl = None
                current_eigrp = None
                current_ospf = None
                current_rip = None
                current_line = None
                continue

            # Top-level configuration parsing
            if line.startswith("hostname"):
                parts = line.split()
                if len(parts) > 1:
                    config["hostname"] = parts[1]
                continue

            # DHCP excluded addresses
            if line.startswith("ip dhcp excluded-address"):
                parts = line.split()
                if len(parts) >= 3:
                    excluded = {
                        "start": parts[3],
                        "end": parts[4] if len(parts) > 4 else parts[3],
                    }
                    config["dhcp_excluded"].append(excluded)
                continue

            # NAT pool configuration
            if line.startswith("ip nat pool"):
                parts = line.split()
                if len(parts) >= 5:
                    pool_entry = {
                        "name": parts[3],
                        "start_ip": parts[4],
                        "end_ip": parts[5],
                    }
                    # Extract netmask if present
                    if "netmask" in parts:
                        idx = parts.index("netmask")
                        if idx + 1 < len(parts):
                            pool_entry["netmask"] = parts[idx + 1]
                    config["nat"]["pools"].append(pool_entry)
                continue

            # NAT inside source configuration
            if line.startswith("ip nat inside source"):
                nat_entry = {"command": line}
                # Parse components: list, pool, overload, etc.
                if "list" in line:
                    parts = line.split()
                    if "list" in parts:
                        list_idx = parts.index("list")
                        if list_idx + 1 < len(parts):
                            nat_entry["acl"] = parts[list_idx + 1]
                    if "pool" in parts:
                        pool_idx = parts.index("pool")
                        if pool_idx + 1 < len(parts):
                            nat_entry["pool"] = parts[pool_idx + 1]
                    if "overload" in parts:
                        nat_entry["overload"] = True
                config["nat"]["inside_source"].append(nat_entry)
                continue

            # Static routes
            if line.startswith("ip route"):
                parts = line.split()
                if len(parts) >= 4:
                    route = {
                        "network": parts[2],
                        "mask": parts[3],
                        "next_hop": parts[4] if len(parts) > 4 else None,
                    }
                    config["routing"]["static_routes"].append(route)
                continue

            # HTTP server configuration
            if line.startswith("ip http"):
                if "ip http server" in line:
                    config["http_server"]["enabled"] = True
                elif "ip http secure-server" in line:
                    config["http_server"]["secure_server"] = True
                elif "ip http authentication" in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        config["http_server"]["authentication"] = parts[3]
                elif "no ip http secure-server" in line:
                    config["http_server"]["secure_server"] = False
                continue

            # Layer-2 / switch global configuration
            if line.startswith("spanning-tree mode"):
                parts = line.split()
                if len(parts) >= 3:
                    config["switching"]["spanning_tree"]["mode"] = parts[2]
                continue

            if line == "spanning-tree extend system-id":
                config["switching"]["spanning_tree"]["extend_system_id"] = True
                continue

            if line.startswith("spanning-tree vlan"):
                config["switching"]["spanning_tree"].setdefault(
                    "vlan_settings", []
                ).append(line)
                continue

            if line.startswith("ip default-gateway"):
                parts = line.split()
                if len(parts) >= 3:
                    config["switching"]["default_gateway"] = parts[2]
                continue

            if line.startswith("vlan internal allocation policy"):
                config["switching"]["vlan_internal_allocation_policy"] = line
                continue

            # Interface block start
            if line.startswith("interface"):
                current_interface = line.split()[1]
                iface_data = config["interfaces"].setdefault(current_interface, {})
                if "." in current_interface:
                    parent, sub_id = current_interface.split(".", 1)
                    iface_data["is_subinterface"] = True
                    iface_data["parent_interface"] = parent
                    iface_data["subinterface_id"] = sub_id
                current_pool = None
                current_acl = None
                current_eigrp = None
                current_ospf = None
                current_rip = None
                current_line = None
                continue

            # DHCP pool block start
            if line.startswith("ip dhcp pool"):
                current_pool = line.replace("ip dhcp pool", "", 1).strip()
                config["dhcp_pools"].setdefault(current_pool, {"network": None})
                current_interface = None
                current_acl = None
                current_eigrp = None
                current_ospf = None
                current_rip = None
                current_line = None
                continue

            # EIGRP routing block start
            if line.startswith("router eigrp"):
                asn = line.split()[-1]
                current_eigrp = {
                    "asn": asn,
                    "networks": [],
                    "passive_interfaces": [],
                    "auto_summary": True,
                }
                config["routing"]["eigrp"].append(current_eigrp)
                current_interface = None
                current_pool = None
                current_acl = None
                current_ospf = None
                current_rip = None
                current_line = None
                continue

            # OSPF routing block start
            if line.startswith("router ospf"):
                process_id = line.split()[-1]
                current_ospf = {
                    "process_id": process_id,
                    "networks": [],
                    "passive_interfaces": [],
                }
                config["routing"]["ospf"].append(current_ospf)
                current_interface = None
                current_pool = None
                current_acl = None
                current_eigrp = None
                current_rip = None
                current_line = None
                continue

            # RIP routing block start
            if line.startswith("router rip"):
                current_rip = {
                    "version": None,
                    "networks": [],
                    "passive_interfaces": [],
                    "auto_summary": True,
                }
                config["routing"]["rip"].append(current_rip)
                current_interface = None
                current_pool = None
                current_acl = None
                current_eigrp = None
                current_ospf = None
                current_line = None
                continue

            # Named ACL block start
            if line.startswith("ip access-list"):
                parts = line.split()
                if len(parts) >= 3:
                    acl_type = parts[2]  # standard or extended
                    acl_name = parts[3] if len(parts) > 3 else parts[2]
                    current_acl = acl_name
                    config["access_lists"].setdefault(
                        current_acl, {"type": acl_type, "rules": []}
                    )
                current_interface = None
                current_pool = None
                current_eigrp = None
                current_ospf = None
                current_rip = None
                current_line = None
                continue

            # Context-specific parsing
            if current_interface:
                iface_data = config["interfaces"][current_interface]
                if line.startswith("ip address"):
                    parts = line.split()
                    if len(parts) >= 4:
                        iface_data["ip"] = parts[2]
                        iface_data["mask"] = parts[3]
                elif line == "no ip address":
                    iface_data["ip"] = None
                    iface_data["mask"] = None
                elif line == "shutdown":
                    iface_data["shutdown"] = True
                elif line == "no shutdown":
                    iface_data["shutdown"] = False
                elif line.startswith("description"):
                    iface_data["description"] = " ".join(line.split()[1:])
                elif line.startswith("switchport mode"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["switchport_mode"] = parts[2]
                elif line.startswith("switchport access vlan"):
                    parts = line.split()
                    if len(parts) >= 4:
                        iface_data["access_vlan"] = parts[3]
                elif line.startswith("switchport trunk native vlan"):
                    parts = line.split()
                    if len(parts) >= 5:
                        iface_data["trunk_native_vlan"] = parts[4]
                elif line.startswith("switchport trunk allowed vlan"):
                    parts = line.split()
                    if len(parts) >= 5:
                        iface_data["trunk_allowed_vlans"] = " ".join(parts[4:])
                elif line.startswith("channel-group"):
                    parts = line.split()
                    if len(parts) >= 2:
                        channel_info = {
                            "id": parts[1],
                            "mode": None,
                            "protocol": None,
                        }
                        if "mode" in parts:
                            mode_idx = parts.index("mode")
                            if mode_idx + 1 < len(parts):
                                channel_info["mode"] = parts[mode_idx + 1]

                        # Infer control protocol from negotiated mode when protocol command is absent.
                        mode = (channel_info.get("mode") or "").lower()
                        if mode in {"active", "passive"}:
                            channel_info["protocol"] = "lacp"
                        elif mode in {"desirable", "auto"}:
                            channel_info["protocol"] = "pagp"
                        elif mode == "on":
                            channel_info["protocol"] = "static"

                        iface_data["channel_group"] = channel_info
                        group = config["switching"]["etherchannel"][
                            "groups"
                        ].setdefault(
                            channel_info["id"],
                            {
                                "members": [],
                                "mode": channel_info.get("mode"),
                                "protocol": channel_info.get("protocol"),
                            },
                        )
                        if current_interface not in group["members"]:
                            group["members"].append(current_interface)
                        if channel_info.get("mode"):
                            group["mode"] = channel_info["mode"]
                        if channel_info.get("protocol"):
                            group["protocol"] = channel_info["protocol"]
                elif line.startswith("channel-protocol"):
                    parts = line.split()
                    if len(parts) >= 2:
                        iface_data["channel_protocol"] = parts[1]
                        channel_group = iface_data.get("channel_group")
                        if isinstance(channel_group, dict):
                            channel_group["protocol"] = parts[1]
                            group_id = channel_group.get("id")
                            if group_id:
                                group = config["switching"]["etherchannel"][
                                    "groups"
                                ].setdefault(
                                    group_id,
                                    {"members": []},
                                )
                                group["protocol"] = parts[1]
                elif line.startswith("lacp rate"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["lacp_rate"] = parts[2]
                elif line.startswith("pagp"):
                    iface_data.setdefault("pagp", []).append(line)
                elif line == "switchport port-security":
                    iface_data.setdefault("port_security", {})["enabled"] = True
                elif line.startswith("switchport port-security maximum"):
                    parts = line.split()
                    if len(parts) >= 4:
                        iface_data.setdefault("port_security", {})["maximum"] = parts[3]
                elif line.startswith("switchport port-security violation"):
                    parts = line.split()
                    if len(parts) >= 4:
                        iface_data.setdefault("port_security", {})["violation"] = parts[
                            3
                        ]
                elif line.startswith("switchport port-security mac-address sticky"):
                    parts = line.split()
                    port_security = iface_data.setdefault("port_security", {})
                    port_security["sticky"] = True
                    if len(parts) >= 5:
                        port_security.setdefault("sticky_macs", []).append(parts[4])
                elif line == "ip nat inside":
                    config["nat"]["inside_interfaces"].append(current_interface)
                elif line == "ip nat outside":
                    config["nat"]["outside_interfaces"].append(current_interface)
                elif line.startswith("encapsulation dot1Q"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["encapsulation"] = "dot1Q"
                        iface_data["vlan"] = parts[2]
                        if len(parts) > 3 and parts[3].lower() == "native":
                            iface_data["native_vlan"] = True
                elif line.startswith("encapsulation dot1q"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["encapsulation"] = "dot1Q"
                        iface_data["vlan"] = parts[2]
                        if len(parts) > 3 and parts[3].lower() == "native":
                            iface_data["native_vlan"] = True
                elif line.startswith("spanning-tree"):
                    iface_data.setdefault("spanning_tree", []).append(line)
                elif line.startswith("encapsulation ppp"):
                    iface_data["encapsulation"] = "ppp"
                elif line.startswith("encapsulation hdlc") or line == "encapsulation hdlc":
                    iface_data["encapsulation"] = "hdlc"
                elif line.startswith("ppp authentication"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["ppp_authentication"] = parts[2]
                elif line.startswith("ppp chap hostname"):
                    parts = line.split()
                    if len(parts) >= 4:
                        iface_data["ppp_chap_hostname"] = parts[3]
                elif line.startswith("ppp chap password"):
                    parts = line.split()
                    if len(parts) >= 4:
                        # Store type indicator only, not raw password
                        iface_data["ppp_chap_password_type"] = parts[3]
                elif line.startswith("ppp pap sent-username"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["ppp_pap_username"] = parts[2] if len(parts) > 2 else None
                elif line.startswith("clock rate"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["clock_rate"] = parts[2]
                elif line.startswith("ip access-group"):
                    parts = line.split()
                    if len(parts) >= 3:
                        direction = parts[3] if len(parts) > 3 else "in"
                        iface_data.setdefault("access_groups", []).append(
                            {"acl": parts[2], "direction": direction}
                        )
                # EtherChannel / channel-group
                elif line.startswith("channel-group"):
                    parts = line.split()
                    if len(parts) >= 2:
                        cg = {"group": parts[1]}
                        if "mode" in parts:
                            mode_idx = parts.index("mode")
                            if mode_idx + 1 < len(parts):
                                cg["mode"] = parts[mode_idx + 1]
                        iface_data["channel_group"] = cg
                # Spanning tree interface commands
                elif line == "spanning-tree portfast":
                    iface_data["stp_portfast"] = True
                elif line == "no spanning-tree portfast":
                    iface_data["stp_portfast"] = False
                elif line == "spanning-tree bpduguard enable":
                    iface_data["stp_bpduguard"] = True
                elif line == "no spanning-tree bpduguard":
                    iface_data["stp_bpduguard"] = False
                elif line == "spanning-tree guard root":
                    iface_data["stp_root_guard"] = True
                elif line.startswith("spanning-tree cost"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["stp_cost"] = parts[2]
                elif line.startswith("spanning-tree port-priority"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["stp_port_priority"] = parts[2]
                # OSPF interface commands
                elif line == "ip ospf network point-to-point":
                    iface_data["ospf_network_type"] = "point-to-point"
                # LACP port-priority
                elif line.startswith("lacp port-priority"):
                    parts = line.split()
                    if len(parts) >= 3:
                        iface_data["lacp_port_priority"] = parts[2]
                # Switchport trunk encapsulation
                elif line.startswith("switchport trunk encapsulation"):
                    parts = line.split()
                    if len(parts) >= 4:
                        iface_data["trunk_encapsulation"] = parts[3]
                continue

            if current_pool:
                pool = config["dhcp_pools"][current_pool]
                if line.startswith("network"):
                    pool["network"] = line.replace("network", "", 1).strip()
                elif line.startswith("default-router"):
                    pool["default_router"] = line.replace(
                        "default-router", "", 1
                    ).strip()
                elif line.startswith("dns-server"):
                    pool["dns_server"] = line.replace("dns-server", "", 1).strip()
                continue

            if current_eigrp:
                if line.startswith("network"):
                    current_eigrp["networks"].append(
                        line.replace("network", "", 1).strip()
                    )
                elif line.startswith("passive-interface"):
                    iface = line.replace("passive-interface", "", 1).strip()
                    current_eigrp["passive_interfaces"].append(iface)
                elif line == "no auto-summary":
                    current_eigrp["auto_summary"] = False
                elif line.startswith("redistribute"):
                    current_eigrp.setdefault("redistribute", []).append(
                        line.replace("redistribute", "", 1).strip()
                    )
                continue

            if current_ospf:
                if line.startswith("network"):
                    current_ospf["networks"].append(
                        line.replace("network", "", 1).strip()
                    )
                elif line.startswith("passive-interface"):
                    iface = line.replace("passive-interface", "", 1).strip()
                    current_ospf["passive_interfaces"].append(iface)
                elif line.startswith("redistribute"):
                    current_ospf.setdefault("redistribute", []).append(
                        line.replace("redistribute", "", 1).strip()
                    )
                continue

            if current_rip:
                if line.startswith("version"):
                    parts = line.split()
                    if len(parts) >= 2:
                        current_rip["version"] = parts[1]
                elif line.startswith("network"):
                    current_rip["networks"].append(
                        line.replace("network", "", 1).strip()
                    )
                elif line.startswith("passive-interface"):
                    iface = line.replace("passive-interface", "", 1).strip()
                    current_rip["passive_interfaces"].append(iface)
                elif line == "no auto-summary":
                    current_rip["auto_summary"] = False
                elif line.startswith("redistribute"):
                    current_rip.setdefault("redistribute", []).append(
                        line.replace("redistribute", "", 1).strip()
                    )
                continue

            if current_line:
                if line.startswith("password"):
                    parts = line.split()
                    if len(parts) >= 2:
                        current_line["password_type"] = parts[1] if len(parts) >= 3 else "0"
                        current_line["password_set"] = True
                elif line.startswith("login"):
                    current_line["login"] = line
                elif line.startswith("transport input"):
                    current_line["transport"] = line.replace("transport input", "", 1).strip()
                elif line.startswith("exec-timeout"):
                    current_line["exec_timeout"] = line.replace("exec-timeout", "", 1).strip()
                elif line.startswith("logging synchronous") or line == "logging synchronous":
                    current_line["logging_synchronous"] = True
                continue

            if current_acl:
                # Inside named ACL block - capture permit/deny rules
                if line.startswith(("permit", "deny")):
                    config["access_lists"][current_acl]["rules"].append(line)
                continue

            # Global-level commands (outside any context block)
            if line.startswith("banner motd"):
                try:
                    config["banner_motd"] = line.split("^C")[1]
                except Exception:
                    config["banner_motd"] = "configured"
            elif line.startswith("line vty"):
                current_line = config["vty"]
                current_line["line"] = line
                current_interface = None
                current_pool = None
                current_acl = None
                current_eigrp = None
                current_ospf = None
                current_rip = None
            elif line.startswith("line con"):
                current_line = config["console"]
                current_line["line"] = line
                current_interface = None
                current_pool = None
                current_acl = None
                current_eigrp = None
                current_ospf = None
                current_rip = None
            elif line.startswith("username"):
                parts = line.split()
                if len(parts) >= 2:
                    username = parts[1]
                    user_entry = {"username": username}

                    # Extract privilege level if present
                    if "privilege" in parts:
                        priv_idx = parts.index("privilege")
                        if priv_idx + 1 < len(parts):
                            user_entry["privilege"] = parts[priv_idx + 1]

                    # Extract password/secret type and value
                    if "secret" in parts:
                        secret_idx = parts.index("secret")
                        if secret_idx + 2 < len(parts):
                            user_entry["auth_type"] = "secret"
                            user_entry["auth_level"] = parts[secret_idx + 1]
                    elif "password" in parts:
                        pass_idx = parts.index("password")
                        if pass_idx + 2 < len(parts):
                            user_entry["auth_type"] = "password"
                            user_entry["auth_level"] = parts[pass_idx + 1]

                    config["users"].append(user_entry)
            elif line.startswith("access-list"):
                # Numbered ACL (legacy format)
                acl_id = line.split()[1]
                config["access_lists"].setdefault(
                    acl_id, {"type": "numbered", "rules": []}
                )
                config["access_lists"][acl_id]["rules"].append(line)
            # Spanning tree global commands
            elif line.startswith("spanning-tree vlan") and "priority" in line:
                parts = line.split()
                # spanning-tree vlan <id> priority <val>
                try:
                    vlan_idx = parts.index("vlan") + 1
                    pri_idx = parts.index("priority") + 1
                    vlan_id = parts[vlan_idx]
                    priority_val = parts[pri_idx]
                    stp = config["switching"]["spanning_tree"]
                    stp.setdefault("vlan_priorities", {})[vlan_id] = priority_val
                except (ValueError, IndexError):
                    pass
            elif line.startswith("spanning-tree vlan") and "root" in line:
                parts = line.split()
                # spanning-tree vlan <id> root primary/secondary
                try:
                    vlan_idx = parts.index("vlan") + 1
                    root_idx = parts.index("root") + 1
                    vlan_id = parts[vlan_idx]
                    root_type = parts[root_idx]
                    stp = config["switching"]["spanning_tree"]
                    stp.setdefault("vlan_root", {})[vlan_id] = root_type
                except (ValueError, IndexError):
                    pass
            # EtherChannel / port-channel load-balance
            elif line.startswith("port-channel load-balance"):
                parts = line.split()
                if len(parts) >= 3:
                    config["etherchannel"]["load_balance"] = parts[2]
            # LACP system priority
            elif line.startswith("lacp system-priority"):
                parts = line.split()
                if len(parts) >= 3:
                    config["lacp_system_priority"] = parts[2]
            # Enable secret/password (store type only for comparison)
            elif line.startswith("enable secret"):
                parts = line.split()
                if len(parts) >= 3:
                    config["enable_secret_type"] = parts[2]
            elif line.startswith("enable password"):
                parts = line.split()
                if len(parts) >= 3:
                    config["enable_password_type"] = parts[2]

    # Clean up NAT interface lists
    config["nat"]["inside_interfaces"] = sorted(set(config["nat"]["inside_interfaces"]))
    config["nat"]["outside_interfaces"] = sorted(
        set(config["nat"]["outside_interfaces"])
    )
    return config


def parse_show_ip_interface_brief(file_path):
    """Parse interface IP/status rows from 'show ip interface brief'."""
    result = {"interfaces": {}}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        if not stripped or _is_device_prompt(stripped):
            continue
        if stripped.lower().startswith("show ip interface brief"):
            continue
        if (
            stripped.lower().startswith("interface")
            and "ip-address" in stripped.lower()
        ):
            continue

        parts = stripped.split()
        if len(parts) < 6:
            continue

        iface = parts[0]
        ip_addr = parts[1]
        protocol = parts[-1]
        status = " ".join(parts[4:-1])
        result["interfaces"][iface] = {
            "ip": ip_addr,
            "status": status,
            "protocol": protocol,
        }

    return result


def parse_show_ip_route(file_path):
    """Parse 'show ip route' output for deterministic comparison values."""
    result = {
        "gateway_of_last_resort": None,
        "gateway": {"next_hop": None, "network": None},
        "routes": [],
    }
    lines = _clean_log_lines(file_path)

    route_line_re = re.compile(r"^(?P<code>[A-Za-z\*]+)\s+(?P<destination>\S+)")

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        if not stripped or _is_device_prompt(stripped):
            continue

        if lower.startswith("show ip route"):
            continue

        # Route code legend can span multiple lines; skip all legend lines.
        if lower.startswith("codes:") or lower.startswith("gateway of last resort"):
            if lower.startswith("gateway of last resort"):
                result["gateway_of_last_resort"] = stripped
                match = re.search(
                    r"gateway of last resort is\s+(\S+)\s+to network\s+(\S+)",
                    lower,
                )
                if match:
                    result["gateway"]["next_hop"] = match.group(1)
                    result["gateway"]["network"] = match.group(2)
            continue

        # Subnet summary lines are informational (not actual route entries).
        if " is subnetted" in lower or " is variably subnetted" in lower:
            continue

        match = route_line_re.match(stripped)
        if not match:
            continue

        code_raw = match.group("code")
        destination = match.group("destination")
        # Keep code tokens exactly as route semantics (e.g., S*, EX, O, C, D).
        code = code_raw.upper()

        route = {
            "code": code,
            "destination": destination,
        }

        if "is directly connected" in stripped:
            route["type"] = "directly_connected"
            route["interface"] = (
                stripped.split("is directly connected", 1)[1].strip().strip(",")
            )
        elif "via" in stripped:
            route["type"] = "learned"
            via_part = stripped.split("via", 1)[1].strip()
            via_tokens = [token.strip() for token in via_part.split(",")]
            if via_tokens:
                route["next_hop"] = via_tokens[0]
            if via_tokens:
                route["interface"] = via_tokens[-1]
        else:
            route["type"] = "other"

        result["routes"].append(route)

    result["routes"] = sorted(
        result["routes"],
        key=lambda entry: (entry.get("code", ""), entry.get("destination", "")),
    )
    return result


def parse_show_access_lists(file_path):
    """Parse ACL names and rule lines from 'show access-lists'."""
    result = {"acls": {}}
    lines = _clean_log_lines(file_path)

    current_acl = None
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped or _is_device_prompt(stripped):
            continue
        if lower.startswith("show access"):
            continue

        if lower.startswith("standard ip access list") or lower.startswith(
            "extended ip access list"
        ):
            current_acl = stripped.split()[-1]
            result["acls"].setdefault(current_acl, [])
            continue

        if stripped.startswith("access-list"):
            acl_id = stripped.split()[1]
            result["acls"].setdefault(acl_id, []).append(stripped)
            continue

        if current_acl:
            # Remove match count (e.g., "(33 matches)") from ACL rules
            rule = stripped
            if "(" in rule and "match" in rule.lower():
                rule = rule.split("(")[0].strip()
            if rule:  # Only append if there's content after removing match count
                result["acls"][current_acl].append(rule)

    for acl_name in result["acls"]:
        result["acls"][acl_name] = sorted(result["acls"][acl_name])

    return result


def parse_show_ip_nat_statistics(file_path):
    """Parse stable NAT interface, mapping, and pool details."""
    result = {
        "outside_interfaces": [],
        "inside_interfaces": [],
        "mappings": [],
        "pools": {},
    }
    lines = _clean_log_lines(file_path)
    section = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip nat")
        ):
            continue

        if lower.startswith("outside interfaces"):
            section = "outside_interfaces"
            continue

        if lower.startswith("inside interfaces"):
            section = "inside_interfaces"
            continue

        if lower.startswith("dynamic mappings"):
            section = "dynamic_mappings"
            continue

        # Omit volatile runtime counters from verification.
        if (
            lower.startswith("total active translations")
            or lower.startswith("hits")
            or lower.startswith("cef translated packets")
            or lower.startswith("expired translations")
            or lower.startswith("appl doors")
            or lower.startswith("normal doors")
            or lower.startswith("queued packets")
        ):
            section = None
            continue

        if section == "outside_interfaces":
            result["outside_interfaces"].append(stripped)
            continue

        if section == "inside_interfaces":
            result["inside_interfaces"].append(stripped)
            continue

        if section == "dynamic_mappings":
            # Example:
            # [Id: 1] access-list NATACLVLAN11 pool public_pool refCount 0
            if "access-list" in lower and "pool" in lower:
                acl_match = re.search(r"access-list\s+(\S+)", stripped, re.IGNORECASE)
                pool_match = re.search(r"pool\s+(\S+)", stripped, re.IGNORECASE)
                if acl_match:
                    mapping = {"acl": acl_match.group(1)}
                    if pool_match:
                        mapping["pool"] = pool_match.group(1)
                    result["mappings"].append(mapping)
                continue

            # Example:
            # pool public_pool: netmask 255.255.255.240
            if lower.startswith("pool ") and ":" in stripped:
                pool_name = stripped.split(":", 1)[0].split()[1]
                details = stripped.split(":", 1)[1].strip()
                result["pools"].setdefault(pool_name, {})

                netmask_match = re.search(r"netmask\s+(\S+)", details, re.IGNORECASE)
                if netmask_match:
                    result["pools"][pool_name]["netmask"] = netmask_match.group(1)

                start_match = re.search(r"start\s+(\S+)", details, re.IGNORECASE)
                end_match = re.search(r"end\s+(\S+)", details, re.IGNORECASE)
                if start_match:
                    result["pools"][pool_name]["start_ip"] = start_match.group(1)
                if end_match:
                    result["pools"][pool_name]["end_ip"] = end_match.group(1)

                continue

    result["outside_interfaces"] = sorted(set(result["outside_interfaces"]))
    result["inside_interfaces"] = sorted(set(result["inside_interfaces"]))
    result["mappings"] = sorted(
        result["mappings"], key=lambda item: (item.get("acl", ""), item.get("pool", ""))
    )

    return result


def parse_show_ip_nat_translations(file_path):
    """Parse NAT translation output as evidence that NAT was exercised."""
    result = {"tested": False}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip nat translations")
            or lower.startswith("pro")
            or lower.startswith("total:")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 5:
            result["tested"] = True
            break

    return result


def parse_show_ip_dhcp_binding(file_path):
    """Parse DHCP binding output and record assigned client IP addresses."""
    result = {"has_assignments": False, "assigned_ips": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip dhcp binding")
            or lower.startswith("bindings from all pools not associated")
            or lower.startswith("ip address")
            or lower.startswith("<none>")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 4 and "." in parts[0]:
            result["assigned_ips"].append(parts[0])

    result["assigned_ips"] = sorted(set(result["assigned_ips"]))
    result["has_assignments"] = bool(result["assigned_ips"])
    return result


def parse_show_ip_dhcp_pool(file_path):
    """Parse DHCP pool output and record pool names that appeared."""
    result = {"tested": False, "pool_names": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip dhcp pool")
        ):
            continue

        if lower.startswith("pool") and ":" in stripped:
            # Extract pool name from "Pool PoolName :" format (part before colon)
            pool_name = (
                stripped.split(":", 1)[0]
                .replace("Pool", "")
                .replace("pool", "")
                .strip()
            )
            if pool_name:
                result["pool_names"].append(pool_name)

    result["pool_names"] = sorted(set(result["pool_names"]))
    result["tested"] = bool(result["pool_names"])

    return result


def parse_show_ip_eigrp(file_path):
    """Parse general EIGRP output into stable verification entries."""
    result = {"entries": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped or _is_device_prompt(stripped):
            continue
        if lower.startswith("show ip eigrp"):
            continue
        if lower.startswith("codes:"):
            continue

        result["entries"].append(stripped)

    result["entries"] = sorted(set(result["entries"]))
    return result


def parse_show_ip_eigrp_neighbor(file_path):
    """Parse EIGRP neighbor address/interface pairs."""
    result = {"neighbors": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip eigrp neighbor")
            or lower.startswith("h ")
            or lower.startswith("address")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 3:
            address = parts[1].strip()
            interface = parts[2].strip()
            if address and interface:
                result["neighbors"].append(
                    {
                        "address": address,
                        "interface": interface,
                    }
                )

    result["neighbors"] = sorted(
        result["neighbors"],
        key=lambda entry: (entry.get("address", ""), entry.get("interface", "")),
    )
    return result


def parse_show_ip_eigrp_topology(file_path):
    """Parse EIGRP topology routes, successor counts, and next-hop paths."""
    result = {"routes": []}
    lines = _clean_log_lines(file_path)
    current_route = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip eigrp topology")
            or lower.startswith("ip-eigrp topology")
            or lower.startswith("codes:")
        ):
            continue

        route_match = re.match(
            r"^(?P<state>[PA])\s+(?P<destination>\S+),\s+(?P<successors>\d+)\s+successors?",
            stripped,
            flags=re.IGNORECASE,
        )
        if route_match:
            current_route = {
                "destination": route_match.group("destination"),
                "state": "Passive" if route_match.group("state").upper() == "P" else "Active",
                "successors": int(route_match.group("successors")),
                "via": [],
            }
            result["routes"].append(current_route)
            continue

        if current_route and lower.startswith("via "):
            via_match = re.match(
                r"^via\s+(?P<address>[^,\s]+).*?,\s*(?P<interface>\S+)\s*$",
                stripped,
                flags=re.IGNORECASE,
            )
            if via_match:
                current_route["via"].append(
                    {
                        "address": via_match.group("address"),
                        "interface": via_match.group("interface"),
                    }
                )

    return result


def parse_show_ip_eigrp_interfaces(file_path):
    """Parse EIGRP-enabled interfaces and peer counts."""
    result = {"interfaces": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip eigrp interfaces")
            or lower.startswith("ip-eigrp interfaces")
            or lower.startswith("interface")
            or lower.startswith("xmit")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 2 and re.match(r"^[A-Za-z]+\S+", parts[0]):
            peers = parts[1] if parts[1].isdigit() else None
            result["interfaces"].append(
                {
                    "name": parts[0],
                    "peers": int(peers) if peers is not None else None,
                }
            )

    return result


def parse_show_ip_ospf(file_path):
    """Parse general OSPF process and area information."""
    result = {"process_info": [], "areas": []}
    lines = _clean_log_lines(file_path)

    current_section = None
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped or _is_device_prompt(stripped):
            continue
        if lower.startswith("show ip ospf"):
            continue

        if lower.startswith("routing process") or lower.startswith("process id"):
            current_section = "process"
            result["process_info"].append(stripped)
        elif lower.startswith("area "):
            current_section = "area"
            result["areas"].append(stripped)
        elif current_section:
            if current_section == "process":
                result["process_info"].append(stripped)
            elif current_section == "area":
                result["areas"].append(stripped)

    return result


def parse_show_ip_ospf_neighbor(file_path):
    """Parse OSPF neighbor state, address, and interface data."""
    result = {"neighbors": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip ospf neighbor")
            or lower.startswith("neighbor id")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 6:
            address = parts[-2].strip()
            interface = parts[-1].strip()
            if address and interface:
                result["neighbors"].append(
                    {
                        "neighbor_id": parts[0].strip(),
                        "priority": parts[1].strip(),
                        "state": parts[2].strip(),
                        "address": address,
                        "interface": interface,
                    }
                )

    result["neighbors"] = sorted(
        result["neighbors"],
        key=lambda entry: (
            entry.get("address", ""),
            entry.get("interface", ""),
            entry.get("state", ""),
        ),
    )
    return result


def parse_show_ip_ospf_database(file_path):
    """Parse OSPF database sections into LSA-type counts."""
    result = {
        "lsa_types": {
            "router": 0,
            "network": 0,
            "summary_net": 0,
            "summary_asbr": 0,
            "as_external": 0,
        }
    }
    lines = _clean_log_lines(file_path)
    section = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped or _is_device_prompt(stripped) or lower.startswith("show ip ospf database"):
            continue

        if "router link states" in lower:
            section = "router"
            continue
        if "net link states" in lower:
            section = "network"
            continue
        if "summary net link states" in lower:
            section = "summary_net"
            continue
        if "summary asbr link states" in lower:
            section = "summary_asbr"
            continue
        if "type-5 as external link states" in lower or "as external link states" in lower:
            section = "as_external"
            continue

        if section and re.match(r"^\d+", stripped):
            result["lsa_types"][section] += 1

    return result


def parse_show_ip_ospf_interface(file_path):
    """Parse OSPF interface area, network type, and state values."""
    result = {"interfaces": []}
    lines = _clean_log_lines(file_path)
    current = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped or _is_device_prompt(stripped) or lower.startswith("show ip ospf interface"):
            continue

        iface_match = re.match(
            r"^(?P<name>\S+)\s+is\s+\w+,\s+line protocol is\s+\w+",
            stripped,
            flags=re.IGNORECASE,
        )
        if iface_match:
            current = {
                "name": iface_match.group("name"),
                "area": "",
                "network_type": "",
                "state": "",
            }
            result["interfaces"].append(current)
            continue

        if not current:
            continue

        area_match = re.search(r"\bArea\s+(\S+)", stripped, flags=re.IGNORECASE)
        if area_match:
            current["area"] = area_match.group(1).rstrip(",")

        network_match = re.search(
            r"Network Type\s+([A-Z_]+)",
            stripped,
            flags=re.IGNORECASE,
        )
        if network_match:
            current["network_type"] = network_match.group(1).upper()

        state_match = re.search(r"\bState\s+([A-Z_\/-]+)", stripped, flags=re.IGNORECASE)
        if state_match:
            current["state"] = state_match.group(1).upper().rstrip(",")

    return result


def parse_show_ip_rip_database(file_path):
    """Parse RIP database routes and metric evidence."""
    result = {"routes": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show ip rip database")
            or lower.startswith("rip")
        ):
            continue

        dest_match = re.search(r"(\d+\.\d+\.\d+\.\d+/\d+)", stripped)
        if not dest_match:
            continue
        metric_match = re.search(r"metric\s+(\d+)", lower)
        result["routes"].append(
            {
                "destination": dest_match.group(1),
                "metric": int(metric_match.group(1)) if metric_match else 0,
                "possibly_down": "possibly down" in lower,
            }
        )

    return result


def parse_show_ip_route_static(file_path):
    """Parse static routes from route output into destination/next-hop rows."""
    result = {"routes": []}
    base = parse_show_ip_route(file_path)
    for route in base.get("routes", []):
        if not isinstance(route, dict):
            continue
        code = str(route.get("code", "")).upper()
        if not code.startswith("S"):
            continue
        destination = str(route.get("destination", "")).strip()
        mask = ""
        if "/" in destination:
            try:
                prefix = int(destination.rsplit("/", 1)[1])
                mask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
            except Exception:
                mask = ""
        result["routes"].append(
            {
                "destination": destination,
                "mask": mask,
                "next_hop": str(route.get("next_hop", "")).strip(),
                "interface": str(route.get("interface", "")).strip(),
            }
        )
    return result


def parse_show_interfaces_trunk(file_path):
    """Parse trunk mode, encapsulation, status, and native VLAN per port."""
    result = {"trunks": {}}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show int")
            or lower.startswith("port ")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 4:
            port = parts[0]
            result["trunks"][port] = {
                "mode": parts[1],
                "encapsulation": parts[2],
                "status": parts[3],
                "native_vlan": parts[4] if len(parts) > 4 else "",
            }

    return result


def parse_show_vlan_brief(file_path):
    """Parse VLAN IDs, names, status, and associated ports."""
    result = {"vlans": {}}
    lines = _clean_log_lines(file_path)
    current_vlan_id = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        # Skip headers and empty lines
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show vlan")
            or lower.startswith("vlan ")
            or lower.startswith("----")
        ):
            continue

        parts = stripped.split()

        # Check if line starts with a VLAN ID (digit)
        if len(parts) >= 2 and parts[0].isdigit():
            vlan_id = parts[0]
            vlan_name = parts[1]
            status = parts[2] if len(parts) > 2 else ""
            ports = " ".join(parts[3:]) if len(parts) > 3 else ""
            result["vlans"][vlan_id] = {
                "name": vlan_name,
                "status": status,
                "ports": ports,
            }
            current_vlan_id = vlan_id

        # Handle port continuation lines (indented lines without VLAN ID)
        elif current_vlan_id and len(parts) > 0:
            # This is a continuation line - append ports to current VLAN
            ports_to_add = " ".join(parts)
            if result["vlans"][current_vlan_id]["ports"]:
                result["vlans"][current_vlan_id]["ports"] += ", " + ports_to_add
            else:
                result["vlans"][current_vlan_id]["ports"] = ports_to_add

    return result


def parse_show_port_security(file_path):
    """Parse port-security counters and configured violation actions."""
    result = {"interfaces": {}}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show port")
            or lower.startswith("secure-port")
            or lower.startswith("----")
            or lower.startswith("total")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 5:
            interface = parts[0]
            result["interfaces"][interface] = {
                "max_secure_addr": parts[1],
                "current_count": parts[2],
                "security_violation_count": parts[3],
                "security_action": parts[4],
            }

    return result


def parse_show_spanning_tree(file_path):
    """Parse spanning-tree instances and interface role/status evidence."""
    result = {"instances": {}, "interfaces": {}}
    lines = _clean_log_lines(file_path)

    current_vlan = None
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped or _is_device_prompt(stripped):
            continue
        if lower.startswith("show spanning"):
            continue

        if lower.startswith("vlan"):
            parts = stripped.split()
            if len(parts) >= 2:
                current_vlan = parts[1]
                result["instances"].setdefault(current_vlan, [])
        elif current_vlan:
            result["instances"][current_vlan].append(stripped)

        # Parse interface lines
        if (
            stripped.startswith("Fa")
            or stripped.startswith("Gi")
            or stripped.startswith("Et")
        ):
            parts = stripped.split()
            if len(parts) >= 4:
                interface = parts[0]
                result["interfaces"].setdefault(interface, {})
                result["interfaces"][interface] = {
                    "role": parts[1] if len(parts) > 1 else "",
                    "status": parts[2] if len(parts) > 2 else "",
                    "cost": parts[3] if len(parts) > 3 else "",
                    "prio": parts[4] if len(parts) > 4 else "",
                    "type": parts[5] if len(parts) > 5 else "",
                }

    return result


def parse_show_etherchannel_summary(file_path):
    """Parse 'show etherchannel summary' output.

    Example output:
    Group  Port-channel  Protocol    Ports
    ------+-------------+-----------+-------
    1      Po1(SU)       LACP        Fa0/1(P)    Fa0/2(P)
    2      Po2(SD)       PAgP        Fa0/3(D)    Fa0/4(D)
    """
    result = {"groups": {}}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show etherchannel")
            or lower.startswith("group")
            or lower.startswith("----")
            or lower.startswith("flags")
            or lower.startswith("number of channel-groups")
        ):
            continue

        # Typical rows look like: "1      Po1(SU)         LACP      Fa0/1(P) Fa0/2(P)"
        parts = stripped.split()
        if len(parts) >= 3 and parts[0].isdigit():
            group = {
                "group": parts[0],
                "port_channel": parts[1],
                "protocol": parts[2],
                "members": parts[3:] if len(parts) > 3 else [],
            }
            result["groups"].append(group)

    return result


def parse_show_pagp_neighbor(file_path):
    """Parse PAgP neighbor rows used for EtherChannel verification."""
    result = {"neighbors": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show pagp")
            or lower.startswith("flags")
            or lower.startswith("port")
            or lower.startswith("----")
        ):
            continue

        # Preserve parsed row as tokens to avoid fragile fixed-column assumptions.
        parts = stripped.split()
        if len(parts) >= 2:
            result["neighbors"].append(
                {
                    "interface": parts[0],
                    "details": parts[1:],
                }
            )

    return result


def parse_show_lacp_neighbor(file_path):
    """Parse LACP neighbor rows used for EtherChannel verification."""
    result = {"neighbors": []}
    lines = _clean_log_lines(file_path)

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if (
            not stripped
            or _is_device_prompt(stripped)
            or lower.startswith("show lacp")
            or lower.startswith("flags")
            or lower.startswith("port")
            or lower.startswith("----")
        ):
            continue

        parts = stripped.split()
        if len(parts) >= 2:
            result["neighbors"].append(
                {
                    "interface": parts[0],
                    "details": parts[1:],
                }
            )

    return result


def parse_log_by_type(file_path, command_type=None):
    """Dispatch one log file to the parser for its command type."""
    command_error = detect_command_error(file_path)
    if command_error:
        return None, None

    command = command_type or detect_command_type(file_path)
    if command == "show_running_config":
        return command, parse_showrun(file_path)
    if command == "show_ip_interface_brief":
        return command, parse_show_ip_interface_brief(file_path)
    if command == "show_ip_route":
        return command, parse_show_ip_route(file_path)
    if command == "show_access_lists":
        return command, parse_show_access_lists(file_path)
    if command == "show_ip_nat_statistics":
        return command, parse_show_ip_nat_statistics(file_path)
    if command == "show_ip_nat_translations":
        return command, parse_show_ip_nat_translations(file_path)
    if command == "show_ip_dhcp_binding":
        return command, parse_show_ip_dhcp_binding(file_path)
    if command == "show_ip_dhcp_pool":
        return command, parse_show_ip_dhcp_pool(file_path)
    if command == "show_ip_eigrp":
        return command, parse_show_ip_eigrp(file_path)
    if command == "show_ip_eigrp_neighbor":
        return command, parse_show_ip_eigrp_neighbor(file_path)
    if command == "show_ip_eigrp_topology":
        return command, parse_show_ip_eigrp_topology(file_path)
    if command == "show_ip_eigrp_interfaces":
        return command, parse_show_ip_eigrp_interfaces(file_path)
    if command == "show_ip_ospf":
        return command, parse_show_ip_ospf(file_path)
    if command == "show_ip_ospf_neighbor":
        return command, parse_show_ip_ospf_neighbor(file_path)
    if command == "show_ip_ospf_database":
        return command, parse_show_ip_ospf_database(file_path)
    if command == "show_ip_ospf_interface":
        return command, parse_show_ip_ospf_interface(file_path)
    if command == "show_ip_rip_database":
        return command, parse_show_ip_rip_database(file_path)
    if command == "show_ip_route_static":
        return command, parse_show_ip_route_static(file_path)
    if command == "show_interfaces_trunk":
        return command, parse_show_interfaces_trunk(file_path)
    if command == "show_vlan_brief":
        return command, parse_show_vlan_brief(file_path)
    if command == "show_port_security":
        return command, parse_show_port_security(file_path)
    if command == "show_spanning_tree":
        return command, parse_show_spanning_tree(file_path)
    if command == "show_etherchannel_summary":
        return command, parse_show_etherchannel_summary(file_path)
    if command == "show_pagp_neighbor":
        return command, parse_show_pagp_neighbor(file_path)
    if command == "show_lacp_neighbor":
        return command, parse_show_lacp_neighbor(file_path)
    return None, None


def parse_device_logs_with_report(file_paths):
    """Parse logs and return normalized parsed output plus skipped-file diagnostics."""
    parsed = {
        "show_running_config": None,
        "verification": {},
    }
    skipped_logs = []

    for file_path in file_paths:
        command_error = detect_command_error(file_path)
        if command_error:
            skipped_logs.append(
                {
                    "file": file_path,
                    "reason": command_error,
                    "detected_command": detect_command_type(file_path),
                }
            )
            continue

        command, parsed_output = parse_log_by_type(file_path)
        if not command or parsed_output is None:
            continue

        if command == "show_running_config":
            parsed["show_running_config"] = parsed_output
        else:
            parsed["verification"][command] = parsed_output

    return normalize_parsed_config(parsed), skipped_logs


def parse_device_logs(file_paths):
    """Parse all known command outputs for one device. Show run is primary source."""
    parsed, _skipped_logs = parse_device_logs_with_report(file_paths)
    return parsed
