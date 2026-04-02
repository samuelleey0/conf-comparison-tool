import re
import json
import ipaddress
from collections import Counter


NAT_STATS_VOLATILE_KEYS = {
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


def _normalize_interface_list_value(value):
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return sorted(set(cleaned))

    if value is None:
        return []

    if isinstance(value, str):
        # Legacy format can be empty string, comma-separated, or newline-separated.
        chunks = re.split(r"[\n,]+", value)
        cleaned = [chunk.strip() for chunk in chunks if chunk.strip()]
        return sorted(set(cleaned))

    return [str(value).strip()]


def _is_nat_stats_path(full_key: str) -> bool:
    return (
        full_key.endswith("verification.show_ip_nat_statistics")
        or ".verification.show_ip_nat_statistics." in full_key
    )


def _normalize_role_name(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip().lower())
    return sanitized.strip("_") or "unknown"


def _extract_vlan_tokens_from_scheme(scheme: dict) -> dict:
    """Build a mapping of vlan_id -> role token from a scheme definition."""
    if not scheme:
        return {}

    variables = scheme.get("variables", {}) or {}
    vlan_token_map = {}

    for key, value in variables.items():
        key_text = str(key).lower()
        if "vlan" not in key_text:
            continue

        # Simple style: vlan_staff: 10
        if isinstance(value, (str, int)):
            vlan_id = str(value).strip()
            if vlan_id:
                role = _normalize_role_name(key)
                vlan_token_map[vlan_id] = f"__VLAN_{role}__"
            continue

        # List style: vlans: [{id: "10", name: "STAFF", role: "staff"}, ...]
        if isinstance(value, list):
            for idx, item in enumerate(value, start=1):
                if not isinstance(item, dict):
                    continue
                vlan_id = str(
                    item.get("id", item.get("vlan_id", item.get("vlan", "")))
                ).strip()
                if not vlan_id:
                    continue
                role_seed = item.get("role") or item.get("name") or f"{key}_{idx}"
                role = _normalize_role_name(role_seed)
                vlan_token_map[vlan_id] = f"__VLAN_{role}__"

    return vlan_token_map


def _replace_vlan_ids_in_text(text: str, vlan_token_map: dict) -> str:
    if not text:
        return text

    # Replace only token-like numeric chunks (safe for vlan-only fields).
    def replacer(match):
        token = match.group(0)
        return vlan_token_map.get(token, token)

    return re.sub(r"\d+", replacer, text)


def _normalize_value(
    value, vlan_token_map: dict, key_hint: str = "", parent_key: str = ""
):
    if isinstance(value, dict):
        normalized = {}
        for key, child in value.items():
            new_key = key
            if str(parent_key).lower().endswith("vlans"):
                new_key = vlan_token_map.get(str(key), key)
            normalized[new_key] = _normalize_value(
                child,
                vlan_token_map,
                key_hint=str(key),
                parent_key=str(new_key),
            )
        return normalized

    if isinstance(value, list):
        return [
            _normalize_value(
                item, vlan_token_map, key_hint=key_hint, parent_key=parent_key
            )
            for item in value
        ]

    if isinstance(value, int):
        as_text = str(value)
        if "vlan" in key_hint.lower() and as_text in vlan_token_map:
            return vlan_token_map[as_text]
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value

        if stripped in vlan_token_map:
            return vlan_token_map[stripped]

        # Only do partial replacements for vlan-related fields.
        if "vlan" in key_hint.lower() or "vlan" in parent_key.lower():
            return _replace_vlan_ids_in_text(value, vlan_token_map)

        return value

    return value


def normalize_config_with_scheme(config: dict, scheme: dict) -> dict:
    """
    Normalize VLAN identifiers in a parsed config using a scheme.

    Example:
    - Scheme A: vlan_staff=10
    - Scheme B: vlan_staff=15
    After normalization both become __VLAN_vlan_staff__.
    """
    vlan_token_map = _extract_vlan_tokens_from_scheme(scheme)
    if not vlan_token_map:
        return config
    return _normalize_value(config, vlan_token_map)


# ─────────────────────────────────────────────────────────────────
# ACCMS Intelligent Pre-Comparison Analysis
# ─────────────────────────────────────────────────────────────────

def _detect_acl_pointless_rules(rules):
    """Detect ACL rules that are shadowed by earlier rules or by 'any any'.
    Returns list of (rule_index, outcome_code) tuples.
    """
    pointless = []
    seen_any_any = False

    for idx, rule in enumerate(rules):
        rule_lower = str(rule).lower().strip()

        if seen_any_any:
            # Everything after 'any any' is pointless
            pointless.append((idx, "MISMATCH_ACL_POINTLESS_DEFAULT"))
            continue

        # Check if this rule is a catch-all 'permit/deny any' or 'permit/deny ip any any'
        if re.search(r"(permit|deny)\s+(ip\s+)?any\s*(any)?$", rule_lower):
            seen_any_any = True

    return pointless


def _check_ospf_areas(ospf_configs):
    """Analyze OSPF area configuration for common errors.
    Returns list of outcome dicts.
    """
    results = []
    if not ospf_configs:
        return results

    for ospf in ospf_configs:
        networks = ospf.get("networks", [])
        areas_used = set()
        for net_str in networks:
            # Parse "x.x.x.x y.y.y.y area N" format
            area_match = re.search(r"area\s+(\S+)", str(net_str))
            if area_match:
                areas_used.add(area_match.group(1))

        if not areas_used:
            continue

        is_multi_area = len(areas_used) > 1

        # Single-area OSPF should use area 0
        if not is_multi_area and "0" not in areas_used:
            results.append({
                "feature": "show_running_config.routing.ospf.area_validation",
                "expected": "area 0 for single-area OSPF",
                "actual": f"area {sorted(areas_used)[0]}",
                "status": "mismatch",
                "outcome_code": "MISMATCH_OSPF_AREA_ID_SINGLE",
            })

        # Multi-area OSPF must have area 0
        if is_multi_area and "0" not in areas_used:
            results.append({
                "feature": "show_running_config.routing.ospf.area_validation",
                "expected": "area 0 must exist in multi-area OSPF",
                "actual": f"areas: {sorted(areas_used)}",
                "status": "mismatch",
                "outcome_code": "MISMATCH_OSPF_AREA_0_MISSING",
            })

    return results


def _check_nat_pool_ranges(template_pools, student_pools):
    """Compare NAT pool IP ranges for coverage issues.
    Returns list of outcome dicts.
    """
    results = []
    if not isinstance(template_pools, list) or not isinstance(student_pools, list):
        return results

    t_pool_map = {p.get("name"): p for p in template_pools if isinstance(p, dict)}
    s_pool_map = {p.get("name"): p for p in student_pools if isinstance(p, dict)}

    for name, t_pool in t_pool_map.items():
        s_pool = s_pool_map.get(name)
        if not s_pool:
            continue

        try:
            t_start = ipaddress.ip_address(t_pool.get("start_ip", ""))
            t_end = ipaddress.ip_address(t_pool.get("end_ip", ""))
            s_start = ipaddress.ip_address(s_pool.get("start_ip", ""))
            s_end = ipaddress.ip_address(s_pool.get("end_ip", ""))
        except (ValueError, TypeError):
            continue

        # Student range extends outside template
        if s_start < t_start or s_end > t_end:
            results.append({
                "feature": f"show_running_config.nat.pools.{name}.range",
                "expected": f"{t_start}-{t_end}",
                "actual": f"{s_start}-{s_end}",
                "status": "mismatch",
                "outcome_code": "MISMATCH_NAT_POOL_RANGE_OUTSIDE",
            })

        # Student range doesn't cover entire template
        if s_start > t_start or s_end < t_end:
            results.append({
                "feature": f"show_running_config.nat.pools.{name}.range",
                "expected": f"{t_start}-{t_end}",
                "actual": f"{s_start}-{s_end}",
                "status": "mismatch",
                "outcome_code": "MISMATCH_NAT_POOL_RANGE_INSIDE",
            })

    return results


def _check_configured_but_shutdown(interfaces):
    """Detect interfaces that are shutdown but have meaningful config.
    Returns list of outcome dicts.
    """
    results = []
    config_keys = {"ip", "mask", "encapsulation", "switchport_mode", "access_vlan",
                   "trunk_native_vlan", "trunk_allowed_vlans", "ppp_authentication",
                   "channel_group", "ospf_network_type"}

    for iface_name, iface_cfg in interfaces.items():
        if not isinstance(iface_cfg, dict):
            continue
        if iface_cfg.get("shutdown") is not True:
            continue
        has_config = any(
            iface_cfg.get(k) is not None for k in config_keys
            if k in iface_cfg
        )
        # Check if the IP is set (not None)
        if has_config and iface_cfg.get("ip") is not None:
            results.append({
                "feature": f"show_running_config.interfaces.{iface_name}.configured_if_shutdown",
                "expected": "shutdown with no configuration OR no shutdown with configuration",
                "actual": f"shutdown=True but has IP/config",
                "status": "mismatch",
                "outcome_code": "MISMATCH_CONFIGURED_IF_SHUTDOWN",
            })

    return results


def _check_acl_not_applied(access_lists, interfaces):
    """Detect ACLs that are created but not applied to any interface.
    Returns list of outcome dicts.
    """
    results = []
    if not access_lists or not interfaces:
        return results

    # Collect all ACLs that are applied to interfaces
    applied_acls = set()
    for iface_cfg in interfaces.values():
        if not isinstance(iface_cfg, dict):
            continue
        access_groups = iface_cfg.get("access_groups", [])
        if isinstance(access_groups, list):
            for ag in access_groups:
                if isinstance(ag, dict):
                    applied_acls.add(str(ag.get("acl", "")))

    # Also check NAT ACL references
    # (handled separately, not flagged here)

    for acl_name in access_lists:
        if acl_name not in applied_acls:
            results.append({
                "feature": f"show_running_config.access_lists.{acl_name}.applied",
                "expected": "ACL applied to at least one interface",
                "actual": "ACL not applied to any interface",
                "status": "missing",
                "outcome_code": "MISSING_ACL_APPLIED",
            })

    return results


def _check_routing_instances(routing):
    """Detect multiple instances of the same routing protocol.
    Returns list of outcome dicts.
    """
    results = []
    if not isinstance(routing, dict):
        return results

    for protocol in ("eigrp", "ospf", "rip"):
        instances = routing.get(protocol, [])
        if isinstance(instances, list) and len(instances) > 1:
            results.append({
                "feature": f"show_running_config.routing.{protocol}.instances",
                "expected": f"1 {protocol.upper()} instance",
                "actual": f"{len(instances)} instances",
                "status": "extra",
                "outcome_code": "EXTRA_ROUTING_INSTANCE",
            })

    return results


def _classify_static_routes(static_routes):
    """Separate default routes (0.0.0.0/0) from other static routes."""
    default_routes = []
    other_routes = []
    for route in (static_routes or []):
        if not isinstance(route, dict):
            continue
        network = str(route.get("network", ""))
        mask = str(route.get("mask", ""))
        if network == "0.0.0.0" and mask == "0.0.0.0":
            default_routes.append(route)
        else:
            other_routes.append(route)
    return default_routes, other_routes


def _check_dhcp_excluded_conflicts(parsed_config):
    """Check if excluded IPs fall outside any configured DHCP pool range."""
    results = []
    show_run = parsed_config.get("show_running_config", {}) or {}
    excluded_ranges = show_run.get("dhcp_excluded", [])
    dhcp_pools = show_run.get("dhcp_pools", {})

    if not excluded_ranges or not dhcp_pools:
        return results

    # Collect all pool network ranges
    pool_networks = []
    for pool_name, pool_cfg in dhcp_pools.items():
        if not isinstance(pool_cfg, dict):
            continue
        network_str = pool_cfg.get("network")
        if not network_str:
            continue
        try:
            net = ipaddress.ip_network(network_str.replace(" ", "/"), strict=False)
            pool_networks.append((pool_name, net))
        except (ValueError, TypeError):
            continue

    for excl in excluded_ranges:
        if not isinstance(excl, dict):
            continue
        try:
            start = ipaddress.ip_address(excl.get("start", ""))
        except (ValueError, TypeError):
            continue

        in_any_pool = False
        for pool_name, net in pool_networks:
            if start in net:
                in_any_pool = True
                break

        if not in_any_pool:
            results.append({
                "feature": "show_running_config.dhcp_excluded.bad_exclusion",
                "expected": "Excluded IPs within a configured DHCP pool range",
                "actual": f"Excluded IP {start} not in any pool",
                "status": "mismatch",
                "outcome_code": "MISMATCH_DHCP_BAD_EXCLUSION",
            })

    return results


def _check_dhcp_empty_pools(student_pools):
    """Detect DHCP pools that exist but have no network statement."""
    results = []
    if not isinstance(student_pools, dict):
        return results
    for pool_name, pool_cfg in student_pools.items():
        if not isinstance(pool_cfg, dict):
            continue
        network = pool_cfg.get("network")
        if not network:
            results.append({
                "feature": f"show_running_config.dhcp_pools.{pool_name}.network",
                "expected": "DHCP pool with network statement",
                "actual": "Pool created but no network configured",
                "status": "missing",
                "outcome_code": "MISSING_DHCP_EMPTY_POOL",
            })
    return results


def _check_trunk_ip_assigned(template_interfaces, student_interfaces):
    """Detect interfaces that should be trunks (have sub-interfaces) but have an IP assigned."""
    results = []
    if not isinstance(student_interfaces, dict):
        return results

    # Collect base interfaces that have sub-interfaces in template
    template_bases_with_subint = set()
    for iface_name in (template_interfaces or {}):
        if not isinstance(iface_name, str):
            continue
        if "." in iface_name:
            base = iface_name.rsplit(".", 1)[0]
            template_bases_with_subint.add(base)

    for base_iface in template_bases_with_subint:
        s_cfg = student_interfaces.get(base_iface)
        if not isinstance(s_cfg, dict):
            continue
        if s_cfg.get("ip") is not None:
            results.append({
                "feature": f"show_running_config.interfaces.{base_iface}.trunk_ip_assigned",
                "expected": "No IP on trunk interface (sub-interfaces expected)",
                "actual": f"IP {s_cfg.get('ip')} assigned to trunk interface",
                "status": "mismatch",
                "outcome_code": "MISMATCH_TRUNK_IP_ASSIGNED",
            })
    return results


def _check_extra_ppp(template_interfaces, student_interfaces):
    """Detect PPP configured on interfaces that should not have it."""
    results = []
    if not isinstance(student_interfaces, dict) or not isinstance(template_interfaces, dict):
        return results

    for iface_name, s_cfg in student_interfaces.items():
        if not isinstance(s_cfg, dict):
            continue
        if s_cfg.get("encapsulation") != "ppp":
            continue
        t_cfg = template_interfaces.get(iface_name, {})
        if not isinstance(t_cfg, dict):
            t_cfg = {}
        if t_cfg.get("encapsulation") != "ppp":
            results.append({
                "feature": f"show_running_config.interfaces.{iface_name}.extra_ppp",
                "expected": f"encapsulation {t_cfg.get('encapsulation', 'hdlc (default)')}",
                "actual": "encapsulation ppp",
                "status": "extra",
                "outcome_code": "EXTRA_PPP_ENABLED",
            })
    return results


def _check_nat_completeness(student_nat):
    """Check for missing NAT ACL binding and overload when NAT pools exist."""
    results = []
    if not isinstance(student_nat, dict):
        return results

    pools = student_nat.get("pools", [])
    sources = student_nat.get("inside_source", [])

    if not pools:
        return results

    # Check each pool has an ACL binding via inside_source
    pool_names = set()
    for pool in pools:
        if isinstance(pool, dict) and pool.get("name"):
            pool_names.add(pool["name"])

    bound_pools = set()
    for src in (sources or []):
        if isinstance(src, dict) and src.get("pool"):
            bound_pools.add(src["pool"])

    for pool_name in pool_names:
        if pool_name not in bound_pools:
            results.append({
                "feature": f"show_running_config.nat.pools.{pool_name}.acl_binding",
                "expected": f"ip nat inside source list <acl> pool {pool_name}",
                "actual": "No inside source binding for this pool",
                "status": "missing",
                "outcome_code": "MISSING_NAT_ACL",
            })

    return results


def _check_etherchannel_members(template_ec_summary, student_ec_summary):
    """Compare etherchannel group membership from show etherchannel summary."""
    results = []
    if not isinstance(template_ec_summary, dict) or not isinstance(student_ec_summary, dict):
        return results

    t_groups = template_ec_summary.get("groups", {})
    s_groups = student_ec_summary.get("groups", {})

    for group_num, t_info in t_groups.items():
        if not isinstance(t_info, dict):
            continue
        s_info = s_groups.get(group_num)
        if not s_info:
            results.append({
                "feature": f"verification.show_etherchannel_summary.groups.{group_num}",
                "expected": t_info,
                "actual": None,
                "status": "missing",
                "outcome_code": "MISSING_ETHERCHANNEL",
            })
            continue

        # Compare members
        t_members = set()
        for m in t_info.get("members", []):
            if isinstance(m, dict):
                t_members.add(m.get("interface", ""))
        s_members = set()
        for m in s_info.get("members", []):
            if isinstance(m, dict):
                s_members.add(m.get("interface", ""))

        for missing_m in sorted(t_members - s_members):
            results.append({
                "feature": f"verification.show_etherchannel_summary.groups.{group_num}.members.{missing_m}",
                "expected": f"Interface {missing_m} in port-channel group {group_num}",
                "actual": "Not in group",
                "status": "missing",
                "outcome_code": "MISSING_ETHERCHANNEL_MEMBER",
            })
        for extra_m in sorted(s_members - t_members):
            results.append({
                "feature": f"verification.show_etherchannel_summary.groups.{group_num}.members.{extra_m}",
                "expected": f"Interface {extra_m} not in group {group_num}",
                "actual": "In group",
                "status": "extra",
                "outcome_code": "EXTRA_ETHERCHANNEL_MEMBER",
            })

    # Extra groups in student
    for group_num, s_info in s_groups.items():
        if group_num not in t_groups:
            results.append({
                "feature": f"verification.show_etherchannel_summary.groups.{group_num}",
                "expected": None,
                "actual": s_info,
                "status": "extra",
                "outcome_code": "EXTRA_ETHERCHANNEL",
            })

    return results


# ─────────────────────────────────────────────────────────────────
# Outcome Code Mapping Helpers
# ─────────────────────────────────────────────────────────────────

def _outcome_code_for_path(full_key, status):
    """Determine the specific ACCMS outcome code based on feature path and status."""

    # Hostname
    if full_key.endswith(".hostname"):
        return f"{'MISMATCH' if status == 'mismatch' else 'MISSING' if status == 'missing' else 'EXTRA'}_HOSTNAME"

    # Banner MOTD
    if "banner_motd" in full_key:
        if status == "extra":
            return "EXTRA_MOTD"
        return "MISMATCH_MOTD" if status == "mismatch" else "MISSING_MOTD"

    # Routing protocol (handled by routing block comparison)
    if full_key.endswith(".routing.protocol"):
        if status == "missing":
            return "MISSING_ROUTING_PROTOCOL"
        if status == "extra":
            return "EXTRA_ROUTING_PROTOCOL"
        return "MISMATCH_ROUTING_PROTOCOL"

    # EIGRP AS number
    if ".eigrp" in full_key and full_key.endswith(".asn"):
        return "MISMATCH_EIGRP_AS_INCORRECT"

    # RIP version
    if ".rip" in full_key and full_key.endswith(".version"):
        return "MISMATCH_RIP_VERSION"

    # Routing networks
    if ".routing." in full_key and ".networks" in full_key:
        if status == "missing":
            return "MISSING_ROUTING_NETWORK"
        if status == "extra":
            return "EXTRA_NETWORK_ADVERTISED"
        return "MISMATCH_ROUTING_NETWORK"

    # Passive interfaces
    if ".passive_interface" in full_key:
        if status == "missing":
            return "MISSING_PASSIVE_INTERFACE"
        return "MISMATCH_ROUTING_PASSIVE"

    # Auto-summary
    if ".auto_summary" in full_key:
        return "MISMATCH_AUTO_SUMMARY"

    # Redistribute
    if ".redistribute" in full_key:
        if status == "missing":
            return "MISSING_REDISTRIBUTE"
        if status == "extra":
            return "EXTRA_REDISTRIBUTE"
        return "MISMATCH_ROUTING_REDISTRIBUTE"

    # Static routes
    if ".static_routes" in full_key:
        if status == "missing":
            return "MISSING_STATIC_ROUTE"
        if status == "extra":
            return "EXTRA_STATIC_ROUTE"
        return "MISMATCH_STATIC_ROUTE_NETWORK"

    # Default gateway
    if full_key.endswith(".default_gateway"):
        if status == "missing":
            return "MISSING_DEFAULT_GATEWAY"
        return "MISMATCH_GATEWAY_ADDRESS"

    # IP address
    if full_key.endswith(".ip") and ".interfaces." in full_key:
        if status == "missing":
            return "MISSING_IP"
        return "MISMATCH_IP"

    # Subnet mask
    if full_key.endswith(".mask") and ".interfaces." in full_key:
        return "MISMATCH_MASK"

    # Shutdown
    if full_key.endswith(".shutdown"):
        if status == "missing":
            return "MISSING_SHUTDOWN"
        return "MISMATCH_SHUTDOWN"

    # Clock rate
    if full_key.endswith(".clock_rate"):
        if status == "missing":
            return "MISSING_CLOCK_RATE"
        return "MISMATCH_CLOCK_RATE"

    # Description
    if full_key.endswith(".description"):
        if status == "missing":
            return "MISSING_DESCRIPTION"
        if status == "extra":
            return "EXTRA_DESCRIPTION"
        return "MISSING_DESCRIPTION"

    # Encapsulation
    if full_key.endswith(".encapsulation"):
        if status == "missing":
            return "MISSING_ENCAPSULATION"
        return "MISMATCH_ENCAPSULATION"

    # PPP authentication
    if full_key.endswith(".ppp_authentication"):
        if status == "missing":
            return "MISSING_PPP_AUTH"
        return "MISMATCH_PPP_AUTH"

    # PPP credentials
    if "ppp_chap_hostname" in full_key:
        return "MISMATCH_PPP_USERNAME"
    if "ppp_chap_password" in full_key:
        return "MISMATCH_PPP_PASSWORD"
    if "ppp_pap_username" in full_key:
        return "MISMATCH_PPP_USERNAME"

    # Switchport mode
    if full_key.endswith(".switchport_mode"):
        return "MISMATCH_SWITCHPORT_MODE"

    # Access VLAN
    if full_key.endswith(".access_vlan"):
        return "MISMATCH_ACCESS_VLAN"

    # Trunk native VLAN
    if full_key.endswith(".trunk_native_vlan"):
        return "MISMATCH_TRUNK_NATIVE_VLAN"

    # Trunk allowed VLANs
    if full_key.endswith(".trunk_allowed_vlans"):
        if status == "missing":
            return "MISSING_TRUNK_ALLOWED_VLAN"
        if status == "extra":
            return "EXTRA_TRUNK_ALLOWED_VLAN"
        return "MISMATCH_TRUNK_ALLOWED_VLANS"

    # Trunk encapsulation
    if full_key.endswith(".trunk_encapsulation"):
        return "MISMATCH_TRUNK_ENCAPSULATION"

    # Port security
    if ".port_security." in full_key:
        if "maximum" in full_key:
            return "MISMATCH_PORT_SECURITY_MAX"
        if "violation" in full_key:
            return "MISMATCH_PORT_SECURITY_VIOLATION"
        if "sticky" in full_key:
            return "MISMATCH_PORT_SECURITY_STICKY"
        if "enabled" in full_key:
            if status == "missing":
                return "MISSING_PORT_SECURITY"
            return "MISMATCH_PORT_SECURITY_VIOLATION"

    # STP interface
    if full_key.endswith(".stp_portfast"):
        if status == "missing":
            return "MISSING_STP_PORTFAST"
        return "MISMATCH_STP_PORTFAST"
    if full_key.endswith(".stp_bpduguard"):
        if status == "missing":
            return "MISSING_STP_BPDU_GUARD"
        return "MISMATCH_STP_BPDU_GUARD"
    if full_key.endswith(".stp_root_guard"):
        return "MISMATCH_STP_ROOT_GUARD"
    if full_key.endswith(".stp_cost"):
        return "MISMATCH_STP_PORT_COST"
    if full_key.endswith(".stp_port_priority"):
        return "MISMATCH_STP_PORT_PRIORITY"

    # STP global
    if ".spanning_tree.mode" in full_key:
        if status == "missing":
            return "MISSING_STP_MODE"
        return "MISMATCH_STP_MODE"
    if ".spanning_tree.vlan_priorities" in full_key:
        if status == "missing":
            return "MISSING_STP_PRIORITY"
        if status == "extra":
            return "EXTRA_STP_PRIORITY"
        return "MISMATCH_STP_PRIORITY"
    if ".spanning_tree.vlan_root" in full_key:
        if status == "missing":
            return "MISSING_STP_ROOT_FORCED"
        return "MISMATCH_STP_PRIORITY"

    # EtherChannel
    if ".channel_group" in full_key:
        if ".group" in full_key:
            if status == "missing":
                return "MISSING_ETHERCHANNEL"
            return "MISMATCH_ETHERCHANNEL_GROUP"
        if ".mode" in full_key:
            return "MISMATCH_ETHERCHANNEL_MODE"
        if status == "missing":
            return "MISSING_ETHERCHANNEL"
        return "MISMATCH_ETHERCHANNEL_GROUP"
    if ".etherchannel.load_balance" in full_key:
        return "MISMATCH_ETHERCHANNEL_LOAD_BALANCE"

    # LACP/PAgP
    if full_key.endswith(".lacp_port_priority"):
        return "MISMATCH_LACP_PORT_PRIORITY"
    if "lacp_system_priority" in full_key:
        return "MISMATCH_LACP_SYSTEM_PRIORITY"

    # OSPF interface
    if full_key.endswith(".ospf_network_type"):
        if status == "missing":
            return "MISSING_OSPF_POINT_TO_POINT"
        return "MISSING_OSPF_POINT_TO_POINT"

    # VTY
    if ".vty." in full_key:
        if "transport" in full_key:
            return "MISMATCH_VTY_TRANSPORT"
        if "login" in full_key:
            if status == "missing":
                return "MISSING_VTY_LOGIN"
            return "MISMATCH_VTY_TRANSPORT"
        if "password" in full_key:
            if status == "missing":
                return "MISSING_LINE_PASSWORD"
            return "MISMATCH_LINE_PASSWORD"

    # Console
    if ".console." in full_key:
        if "password" in full_key:
            if status == "missing":
                return "MISSING_LINE_PASSWORD"
            return "MISMATCH_LINE_PASSWORD"

    # Users
    if ".users." in full_key:
        if status == "missing":
            return "MISSING_USER"
        if status == "extra":
            return "EXTRA_USER"
        if ".privilege" in full_key:
            return "MISMATCH_USER_PRIVILEGE"
        if ".auth_type" in full_key:
            return "MISMATCH_USER_AUTH_TYPE"

    # HTTP server
    if ".http_server." in full_key:
        return "MISMATCH_HTTP_SERVER"

    # DHCP pools
    if ".dhcp_pools." in full_key:
        if status == "missing":
            return "MISSING_DHCP_POOL"
        if status == "extra":
            return "EXTRA_DHCP_POOL"
        return "MISMATCH_DHCP_POOL"

    # DHCP excluded
    if ".dhcp_excluded" in full_key:
        if status == "missing":
            return "MISSING_DHCP_EXCLUDED"
        return "MISMATCH_DHCP_EXCLUDED"

    # NAT
    if ".nat." in full_key:
        if "inside_interfaces" in full_key:
            if status == "missing":
                return "MISSING_NAT_INSIDE"
            return "MISMATCH_NAT_DIRECTION"
        if "outside_interfaces" in full_key:
            if status == "missing":
                return "MISSING_NAT_OUTSIDE"
            return "MISMATCH_NAT_DIRECTION"
        if "pools" in full_key:
            if status == "missing":
                return "MISSING_NAT_POOL"
            if "netmask" in full_key or "prefix" in full_key:
                return "MISMATCH_NAT_POOL_PREFIX"
            return "MISMATCH_NAT_POOL"
        if "inside_source" in full_key:
            if status == "missing":
                return "MISSING_NAT_SOURCE"
            if status == "extra":
                return "EXTRA_NAT_SOURCE"
            if "overload" in full_key:
                return "EXTRA_NAT_OVERLOAD"
            return "MISMATCH_NAT_ACL_BINDING"

    # Access lists
    if ".access_lists." in full_key:
        if status == "missing":
            if ".rules" in full_key:
                return "MISSING_ACL_RULE"
            return "MISSING_ACL"
        if status == "extra":
            if ".rules" in full_key:
                return "EXTRA_ACL_RULE"
            return "EXTRA_ACL"
        if ".type" in full_key:
            return "MISMATCH_ACL_TYPE"
        if ".rules" in full_key:
            return "MISMATCH_ACL_RULE"
        return "MISMATCH_ACL_RULE"

    # Access groups on interface
    if ".access_groups" in full_key:
        if status == "missing":
            return "MISSING_ACL_APPLIED"
        if status == "extra":
            return "EXTRA_ACL_APPLIED"
        return "MISMATCH_ACL_RULE"

    # VLAN related (show vlan brief)
    if ".show_vlan_brief.vlans." in full_key:
        if ".name" in full_key:
            if status == "missing":
                return "MISSING_VLAN_NAME"
            return "MISMATCH_VLAN_NAME"
        if ".vlan" in full_key:
            return "MISMATCH_VLAN_ID"
        if status == "missing":
            return "MISSING_VLAN"
        if status == "extra":
            return "EXTRA_VLAN"
        return "MISMATCH_VLAN_ID"

    # VLAN SVI interface mismatch
    if ".Vlan.interface" in full_key:
        if status == "missing":
            return "MISSING_VLAN_INTERFACE"
        return "MISMATCH_VLAN_INTERFACE"

    # Sub-interface mismatch
    if ".subinterface" in full_key:
        return "MISMATCH_SUB_INT_VLAN"

    # Interfaces (generic — catch-all)
    if ".interfaces." in full_key:
        if status == "missing":
            return "MISSING_INTERFACE_CONFIG"
        if status == "extra":
            return "EXTRA_IFACE_CONFIGURED"

    # Trunk (from show interfaces trunk)
    if ".show_interfaces_trunk." in full_key:
        if ".encapsulation" in full_key:
            return "MISMATCH_TRUNK_ENCAPSULATION"
        if ".mode" in full_key:
            return "MISMATCH_TRUNK_MODE"
        if ".native_vlan" in full_key:
            return "MISMATCH_TRUNK_NATIVE_VLAN"

    return None


def _make_result(feature, status, expected=None, actual=None, outcome_code=None):
    """Create a standardized result dict with optional outcome code."""
    result = {"feature": feature, "status": status}
    if expected is not None:
        result["expected"] = expected
    if actual is not None:
        result["actual"] = actual
    if outcome_code:
        result["outcome_code"] = outcome_code
    elif status != "correct":
        code = _outcome_code_for_path(feature, status)
        if code:
            result["outcome_code"] = code
    return result


def compare_dicts(template: dict, student: dict, parent_key="") -> list:
    """
    Recursively compares two dictionaries and returns structured results.
    """
    results = []

    def _iface_has_config(val):
        if isinstance(val, dict):
            if not val:
                return False
            for _, v in val.items():
                if isinstance(v, dict):
                    if _iface_has_config(v):
                        return True
                    continue
                if isinstance(v, list):
                    if len(v) > 0:
                        return True
                    continue
                if v is None:
                    continue
                if isinstance(v, str) and v.strip() == "":
                    continue
                return True
            return False
        if isinstance(val, list):
            return len(val) > 0
        if val is None:
            return False
        if isinstance(val, str) and val.strip() == "":
            return False
        return True

    def _split_subinterface(iface_name):
        if not isinstance(iface_name, str):
            return None
        if "." not in iface_name:
            return None
        base, sub = iface_name.rsplit(".", 1)
        if sub.isdigit():
            return base, sub
        return None

    def _split_vlan_iface(iface_name):
        if not isinstance(iface_name, str):
            return None
        if not iface_name.lower().startswith("vlan"):
            return None
        vlan_id = iface_name[4:]
        if vlan_id.isdigit():
            return "Vlan", vlan_id
        return None

    def _is_hw_absent_serial(iface_name, iface_cfg):
        if not isinstance(iface_name, str):
            return False
        if not re.match(r"^Serial0/[123]/\d+$", iface_name):
            return False
        if not isinstance(iface_cfg, dict):
            return False
        if iface_cfg.get("shutdown") is not True:
            return False
        if iface_cfg.get("ip") is not None or iface_cfg.get("mask") is not None:
            return False
        return True

    def _is_ip_field(path):
        if not path.endswith(".ip"):
            return False
        if "show_running_config.interfaces" in path:
            return True
        if "verification.show_ip_interface_brief.interfaces" in path:
            return True
        return False

    def _should_skip_path(path):
        if "sticky_macs" in path:
            return True
        if path.endswith("switching.spanning_tree.extend_system_id"):
            return True
        if "verification.show_interfaces_trunk.trunks" in path and path.endswith(
            ".status"
        ):
            return True
        if re.search(
            r"verification\.show_vlan_brief\.vlans\.(1002|1003|1004|1005)(\.|$)",
            path,
        ):
            return True
        if path.endswith("verification.show_vlan_brief.vlans.1.ports"):
            return True
        if re.search(r"pagp|lacp", path, re.IGNORECASE) and re.search(
            r"timer|negotiated|state|partner", path, re.IGNORECASE
        ):
            return True
        # Skip enable password/secret hash values (compare type only)
        if "enable_secret_type" in path or "enable_password_type" in path:
            return False  # Don't skip — we compare these
        return False

    # ── Top-level intelligent analysis passes ──
    # These only run at the root level (no parent_key)
    if not parent_key:
        # OSPF area validation on student config
        student_show_run = student.get("show_running_config", {}) or {}
        student_routing = student_show_run.get("routing", {}) or {}
        student_ospf = student_routing.get("ospf", [])
        if student_ospf:
            results.extend(_check_ospf_areas(student_ospf))

        # NAT pool range validation
        template_show_run = template.get("show_running_config", {}) or {}
        t_nat = template_show_run.get("nat", {}) or {}
        s_nat = student_show_run.get("nat", {}) or {}
        results.extend(_check_nat_pool_ranges(
            t_nat.get("pools", []),
            s_nat.get("pools", []),
        ))

        # Configured-but-shutdown detection
        s_interfaces = student_show_run.get("interfaces", {}) or {}
        results.extend(_check_configured_but_shutdown(s_interfaces))

        # ACL not applied detection
        s_acls = student_show_run.get("access_lists", {}) or {}
        results.extend(_check_acl_not_applied(s_acls, s_interfaces))

        # Routing instance detection
        results.extend(_check_routing_instances(student_routing))

        # DHCP bad exclusion detection
        results.extend(_check_dhcp_excluded_conflicts(student))

        # ACL shadow/pointless rule detection
        for acl_name, acl_data in s_acls.items():
            if not isinstance(acl_data, dict):
                continue
            rules = acl_data.get("rules", [])
            pointless = _detect_acl_pointless_rules(rules)
            for rule_idx, code in pointless:
                results.append({
                    "feature": f"show_running_config.access_lists.{acl_name}.rules.{rule_idx}",
                    "expected": "Effective ACL rule",
                    "actual": f"Rule shadowed: {rules[rule_idx]}",
                    "status": "mismatch",
                    "outcome_code": code,
                })

        # DHCP empty pool detection
        s_dhcp_pools = student_show_run.get("dhcp_pools", {}) or {}
        results.extend(_check_dhcp_empty_pools(s_dhcp_pools))

        # Trunk IP assigned (interface has IP when sub-interfaces expected)
        t_interfaces = template_show_run.get("interfaces", {}) or {}
        results.extend(_check_trunk_ip_assigned(t_interfaces, s_interfaces))

        # Extra PPP (PPP on interfaces that should not have it)
        results.extend(_check_extra_ppp(t_interfaces, s_interfaces))

        # NAT completeness (pool without ACL binding)
        results.extend(_check_nat_completeness(s_nat))

        # EtherChannel member checks (from show etherchannel summary)
        template_verification = template.get("verification", {}) or {}
        student_verification = student.get("verification", {}) or {}
        t_ec_summary = template_verification.get("show_etherchannel_summary", {})
        s_ec_summary = student_verification.get("show_etherchannel_summary", {})
        if t_ec_summary and s_ec_summary:
            results.extend(_check_etherchannel_members(t_ec_summary, s_ec_summary))

    for key, t_val in template.items():
        full_key = f"{parent_key}.{key}" if parent_key else key

        # Ignore volatile NAT statistics values that naturally drift over time.
        if _is_nat_stats_path(full_key) and key in NAT_STATS_VOLATILE_KEYS:
            continue
        if _should_skip_path(full_key):
            continue

        has_student_key = key in student
        s_val = student.get(key)

        # Normalize NAT interface list representation (legacy string vs new list).
        if full_key.endswith("verification.show_ip_nat_statistics.outside_interfaces"):
            t_val = _normalize_interface_list_value(t_val)
            s_val = _normalize_interface_list_value(s_val)
        elif full_key.endswith("verification.show_ip_nat_statistics.inside_interfaces"):
            t_val = _normalize_interface_list_value(t_val)
            s_val = _normalize_interface_list_value(s_val)

        if full_key.endswith("verification.show_vlan_brief") and (
            not isinstance(s_val, dict) or not (s_val.get("vlans") or {})
        ):
            # VLAN info not captured on student device; skip comparison.
            continue
        if _is_ip_field(full_key):
            if not has_student_key:
                if t_val is None:
                    results.append(_make_result(full_key, "correct"))
                else:
                    results.append(_make_result(full_key, "mismatch", t_val, None))
            else:
                if t_val == s_val:
                    results.append(_make_result(full_key, "correct"))
                else:
                    results.append(_make_result(full_key, "mismatch", t_val, s_val))
            continue

        if not has_student_key:
            results.append(_make_result(full_key, "missing", t_val, None))
        elif full_key.endswith("banner_motd"):
            # Banner content is not graded by exact string; only presence matters.
            if t_val is None and s_val is None:
                results.append(_make_result(full_key, "correct"))
            elif t_val is None and s_val is not None:
                results.append(_make_result(full_key, "extra", None, s_val))
            elif t_val is not None and s_val is None:
                results.append(_make_result(full_key, "missing", t_val, None))
            else:
                results.append(_make_result(full_key, "correct"))
        elif t_val is None and s_val is None:
            results.append(_make_result(full_key, "correct"))
        elif t_val is None and s_val is not None:
            results.append(_make_result(full_key, "extra", None, s_val))
        elif t_val is not None and s_val is None:
            results.append(_make_result(full_key, "missing", t_val, None))
        elif isinstance(t_val, dict):
            # Hardware models can expose different interface sets.
            # Compare only interfaces present on both sides.
            if full_key.endswith("interfaces") and isinstance(s_val, dict):
                t_ifaces = set(t_val.keys())
                s_ifaces = set(s_val.keys())
                common_interfaces = sorted(t_ifaces & s_ifaces)
                for iface in common_interfaces:
                    iface_key = f"{full_key}.{iface}"
                    t_iface = t_val.get(iface)
                    s_iface = s_val.get(iface)
                    if isinstance(t_iface, dict) and isinstance(s_iface, dict):
                        results.extend(compare_dicts(t_iface, s_iface, iface_key))
                    elif t_iface == s_iface:
                        results.append(_make_result(iface_key, "correct"))
                    else:
                        results.append(_make_result(iface_key, "mismatch", t_iface, s_iface))
                # Detect configured interfaces missing on either side.
                missing_ifaces = sorted(t_ifaces - s_ifaces)
                extra_ifaces = sorted(s_ifaces - t_ifaces)

                missing_items = []
                extra_items = []
                for iface in missing_ifaces:
                    t_iface = t_val.get(iface)
                    if _iface_has_config(t_iface):
                        if _is_hw_absent_serial(iface, t_iface):
                            continue
                        missing_items.append((iface, t_iface))
                for iface in extra_ifaces:
                    s_iface = s_val.get(iface)
                    if _iface_has_config(s_iface):
                        extra_items.append((iface, s_iface))

                # Merge dot-subinterface missing+extra into a single mismatch.
                missing_by_base = {}
                extra_by_base = {}
                for iface, t_iface in missing_items:
                    split = _split_subinterface(iface)
                    if not split:
                        continue
                    base, _ = split
                    missing_by_base.setdefault(base, []).append((iface, t_iface))
                for iface, s_iface in extra_items:
                    split = _split_subinterface(iface)
                    if not split:
                        continue
                    base, _ = split
                    extra_by_base.setdefault(base, []).append((iface, s_iface))

                merged_missing = set()
                merged_extra = set()
                for base in sorted(set(missing_by_base.keys()) & set(extra_by_base.keys())):
                    m_list = sorted(missing_by_base[base], key=lambda x: x[0])
                    e_list = sorted(extra_by_base[base], key=lambda x: x[0])
                    for (m_iface, m_cfg), (e_iface, e_cfg) in zip(m_list, e_list):
                        results.append(_make_result(
                            f"{full_key}.{base}.subinterface",
                            "mismatch",
                            {"name": m_iface, "config": m_cfg},
                            {"name": e_iface, "config": e_cfg},
                            "MISMATCH_SUB_INT_VLAN",
                        ))
                        merged_missing.add(m_iface)
                        merged_extra.add(e_iface)

                # Merge VLAN interface missing+extra into a single mismatch.
                missing_vlans = []
                extra_vlans = []
                for iface, t_iface in missing_items:
                    if iface in merged_missing:
                        continue
                    if _split_vlan_iface(iface):
                        missing_vlans.append((iface, t_iface))
                for iface, s_iface in extra_items:
                    if iface in merged_extra:
                        continue
                    if _split_vlan_iface(iface):
                        extra_vlans.append((iface, s_iface))

                if missing_vlans and extra_vlans:
                    missing_vlans = sorted(missing_vlans, key=lambda x: x[0])
                    extra_vlans = sorted(extra_vlans, key=lambda x: x[0])
                    for (m_iface, m_cfg), (e_iface, e_cfg) in zip(
                        missing_vlans, extra_vlans
                    ):
                        results.append(_make_result(
                            f"{full_key}.Vlan.interface",
                            "mismatch",
                            {"name": m_iface, "config": m_cfg},
                            {"name": e_iface, "config": e_cfg},
                            "MISMATCH_VLAN_INTERFACE",
                        ))
                        merged_missing.add(m_iface)
                        merged_extra.add(e_iface)

                for iface, t_iface in missing_items:
                    if iface in merged_missing:
                        continue
                    results.append(_make_result(
                        f"{full_key}.{iface}", "missing", t_iface, None
                    ))
                for iface, s_iface in extra_items:
                    if iface in merged_extra:
                        continue
                    results.append(_make_result(
                        f"{full_key}.{iface}", "extra", None, s_iface
                    ))
                continue
            if full_key.endswith("show_running_config.routing") and isinstance(
                s_val, dict
            ):
                t_protocols = {
                    k
                    for k, v in t_val.items()
                    if k != "static_routes" and _iface_has_config(v)
                }
                s_protocols = {
                    k
                    for k, v in s_val.items()
                    if k != "static_routes" and _iface_has_config(v)
                }
                if t_protocols and s_protocols and t_protocols.isdisjoint(s_protocols):
                    results.append(_make_result(
                        f"{full_key}.protocol",
                        "mismatch",
                        sorted(t_protocols),
                        sorted(s_protocols),
                        "MISMATCH_ROUTING_PROTOCOL",
                    ))
                    continue
                if t_protocols and not s_protocols:
                    results.append(_make_result(
                        f"{full_key}.protocol",
                        "missing",
                        sorted(t_protocols),
                        [],
                        "MISSING_ROUTING_PROTOCOL",
                    ))
                    continue
                if s_protocols and not t_protocols:
                    results.append(_make_result(
                        f"{full_key}.protocol",
                        "extra",
                        [],
                        sorted(s_protocols),
                        "EXTRA_ROUTING_PROTOCOL",
                    ))
                    continue
            # VLAN brief: merge missing/extra VLAN IDs with same VLAN name.
            if full_key.endswith("verification.show_vlan_brief.vlans") and isinstance(
                s_val, dict
            ):
                skip_vlan_ids = {"1002", "1003", "1004", "1005"}
                t_vlans = set(k for k in t_val.keys() if k not in skip_vlan_ids)
                s_vlans = set(k for k in s_val.keys() if k not in skip_vlan_ids)
                common_vlans = sorted(t_vlans & s_vlans)
                for vlan_id in common_vlans:
                    vlan_key = f"{full_key}.{vlan_id}"
                    t_vlan = t_val.get(vlan_id)
                    s_vlan = s_val.get(vlan_id)
                    if isinstance(t_vlan, dict) and isinstance(s_vlan, dict):
                        if vlan_id == "1":
                            t_vlan = dict(t_vlan)
                            s_vlan = dict(s_vlan)
                            t_vlan.pop("ports", None)
                            s_vlan.pop("ports", None)
                        results.extend(compare_dicts(t_vlan, s_vlan, vlan_key))
                    elif t_vlan == s_vlan:
                        results.append(_make_result(vlan_key, "correct"))
                    else:
                        results.append(_make_result(vlan_key, "mismatch", t_vlan, s_vlan))

                missing_vlans = sorted(t_vlans - s_vlans)
                extra_vlans = sorted(s_vlans - t_vlans)

                missing_items = []
                extra_items = []
                for vlan_id in missing_vlans:
                    t_vlan = t_val.get(vlan_id)
                    if _iface_has_config(t_vlan):
                        missing_items.append((vlan_id, t_vlan))
                for vlan_id in extra_vlans:
                    s_vlan = s_val.get(vlan_id)
                    if _iface_has_config(s_vlan):
                        extra_items.append((vlan_id, s_vlan))

                missing_by_name = {}
                extra_by_name = {}
                for vlan_id, t_vlan in missing_items:
                    name = None
                    if isinstance(t_vlan, dict):
                        name = t_vlan.get("name")
                    if name:
                        missing_by_name.setdefault(name, []).append((vlan_id, t_vlan))
                for vlan_id, s_vlan in extra_items:
                    name = None
                    if isinstance(s_vlan, dict):
                        name = s_vlan.get("name")
                    if name:
                        extra_by_name.setdefault(name, []).append((vlan_id, s_vlan))

                merged_missing = set()
                merged_extra = set()
                for name in sorted(
                    set(missing_by_name.keys()) & set(extra_by_name.keys())
                ):
                    m_list = sorted(missing_by_name[name], key=lambda x: x[0])
                    e_list = sorted(extra_by_name[name], key=lambda x: x[0])
                    for (m_id, m_cfg), (e_id, e_cfg) in zip(m_list, e_list):
                        results.append(_make_result(
                            f"{full_key}.vlan",
                            "mismatch",
                            {"id": m_id, "config": m_cfg},
                            {"id": e_id, "config": e_cfg},
                            "MISMATCH_VLAN_ID",
                        ))
                        merged_missing.add(m_id)
                        merged_extra.add(e_id)

                for vlan_id, t_vlan in missing_items:
                    if vlan_id in merged_missing:
                        continue
                    results.append(_make_result(
                        f"{full_key}.{vlan_id}", "missing", t_vlan, None, "MISSING_VLAN"
                    ))
                for vlan_id, s_vlan in extra_items:
                    if vlan_id in merged_extra:
                        continue
                    results.append(_make_result(
                        f"{full_key}.{vlan_id}", "extra", None, s_vlan, "EXTRA_VLAN"
                    ))
                continue
            results.extend(compare_dicts(t_val, s_val, full_key))
        elif isinstance(t_val, list):
            if not isinstance(s_val, list):
                results.append(_make_result(full_key, "mismatch", t_val, s_val))
                continue

            # Special handling for user lists keyed by username.
            t_is_user_list = all(
                isinstance(item, dict) and "username" in item for item in t_val
            )
            s_is_user_list = all(
                isinstance(item, dict) and "username" in item for item in s_val
            )

            if t_is_user_list and s_is_user_list:
                t_users = {u["username"]: u for u in t_val}
                s_users = {u["username"]: u for u in s_val}
                for uname, udata in t_users.items():
                    if uname not in s_users:
                        results.append(_make_result(
                            f"{full_key}.{uname}", "missing", udata, None, "MISSING_USER"
                        ))
                    else:
                        if udata.get("privilege") != s_users[uname].get("privilege"):
                            results.append(_make_result(
                                f"{full_key}.{uname}.privilege",
                                "mismatch",
                                udata.get("privilege"),
                                s_users[uname].get("privilege"),
                                "MISMATCH_USER_PRIVILEGE",
                            ))
                        if udata.get("auth_type") != s_users[uname].get("auth_type"):
                            results.append(_make_result(
                                f"{full_key}.{uname}.auth_type",
                                "mismatch",
                                udata.get("auth_type"),
                                s_users[uname].get("auth_type"),
                                "MISMATCH_USER_AUTH_TYPE",
                            ))
                for uname in s_users:
                    if uname not in t_users:
                        results.append(_make_result(
                            f"{full_key}.{uname}", "extra", None, s_users[uname], "EXTRA_USER"
                        ))
                continue

            # Generic list comparison: order-insensitive multiset compare.
            t_counter = Counter(json.dumps(item, sort_keys=True) for item in t_val)
            s_counter = Counter(json.dumps(item, sort_keys=True) for item in s_val)
            if t_counter != s_counter:
                results.append(_make_result(full_key, "mismatch", t_val, s_val))
            else:
                results.append(_make_result(full_key, "correct"))
        else:
            if t_val != s_val:
                results.append(_make_result(full_key, "mismatch", t_val, s_val))
            else:
                results.append(_make_result(full_key, "correct"))

    # Detect extra fields in student config
    for key in student.keys():
        full_key = f"{parent_key}.{key}" if parent_key else key
        if _is_nat_stats_path(full_key) and key in NAT_STATS_VOLATILE_KEYS:
            continue
        if _should_skip_path(full_key):
            continue
        if key not in template:
            results.append(_make_result(full_key, "extra", None, student[key]))

    return results
