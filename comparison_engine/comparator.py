"""
Canonical comparison logic for parsed Cisco configs.

This module compares a template device config against a student's parsed config,
assigns ACCMS outcome codes, and adds higher-level verification checks used by
server.py grading/report generation.
"""
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

_UNSET = object()
VALUE_NOT_PRESENT = "__ACCMS_NOT_PRESENT__"


def _normalize_interface_list_value(value):
    """Convert interface list values into sorted unique strings."""
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
    """Return True when a comparison path points at NAT statistics output."""
    return (
        full_key.endswith("verification.show_ip_nat_statistics")
        or ".verification.show_ip_nat_statistics." in full_key
    )


def _normalize_role_name(value: str) -> str:
    """Convert a scheme role name into a stable token-safe identifier."""
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
    """Replace VLAN number tokens in text with scheme-normalized VLAN tokens."""
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
    """Recursively normalize VLAN IDs inside parsed config values."""
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
            results.append(
                {
                    "feature": "show_running_config.routing.ospf.area_validation",
                    "expected": "area 0 for single-area OSPF",
                    "actual": f"area {sorted(areas_used)[0]}",
                    "status": "mismatch",
                    "outcome_code": "MISMATCH_OSPF_AREA_ID_SINGLE",
                }
            )

        # Multi-area OSPF must have area 0
        if is_multi_area and "0" not in areas_used:
            results.append(
                {
                    "feature": "show_running_config.routing.ospf.area_validation",
                    "expected": "area 0 must exist in multi-area OSPF",
                    "actual": f"areas: {sorted(areas_used)}",
                    "status": "mismatch",
                    "outcome_code": "MISMATCH_OSPF_AREA_0_MISSING",
                }
            )

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
            results.append(
                {
                    "feature": f"show_running_config.nat.pools.{name}.range",
                    "expected": f"{t_start}-{t_end}",
                    "actual": f"{s_start}-{s_end}",
                    "status": "mismatch",
                    "outcome_code": "MISMATCH_NAT_POOL_RANGE_OUTSIDE",
                }
            )

        # Student range doesn't cover entire template
        if s_start > t_start or s_end < t_end:
            results.append(
                {
                    "feature": f"show_running_config.nat.pools.{name}.range",
                    "expected": f"{t_start}-{t_end}",
                    "actual": f"{s_start}-{s_end}",
                    "status": "mismatch",
                    "outcome_code": "MISMATCH_NAT_POOL_RANGE_INSIDE",
                }
            )

    return results


def _check_configured_but_shutdown(interfaces):
    """Detect interfaces that are shutdown but have meaningful config.
    Returns list of outcome dicts.
    """
    results = []
    config_keys = {
        "ip",
        "mask",
        "encapsulation",
        "switchport_mode",
        "access_vlan",
        "trunk_native_vlan",
        "trunk_allowed_vlans",
        "ppp_authentication",
        "channel_group",
        "ospf_network_type",
    }

    for iface_name, iface_cfg in interfaces.items():
        if not isinstance(iface_cfg, dict):
            continue
        if iface_cfg.get("shutdown") is not True:
            continue
        has_config = any(
            iface_cfg.get(k) is not None for k in config_keys if k in iface_cfg
        )
        # Check if the IP is set (not None)
        if has_config and iface_cfg.get("ip") is not None:
            results.append(
                {
                    "feature": f"show_running_config.interfaces.{iface_name}.configured_if_shutdown",
                    "expected": "shutdown with no configuration OR no shutdown with configuration",
                    "actual": f"shutdown=True but has IP/config",
                    "status": "mismatch",
                    "outcome_code": "MISMATCH_CONFIGURED_IF_SHUTDOWN",
                }
            )

    return results


def _check_acl_not_applied(access_lists, interfaces, nat=None, template_access_lists=None):
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

    # Treat NAT source ACL bindings as valid ACL usage.
    nat_inside_source = []
    if isinstance(nat, dict):
        nat_inside_source = nat.get("inside_source", []) or []
    for source in nat_inside_source:
        if not isinstance(source, dict):
            continue
        acl_name = str(source.get("acl", "")).strip()
        if acl_name:
            applied_acls.add(acl_name)

    template_access_lists = template_access_lists or {}
    for acl_name in access_lists:
        if acl_name not in applied_acls:
            in_template = acl_name in template_access_lists
            results.append(
                {
                    "feature": f"show_running_config.access_lists.{acl_name}.applied",
                    "expected": (
                        "ACL applied to at least one interface"
                        if in_template
                        else "No unused ACL created"
                    ),
                    "actual": (
                        "ACL not applied to any interface"
                        if in_template
                        else f"ACL {acl_name} exists but is not applied to any interface"
                    ),
                    "status": "missing" if in_template else "extra",
                    "outcome_code": (
                        "MISSING_ACL_APPLIED" if in_template else "NON_APPLIED_ACL"
                    ),
                }
            )

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
            results.append(
                {
                    "feature": f"show_running_config.routing.{protocol}.instances",
                    "expected": f"1 {protocol.upper()} instance",
                    "actual": f"{len(instances)} instances",
                    "status": "extra",
                    "outcome_code": "EXTRA_ROUTING_INSTANCE",
                }
            )

    return results


def _classify_static_routes(static_routes):
    """Separate default routes (0.0.0.0/0) from other static routes."""
    default_routes = []
    other_routes = []
    for route in static_routes or []:
        if not isinstance(route, dict):
            continue
        network = str(route.get("network", ""))
        mask = str(route.get("mask", ""))
        if network == "0.0.0.0" and mask == "0.0.0.0":
            default_routes.append(route)
        else:
            other_routes.append(route)
    return default_routes, other_routes


def _as_ipv4_address(value):
    """Parse a value as IPv4Address, returning None when invalid."""
    try:
        return ipaddress.IPv4Address(str(value or "").strip())
    except Exception:
        return None


def _as_ipv4_network(network, mask):
    """Parse network/mask values as IPv4Network, returning None when invalid."""
    try:
        return ipaddress.IPv4Network(
            f"{str(network or '').strip()}/{str(mask or '').strip()}",
            strict=False,
        )
    except Exception:
        return None


def _route_network_key(route):
    """Return a comparable destination key for a static route dict."""
    if not isinstance(route, dict):
        return None
    network = str(route.get("network") or route.get("destination") or "").strip()
    mask = str(route.get("mask") or "").strip()
    if not network:
        return None
    if "/" in network:
        try:
            return str(ipaddress.IPv4Network(network, strict=False))
        except Exception:
            return network
    if mask:
        parsed = _as_ipv4_network(network, mask)
        return str(parsed) if parsed else f"{network}/{mask}"
    return network


def _lookup_interface_config(interface_name, interfaces):
    """Find an interface config by name using case-insensitive matching."""
    if not interface_name or not isinstance(interfaces, dict):
        return None
    wanted = str(interface_name).strip().lower()
    for name, config in interfaces.items():
        if str(name).strip().lower() == wanted:
            return config if isinstance(config, dict) else None
    return None


def _interface_connected_network(interface_name, interfaces):
    """Return the connected IPv4 network for an interface, if configured."""
    config = _lookup_interface_config(interface_name, interfaces)
    if not config:
        return None
    ip = config.get("ip")
    mask = config.get("mask")
    if not ip or not mask:
        return None
    return _as_ipv4_network(ip, mask)


def _static_route_next_hop_equivalent(
    template_next_hop,
    student_next_hop,
    template_interfaces,
    student_interfaces,
):
    """Treat an outgoing-interface static route as equivalent to an IP next hop
    when the IP is reachable through that interface's connected subnet.
    """
    template_next = str(template_next_hop or "").strip()
    student_next = str(student_next_hop or "").strip()
    if template_next == student_next:
        return True

    template_ip = _as_ipv4_address(template_next)
    student_ip = _as_ipv4_address(student_next)

    if template_ip and student_ip:
        return template_ip == student_ip

    if not template_ip and student_ip:
        for interfaces in (template_interfaces, student_interfaces):
            network = _interface_connected_network(template_next, interfaces)
            if network and student_ip in network:
                return True

    if template_ip and not student_ip:
        for interfaces in (student_interfaces, template_interfaces):
            network = _interface_connected_network(student_next, interfaces)
            if network and template_ip in network:
                return True

    return False


def _compare_static_route_lists(
    template_routes,
    student_routes,
    template_interfaces,
    student_interfaces,
    full_key,
):
    """Compare static routes with special handling for equivalent next hops."""
    template_routes = [r for r in (template_routes or []) if isinstance(r, dict)]
    student_routes = [r for r in (student_routes or []) if isinstance(r, dict)]

    if not template_routes and not student_routes:
        return [_make_result(full_key, "correct")]
    if template_routes and not student_routes:
        return [
            _make_result(
                full_key,
                "missing",
                template_routes,
                VALUE_NOT_PRESENT,
                "MISSING_STATIC_ROUTE",
            )
        ]
    if student_routes and not template_routes:
        return [
            _make_result(
                full_key,
                "extra",
                None,
                student_routes,
                "EXTRA_STATIC_ROUTE",
            )
        ]

    used_student_indexes = set()
    next_hop_mismatches = []
    missing_routes = []

    for template_route in template_routes:
        template_key = _route_network_key(template_route)
        same_network_candidates = []
        for idx, student_route in enumerate(student_routes):
            if idx in used_student_indexes:
                continue
            if _route_network_key(student_route) == template_key:
                same_network_candidates.append((idx, student_route))

        matched_idx = None
        for idx, student_route in same_network_candidates:
            if _static_route_next_hop_equivalent(
                template_route.get("next_hop"),
                student_route.get("next_hop"),
                template_interfaces,
                student_interfaces,
            ):
                matched_idx = idx
                break

        if matched_idx is not None:
            used_student_indexes.add(matched_idx)
            continue

        if same_network_candidates:
            idx, student_route = same_network_candidates[0]
            used_student_indexes.add(idx)
            next_hop_mismatches.append(
                {"expected": template_route, "actual": student_route}
            )
        else:
            missing_routes.append(template_route)

    extra_routes = [
        route for idx, route in enumerate(student_routes) if idx not in used_student_indexes
    ]

    if not missing_routes and not extra_routes and not next_hop_mismatches:
        return [_make_result(full_key, "correct")]

    if missing_routes:
        return [
            _make_result(
                full_key,
                "missing",
                missing_routes,
                student_routes,
                "MISSING_STATIC_ROUTE",
            )
        ]

    if extra_routes:
        return [
            _make_result(
                full_key,
                "extra",
                template_routes,
                extra_routes,
                "EXTRA_STATIC_ROUTE",
            )
        ]

    return [
        _make_result(
            full_key,
            "mismatch",
            [item["expected"] for item in next_hop_mismatches],
            [item["actual"] for item in next_hop_mismatches],
            "MISMATCH_STATIC_ROUTE_NEXTHOP",
        )
    ]


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
            results.append(
                {
                    "feature": "show_running_config.dhcp_excluded.bad_exclusion",
                    "expected": "Excluded IPs within a configured DHCP pool range",
                    "actual": f"Excluded IP {start} not in any pool",
                    "status": "mismatch",
                    "outcome_code": "MISMATCH_DHCP_BAD_EXCLUSION",
                }
            )

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
            results.append(
                {
                    "feature": f"show_running_config.dhcp_pools.{pool_name}.network",
                    "expected": "DHCP pool with network statement",
                    "actual": "Pool created but no network configured",
                    "status": "missing",
                    "outcome_code": "MISSING_DHCP_EMPTY_POOL",
                }
            )
    return results


def _check_trunk_ip_assigned(template_interfaces, student_interfaces):
    """Detect interfaces that should be trunks (have sub-interfaces) but have an IP assigned."""
    results = []
    if not isinstance(student_interfaces, dict):
        return results

    # Collect base interfaces that have sub-interfaces in template
    template_bases_with_subint = set()
    for iface_name in template_interfaces or {}:
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
            results.append(
                {
                    "feature": f"show_running_config.interfaces.{base_iface}.trunk_ip_assigned",
                    "expected": "No IP on trunk interface (sub-interfaces expected)",
                    "actual": f"IP {s_cfg.get('ip')} assigned to trunk interface",
                    "status": "mismatch",
                    "outcome_code": "MISMATCH_TRUNK_IP_ASSIGNED",
                }
            )
    return results


def _check_extra_ppp(template_interfaces, student_interfaces):
    """Detect PPP configured on interfaces that should not have it."""
    results = []
    if not isinstance(student_interfaces, dict) or not isinstance(
        template_interfaces, dict
    ):
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
            results.append(
                {
                    "feature": f"show_running_config.interfaces.{iface_name}.extra_ppp",
                    "expected": f"encapsulation {t_cfg.get('encapsulation', 'hdlc (default)')}",
                    "actual": "encapsulation ppp",
                    "status": "extra",
                    "outcome_code": "EXTRA_PPP_ENABLED",
                }
            )
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
    for src in sources or []:
        if isinstance(src, dict) and src.get("pool"):
            bound_pools.add(src["pool"])

    for pool_name in pool_names:
        if pool_name not in bound_pools:
            results.append(
                {
                    "feature": f"show_running_config.nat.pools.{pool_name}.acl_binding",
                    "expected": f"ip nat inside source list <acl> pool {pool_name}",
                    "actual": "No inside source binding for this pool",
                    "status": "missing",
                    "outcome_code": "MISSING_NAT_ACL",
                }
            )

    return results


def _check_etherchannel_members(template_ec_summary, student_ec_summary):
    """Compare etherchannel group membership from show etherchannel summary."""
    results = []
    if not isinstance(template_ec_summary, dict) or not isinstance(
        student_ec_summary, dict
    ):
        return results

    t_groups = template_ec_summary.get("groups", {})
    s_groups = student_ec_summary.get("groups", {})

    for group_num, t_info in t_groups.items():
        if not isinstance(t_info, dict):
            continue
        s_info = s_groups.get(group_num)
        if not s_info:
            results.append(
                {
                    "feature": f"verification.show_etherchannel_summary.groups.{group_num}",
                    "expected": t_info,
                    "actual": None,
                    "status": "missing",
                    "outcome_code": "MISSING_ETHERCHANNEL",
                }
            )
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
            results.append(
                {
                    "feature": f"verification.show_etherchannel_summary.groups.{group_num}.members.{missing_m}",
                    "expected": f"Interface {missing_m} in port-channel group {group_num}",
                    "actual": "Not in group",
                    "status": "missing",
                    "outcome_code": "MISSING_ETHERCHANNEL_MEMBER",
                }
            )
        for extra_m in sorted(s_members - t_members):
            results.append(
                {
                    "feature": f"verification.show_etherchannel_summary.groups.{group_num}.members.{extra_m}",
                    "expected": f"Interface {extra_m} not in group {group_num}",
                    "actual": "In group",
                    "status": "extra",
                    "outcome_code": "EXTRA_ETHERCHANNEL_MEMBER",
                }
            )

    # Extra groups in student
    for group_num, s_info in s_groups.items():
        if group_num not in t_groups:
            results.append(
                {
                    "feature": f"verification.show_etherchannel_summary.groups.{group_num}",
                    "expected": None,
                    "actual": s_info,
                    "status": "extra",
                    "outcome_code": "EXTRA_ETHERCHANNEL",
                }
            )

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

    if full_key.endswith(".routing.rip"):
        return "MISMATCH_ROUTING_PASSIVE"

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
        if "transport" in full_key:
            return "MISMATCH_VTY_TRANSPORT"
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


def _make_result(feature, status, expected=_UNSET, actual=_UNSET, outcome_code=None):
    """Create a standardized result dict with optional outcome code."""
    result = {"feature": feature, "status": status}
    if expected is not _UNSET:
        result["expected"] = expected
    if actual is not _UNSET:
        result["actual"] = actual
    if outcome_code:
        result["outcome_code"] = outcome_code
    elif status != "correct":
        code = _outcome_code_for_path(feature, status)
        if code:
            result["outcome_code"] = code
    return result


def _route_codes(routes):
    """Return the set of route code tokens present in parsed route entries."""
    codes = set()
    if not isinstance(routes, list):
        return codes
    for route in routes:
        if not isinstance(route, dict):
            continue
        code = str(route.get("code", "")).strip().upper()
        if code:
            codes.add(code)
    return codes


def _route_destinations(routes):
    """Return learned/static route destinations, excluding connected/local routes."""
    destinations = set()
    if not isinstance(routes, list):
        return destinations
    for route in routes:
        if not isinstance(route, dict):
            continue
        destination = str(route.get("destination", "")).strip()
        route_type = str(route.get("type", "")).strip().lower()
        code = str(route.get("code", "")).strip().upper()
        if not destination or destination == "-":
            continue
        if route_type in {"directly_connected", "other"}:
            continue
        if code in {"C", "L"}:
            continue
        destinations.add(destination)
    return destinations


def _verification_outcome_code_for_path(full_key, status, expected=None, actual=None):
    """Map verification comparison paths to user-facing outcome codes."""
    feature = str(full_key or "")
    if not feature.startswith("verification."):
        return None

    expected_text = str(expected or "").strip().lower()
    actual_text = str(actual or "").strip().lower()

    if ".show_ip_interface_brief.interfaces." in feature:
        if feature.endswith(".ip"):
            return "VERIFY_IFACE_IP_WRONG"
        if feature.endswith(".status") or feature.endswith(".protocol"):
            if "administratively down" in expected_text or expected_text == "down":
                return "VERIFY_IFACE_ADMIN_UP"
            return "VERIFY_IFACE_DOWN"

    if feature.startswith("verification.show_ip_route"):
        if feature.endswith(".gateway_of_last_resort"):
            if expected_text and "not set" not in expected_text:
                if not actual_text or "not set" in actual_text:
                    return "VERIFY_ROUTE_MISSING_DEFAULT"
            return "VERIFY_GATEWAY_WRONG"
        if feature.endswith(".routes"):
            expected_codes = _route_codes(expected)
            actual_codes = _route_codes(actual)
            expected_destinations = _route_destinations(expected)
            actual_destinations = _route_destinations(actual)
            if "S*" in expected_codes and "S*" not in actual_codes:
                return "VERIFY_ROUTE_MISSING_DEFAULT"
            if (expected_codes & {"D", "O", "R", "S"}) - actual_codes:
                return "VERIFY_ROUTE_PROTOCOL_ABSENT"
            if expected_destinations - actual_destinations:
                return "VERIFY_ROUTE_MISSING_LEARNED"
            return "VERIFY_ROUTE_PROTOCOL_ABSENT"

    if feature.startswith("verification.show_access_lists.acls."):
        if status == "missing":
            return "VERIFY_ACL_MISSING"
        if actual in (None, [], {}):
            return "VERIFY_ACL_EMPTY"
        return "VERIFY_ACL_RULE_WRONG"

    if feature.startswith("verification.show_ip_nat_translations"):
        return "VERIFY_NAT_NOT_WORKING"

    if feature == "verification.show_ip_nat_statistics":
        return "VERIFY_NAT_NO_STATS"
    if feature.startswith("verification.show_ip_nat_statistics."):
        if ".inside_interfaces" in feature or ".outside_interfaces" in feature:
            return "VERIFY_NAT_IFACE_WRONG"
        if ".mappings" in feature:
            return "VERIFY_NAT_MAPPING_WRONG"
        if ".pools" in feature:
            return "VERIFY_NAT_POOL_WRONG"
        return "VERIFY_NAT_NO_STATS"

    if feature.startswith("verification.show_ip_dhcp_binding"):
        return "VERIFY_DHCP_NOT_ASSIGNING"

    if feature.startswith("verification.show_ip_dhcp_pool"):
        if feature.endswith(".pool_names"):
            expected_names = (
                set(expected or []) if isinstance(expected, list) else set()
            )
            actual_names = set(actual or []) if isinstance(actual, list) else set()
            if expected_names - actual_names:
                return "VERIFY_DHCP_POOL_MISSING"
            if actual_names - expected_names:
                return "VERIFY_DHCP_POOL_EXTRA"
            return "VERIFY_DHCP_POOL_MISSING"
        return "VERIFY_DHCP_POOL_MISSING"

    if feature.startswith("verification.show_vlan_brief"):
        if ".vlans.vlan" in feature:
            return "VERIFY_VLAN_SCHEME_MISMATCH"
        if ".vlans." in feature:
            if status == "missing":
                return "VERIFY_VLAN_MISSING"
            if status == "extra":
                return "VERIFY_VLAN_EXTRA"
            if feature.endswith(".name"):
                return "VERIFY_VLAN_NAME_WRONG"
            if feature.endswith(".status"):
                return "VERIFY_VLAN_NOT_ACTIVE"
            if feature.endswith(".ports"):
                return "VERIFY_VLAN_PORT_WRONG"
            return "VERIFY_VLAN_SCHEME_MISMATCH"

    if feature.startswith("verification.show_interfaces_trunk.trunks."):
        if status in {"missing", "extra"} or feature.endswith(".status"):
            return "VERIFY_TRUNK_NOT_TRUNKING"
        if feature.endswith(".encapsulation"):
            return "VERIFY_TRUNK_ENCAP_WRONG"
        if feature.endswith(".native_vlan"):
            return "VERIFY_TRUNK_NATIVE_WRONG"
        if feature.endswith(".mode"):
            return "VERIFY_TRUNK_MODE_WRONG"

    if feature.startswith("verification.show_port_security.interfaces."):
        if status == "missing":
            return "VERIFY_PORT_SECURITY_MISSING_IFACE"
        if feature.endswith(".max_secure_addr"):
            return "VERIFY_PORT_SECURITY_MAX_WRONG"
        if feature.endswith(".security_action"):
            return "VERIFY_PORT_SECURITY_ACTION_WRONG"
        if feature.endswith(".security_violation_count"):
            return "VERIFY_PORT_SECURITY_VIOLATION"

    return None


def _normalize_interface_name(name):
    """Expand common Cisco interface abbreviations for reliable matching."""
    text = str(name or "").strip()
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


def _normalized_interface_set(values):
    """Normalize a list of interface names or dicts into a comparable set."""
    interfaces = set()
    for value in values or []:
        if isinstance(value, dict):
            name = value.get("name") or value.get("interface")
        else:
            name = value
        normalized = _normalize_interface_name(name)
        if normalized:
            interfaces.add(normalized)
    return interfaces


def _make_verification_chain_result(
    feature,
    outcome_code,
    expected,
    actual,
    command,
    level,
    protocol,
    chain_stopped=False,
    details=None,
):
    """Create a structured multi-step verification failure result."""
    result = _make_result(feature, "mismatch", expected, actual, outcome_code)
    result["command"] = command
    result["level"] = level
    result["protocol"] = protocol
    result["chain_stopped"] = bool(chain_stopped)
    result["details"] = details or []
    return result


def _filter_route_table(routes, code_prefixes):
    """Filter route-table entries by route-code prefixes."""
    filtered = []
    prefixes = tuple(code_prefixes)
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        code = str(route.get("code", "")).strip().upper()
        if code.startswith(prefixes):
            filtered.append(route)
    return filtered


def _extract_route_destinations(routes):
    """Extract destination values from route-table entries."""
    destinations = set()
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        destination = str(route.get("destination", "")).strip()
        if destination and destination != "-":
            destinations.add(destination)
    return destinations


def _route_table_failure(protocol, expected_routes, actual_routes, expected_codes):
    """Classify why a verification route table does not contain expected routes."""
    actual_codes = _route_codes(actual_routes)
    expected_destinations = _extract_route_destinations(expected_routes)
    actual_destinations = _extract_route_destinations(actual_routes)
    missing_code_prefix = [
        code
        for code in expected_codes
        if not any(c.startswith(code.rstrip("*")) for c in actual_codes)
    ]
    if missing_code_prefix:
        return "VERIFY_ROUTE_PROTOCOL_ABSENT", {
            "expected_codes": sorted(expected_codes),
            "actual_codes": sorted(actual_codes),
        }
    missing_destinations = sorted(expected_destinations - actual_destinations)
    if missing_destinations:
        return "VERIFY_ROUTE_MISSING_LEARNED", {
            "expected_destinations": sorted(expected_destinations),
            "actual_destinations": sorted(actual_destinations),
        }
    return None, None


def _find_default_static_route(routes):
    """Return the default static route from parsed show-run routes, if present."""
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        destination = str(
            route.get("network") or route.get("destination") or ""
        ).strip()
        mask = str(route.get("mask", "")).strip()
        if destination in {"0.0.0.0", "0.0.0.0/0"} and mask in {"0.0.0.0", "", "/0"}:
            return route
        if destination == "0.0.0.0/0":
            return route
    return None


def _check_routing_verification(template, student):
    """Run chained routing verification checks across neighbor/topology/route data."""
    results = []
    template_show_run = template.get("show_running_config", {}) or {}
    student_show_run = student.get("show_running_config", {}) or {}
    template_routing = template_show_run.get("routing", {}) or {}
    student_routing = student_show_run.get("routing", {}) or {}
    template_verification = template.get("verification", {}) or {}
    student_verification = student.get("verification", {}) or {}

    def _eigrp_result():
        t_neighbors = (template_verification.get("show_ip_eigrp_neighbor") or {}).get(
            "neighbors"
        ) or []
        s_neighbors = (student_verification.get("show_ip_eigrp_neighbor") or {}).get(
            "neighbors"
        ) or []
        if t_neighbors:
            if not s_neighbors:
                return _make_verification_chain_result(
                    "verification.show_ip_eigrp_neighbor.level1",
                    "VERIFY_EIGRP_NO_NEIGHBORS",
                    f"at least {len(t_neighbors)} EIGRP neighbors",
                    "0 neighbors",
                    "show_ip_eigrp_neighbor",
                    1,
                    "eigrp",
                    chain_stopped=True,
                )
            if len(s_neighbors) < len(t_neighbors):
                return _make_verification_chain_result(
                    "verification.show_ip_eigrp_neighbor.level1",
                    "VERIFY_EIGRP_NEIGHBOR_COUNT",
                    f"{len(t_neighbors)} EIGRP neighbors",
                    f"{len(s_neighbors)} neighbors",
                    "show_ip_eigrp_neighbor",
                    1,
                    "eigrp",
                    chain_stopped=True,
                )
            t_ifaces = _normalized_interface_set(t_neighbors)
            s_ifaces = _normalized_interface_set(s_neighbors)
            if not t_ifaces.issubset(s_ifaces):
                return _make_verification_chain_result(
                    "verification.show_ip_eigrp_neighbor.level1",
                    "VERIFY_EIGRP_WRONG_INTERFACE",
                    sorted(t_ifaces),
                    sorted(s_ifaces),
                    "show_ip_eigrp_neighbor",
                    1,
                    "eigrp",
                    chain_stopped=True,
                )

        t_topology = (template_verification.get("show_ip_eigrp_topology") or {}).get(
            "routes"
        ) or []
        s_topology = (student_verification.get("show_ip_eigrp_topology") or {}).get(
            "routes"
        ) or []
        if t_topology:
            t_destinations = _extract_route_destinations(t_topology)
            s_destinations = _extract_route_destinations(s_topology)
            missing = sorted(t_destinations - s_destinations)
            if missing:
                return _make_verification_chain_result(
                    "verification.show_ip_eigrp_topology.level2",
                    "VERIFY_EIGRP_ROUTE_MISSING",
                    sorted(t_destinations),
                    sorted(s_destinations),
                    "show_ip_eigrp_topology",
                    2,
                    "eigrp",
                    chain_stopped=True,
                    details=missing,
                )
            s_topology_by_dest = {
                str(route.get("destination", ""))
                .strip(): str(route.get("state", ""))
                .strip()
                .lower()
                for route in s_topology
                if isinstance(route, dict)
            }
            for route in t_topology:
                destination = str(route.get("destination", "")).strip()
                if not destination:
                    continue
                if s_topology_by_dest.get(destination) == "active":
                    return _make_verification_chain_result(
                        "verification.show_ip_eigrp_topology.level2",
                        "VERIFY_EIGRP_ROUTE_ACTIVE",
                        "Passive EIGRP topology state",
                        f"{destination} is Active",
                        "show_ip_eigrp_topology",
                        2,
                        "eigrp",
                        chain_stopped=True,
                    )

        t_eigrp_interfaces = (
            template_verification.get("show_ip_eigrp_interfaces") or {}
        ).get("interfaces") or []
        s_eigrp_interfaces = (
            student_verification.get("show_ip_eigrp_interfaces") or {}
        ).get("interfaces") or []
        if t_eigrp_interfaces:
            t_ifaces = _normalized_interface_set(t_eigrp_interfaces)
            s_ifaces = _normalized_interface_set(s_eigrp_interfaces)
            missing = sorted(t_ifaces - s_ifaces)
            if missing:
                return _make_verification_chain_result(
                    "verification.show_ip_eigrp_interfaces.level2",
                    "VERIFY_EIGRP_IFACE_MISSING",
                    sorted(t_ifaces),
                    sorted(s_ifaces),
                    "show_ip_eigrp_interfaces",
                    2,
                    "eigrp",
                    chain_stopped=True,
                    details=missing,
                )

        passive = set()
        for process in template_routing.get("eigrp", []) or []:
            passive.update(
                _normalized_interface_set(process.get("passive_interfaces") or [])
            )
        if passive and s_eigrp_interfaces:
            s_ifaces = _normalized_interface_set(s_eigrp_interfaces)
            active_passive = sorted(passive & s_ifaces)
            if active_passive:
                return _make_verification_chain_result(
                    "verification.show_ip_eigrp_interfaces.level2",
                    "VERIFY_EIGRP_PASSIVE_ACTIVE",
                    f"passive interfaces absent from EIGRP: {sorted(passive)}",
                    f"present in EIGRP: {active_passive}",
                    "show_ip_eigrp_interfaces",
                    2,
                    "eigrp",
                    chain_stopped=True,
                )

        t_route = template_verification.get("show_ip_route") or {}
        s_route = student_verification.get("show_ip_route") or {}
        t_routes = _filter_route_table(t_route.get("routes") or [], {"D"})
        s_routes = _filter_route_table(s_route.get("routes") or [], {"D"})
        if t_routes:
            code, details = _route_table_failure("eigrp", t_routes, s_routes, {"D"})
            if code:
                return _make_verification_chain_result(
                    "verification.show_ip_route.eigrp.level3",
                    code,
                    details.get("expected_destinations")
                    or details.get("expected_codes"),
                    details.get("actual_destinations") or details.get("actual_codes"),
                    "show_ip_route",
                    3,
                    "eigrp",
                )
        return None

    def _ospf_result():
        t_neighbors = (template_verification.get("show_ip_ospf_neighbor") or {}).get(
            "neighbors"
        ) or []
        s_neighbors = (student_verification.get("show_ip_ospf_neighbor") or {}).get(
            "neighbors"
        ) or []
        if t_neighbors:
            if not s_neighbors:
                return _make_verification_chain_result(
                    "verification.show_ip_ospf_neighbor.level1",
                    "VERIFY_OSPF_NO_NEIGHBORS",
                    f"at least {len(t_neighbors)} OSPF neighbors",
                    "0 neighbors",
                    "show_ip_ospf_neighbor",
                    1,
                    "ospf",
                    chain_stopped=True,
                )
            if len(s_neighbors) < len(t_neighbors):
                return _make_verification_chain_result(
                    "verification.show_ip_ospf_neighbor.level1",
                    "VERIFY_OSPF_NEIGHBOR_COUNT",
                    f"{len(t_neighbors)} OSPF neighbors",
                    f"{len(s_neighbors)} neighbors",
                    "show_ip_ospf_neighbor",
                    1,
                    "ospf",
                    chain_stopped=True,
                )
            for neighbor in s_neighbors:
                state = str(neighbor.get("state", "")).upper()
                if state and not state.startswith("FULL"):
                    return _make_verification_chain_result(
                        "verification.show_ip_ospf_neighbor.level1",
                        "VERIFY_OSPF_NOT_FULL",
                        "FULL OSPF adjacency",
                        state,
                        "show_ip_ospf_neighbor",
                        1,
                        "ospf",
                        chain_stopped=True,
                    )
            t_ifaces = _normalized_interface_set(t_neighbors)
            s_ifaces = _normalized_interface_set(s_neighbors)
            if not t_ifaces.issubset(s_ifaces):
                return _make_verification_chain_result(
                    "verification.show_ip_ospf_neighbor.level1",
                    "VERIFY_OSPF_WRONG_INTERFACE",
                    sorted(t_ifaces),
                    sorted(s_ifaces),
                    "show_ip_ospf_neighbor",
                    1,
                    "ospf",
                    chain_stopped=True,
                )

        t_db = (template_verification.get("show_ip_ospf_database") or {}).get(
            "lsa_types"
        ) or {}
        s_db = (student_verification.get("show_ip_ospf_database") or {}).get(
            "lsa_types"
        ) or {}
        if t_db.get("router", 0) > 0 and s_db.get("router", 0) == 0:
            return _make_verification_chain_result(
                "verification.show_ip_ospf_database.level2",
                "VERIFY_OSPF_NO_ROUTER_LSA",
                "router LSAs present",
                "0 router LSAs",
                "show_ip_ospf_database",
                2,
                "ospf",
                chain_stopped=True,
            )
        if t_db.get("summary_net", 0) > 0 and s_db.get("summary_net", 0) == 0:
            return _make_verification_chain_result(
                "verification.show_ip_ospf_database.level2",
                "VERIFY_OSPF_NO_SUMMARY_LSA",
                "summary LSAs present",
                "0 summary LSAs",
                "show_ip_ospf_database",
                2,
                "ospf",
                chain_stopped=True,
            )
        if t_db.get("as_external", 0) > 0 and s_db.get("as_external", 0) == 0:
            return _make_verification_chain_result(
                "verification.show_ip_ospf_database.level2",
                "VERIFY_OSPF_NO_EXTERNAL_LSA",
                "external LSAs present",
                "0 external LSAs",
                "show_ip_ospf_database",
                2,
                "ospf",
                chain_stopped=True,
            )

        t_interfaces = (template_verification.get("show_ip_ospf_interface") or {}).get(
            "interfaces"
        ) or []
        s_interfaces = (student_verification.get("show_ip_ospf_interface") or {}).get(
            "interfaces"
        ) or []
        if t_interfaces:
            s_by_name = {
                _normalize_interface_name(interface.get("name")): interface
                for interface in s_interfaces
                if isinstance(interface, dict)
            }
            for interface in t_interfaces:
                name = _normalize_interface_name(interface.get("name"))
                if not name:
                    continue
                student_interface = s_by_name.get(name)
                if not student_interface:
                    return _make_verification_chain_result(
                        "verification.show_ip_ospf_interface.level2",
                        "VERIFY_OSPF_IFACE_DOWN",
                        f"{name} present in OSPF interface table",
                        "missing",
                        "show_ip_ospf_interface",
                        2,
                        "ospf",
                        chain_stopped=True,
                    )
                if (
                    str(interface.get("area", "")).strip()
                    != str(student_interface.get("area", "")).strip()
                ):
                    return _make_verification_chain_result(
                        f"verification.show_ip_ospf_interface.level2.{name}.area",
                        "VERIFY_OSPF_WRONG_AREA",
                        interface.get("area"),
                        student_interface.get("area"),
                        "show_ip_ospf_interface",
                        2,
                        "ospf",
                        chain_stopped=True,
                    )
                t_network_type = str(interface.get("network_type", "")).strip().upper()
                s_network_type = (
                    str(student_interface.get("network_type", "")).strip().upper()
                )
                if (
                    t_network_type == "POINT_TO_POINT"
                    and s_network_type != "POINT_TO_POINT"
                ):
                    return _make_verification_chain_result(
                        f"verification.show_ip_ospf_interface.level2.{name}.network_type",
                        "VERIFY_OSPF_WRONG_NETWORK_TYPE",
                        t_network_type,
                        s_network_type,
                        "show_ip_ospf_interface",
                        2,
                        "ospf",
                        chain_stopped=True,
                    )
                if str(student_interface.get("state", "")).strip().upper() == "DOWN":
                    return _make_verification_chain_result(
                        f"verification.show_ip_ospf_interface.level2.{name}.state",
                        "VERIFY_OSPF_IFACE_DOWN",
                        "not DOWN",
                        "DOWN",
                        "show_ip_ospf_interface",
                        2,
                        "ospf",
                        chain_stopped=True,
                    )

        t_route = template_verification.get("show_ip_route") or {}
        s_route = student_verification.get("show_ip_route") or {}
        t_routes = _filter_route_table(t_route.get("routes") or [], {"O"})
        s_routes = _filter_route_table(s_route.get("routes") or [], {"O"})
        if t_routes:
            code, details = _route_table_failure("ospf", t_routes, s_routes, {"O"})
            if code:
                return _make_verification_chain_result(
                    "verification.show_ip_route.ospf.level3",
                    code,
                    details.get("expected_destinations")
                    or details.get("expected_codes"),
                    details.get("actual_destinations") or details.get("actual_codes"),
                    "show_ip_route",
                    3,
                    "ospf",
                )
        return None

    def _rip_result():
        t_db = (template_verification.get("show_ip_rip_database") or {}).get(
            "routes"
        ) or []
        s_db = (student_verification.get("show_ip_rip_database") or {}).get(
            "routes"
        ) or []
        if t_db:
            if not s_db:
                return _make_verification_chain_result(
                    "verification.show_ip_rip_database.level2",
                    "VERIFY_RIP_DATABASE_EMPTY",
                    "RIP database entries",
                    "0 routes",
                    "show_ip_rip_database",
                    2,
                    "rip",
                    chain_stopped=True,
                )
            t_dest = _extract_route_destinations(t_db)
            s_dest = _extract_route_destinations(s_db)
            missing = sorted(t_dest - s_dest)
            if missing:
                return _make_verification_chain_result(
                    "verification.show_ip_rip_database.level2",
                    "VERIFY_RIP_ROUTE_MISSING",
                    sorted(t_dest),
                    sorted(s_dest),
                    "show_ip_rip_database",
                    2,
                    "rip",
                    chain_stopped=True,
                    details=missing,
                )
            for route in s_db:
                if int(route.get("metric", 0) or 0) >= 16:
                    return _make_verification_chain_result(
                        "verification.show_ip_rip_database.level2",
                        "VERIFY_RIP_ROUTE_UNREACHABLE",
                        "metric < 16",
                        route,
                        "show_ip_rip_database",
                        2,
                        "rip",
                        chain_stopped=True,
                    )
                if route.get("possibly_down"):
                    return _make_verification_chain_result(
                        "verification.show_ip_rip_database.level2",
                        "VERIFY_RIP_ROUTE_POSSIBLY_DOWN",
                        "possibly_down = false",
                        route,
                        "show_ip_rip_database",
                        2,
                        "rip",
                        chain_stopped=True,
                    )

        t_route = template_verification.get("show_ip_route") or {}
        s_route = student_verification.get("show_ip_route") or {}
        t_routes = _filter_route_table(t_route.get("routes") or [], {"R"})
        s_routes = _filter_route_table(s_route.get("routes") or [], {"R"})
        if t_routes:
            code, details = _route_table_failure("rip", t_routes, s_routes, {"R"})
            if code:
                return _make_verification_chain_result(
                    "verification.show_ip_route.rip.level3",
                    code,
                    details.get("expected_destinations")
                    or details.get("expected_codes"),
                    details.get("actual_destinations") or details.get("actual_codes"),
                    "show_ip_route",
                    3,
                    "rip",
                )
        return None

    def _static_result():
        t_static = template_routing.get("static_routes") or []
        if not t_static:
            return None
        s_route_static = (student_verification.get("show_ip_route_static") or {}).get(
            "routes"
        ) or []
        if not s_route_static:
            show_ip_route = student_verification.get("show_ip_route") or {}
            s_route_static = _filter_route_table(
                show_ip_route.get("routes") or [], {"S"}
            )

        t_default = _find_default_static_route(t_static)
        if t_default:
            default_present = any(
                str(route.get("destination", "")).strip() == "0.0.0.0/0"
                or (
                    str(route.get("destination", "")).strip() == "0.0.0.0"
                    and str(route.get("mask", "")).strip() == "0.0.0.0"
                )
                for route in s_route_static
            )
            if not default_present:
                return _make_verification_chain_result(
                    "verification.show_ip_route.static.level3.default",
                    "VERIFY_DEFAULT_ROUTE_MISSING",
                    "default route installed",
                    "default route absent",
                    "show_ip_route_static",
                    3,
                    "static",
                )

        expected_static = set()
        expected_static_networks = set()
        for route in t_static:
            if not isinstance(route, dict):
                continue
            network = str(route.get("network", "")).strip()
            mask = str(route.get("mask", "")).strip()
            if network and mask and not (network == "0.0.0.0" and mask == "0.0.0.0"):
                expected_static_networks.add(network)
                try:
                    expected_static.add(
                        str(ipaddress.IPv4Network(f"{network}/{mask}", strict=False))
                    )
                except Exception:
                    expected_static.add(f"{network}/{mask}")

        actual_static = set()
        actual_static_networks = set()
        for route in s_route_static:
            if not isinstance(route, dict):
                continue
            destination = str(route.get("destination", "")).strip()
            mask = str(route.get("mask", "")).strip()
            if destination:
                if "/" in destination:
                    actual_static_networks.add(destination.split("/", 1)[0])
                else:
                    actual_static_networks.add(destination)
                if "/" in destination:
                    actual_static.add(destination)
                elif mask:
                    try:
                        actual_static.add(
                            str(
                                ipaddress.IPv4Network(
                                    f"{destination}/{mask}", strict=False
                                )
                            )
                        )
                    except Exception:
                        actual_static.add(f"{destination}/{mask}")

        missing_static = sorted(
            cidr
            for cidr in expected_static
            if cidr not in actual_static
            and cidr.split("/", 1)[0] not in actual_static_networks
        )
        if missing_static:
            return _make_verification_chain_result(
                "verification.show_ip_route.static.level3.routes",
                "VERIFY_STATIC_ROUTE_MISSING",
                sorted(expected_static),
                sorted(actual_static or actual_static_networks),
                "show_ip_route_static",
                3,
                "static",
                details=missing_static,
            )
        return None

    t_protocols = {
        proto for proto in ("eigrp", "ospf", "rip") if template_routing.get(proto)
    }

    if "eigrp" in t_protocols:
        result = _eigrp_result()
        if result:
            results.append(result)
    if "ospf" in t_protocols:
        result = _ospf_result()
        if result:
            results.append(result)
    if "rip" in t_protocols:
        result = _rip_result()
        if result:
            results.append(result)
    static_result = _static_result()
    if static_result:
        results.append(static_result)

    t_gateway = (template_verification.get("show_ip_route") or {}).get(
        "gateway", {}
    ) or {}
    s_gateway = (student_verification.get("show_ip_route") or {}).get(
        "gateway", {}
    ) or {}
    t_next_hop = str(t_gateway.get("next_hop", "")).strip()
    s_next_hop = str(s_gateway.get("next_hop", "")).strip()
    if t_next_hop and s_next_hop and t_next_hop != s_next_hop:
        results.append(
            _make_verification_chain_result(
                "verification.show_ip_route.gateway.level3",
                "VERIFY_GATEWAY_WRONG",
                t_next_hop,
                s_next_hop,
                "show_ip_route",
                3,
                None,
            )
        )

    return results


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

    def _is_hw_absent_interface(iface_name, iface_cfg):
        if not isinstance(iface_name, str):
            return False
        # Do not filter out NVI0 or Loopbacks
        upper_name = iface_name.upper()
        if upper_name.startswith("NVI") or upper_name.startswith("LOOPBACK"):
            return False

        if not isinstance(iface_cfg, dict):
            return False

        # Check if it's from show_running_config (has 'shutdown' key)
        if "shutdown" in iface_cfg:
            if iface_cfg.get("shutdown") is not True:
                return False
            if iface_cfg.get("ip") is not None or iface_cfg.get("mask") is not None:
                return False
            return True

        # Check if it's from show_ip_interface_brief (has 'status' key)
        if "status" in iface_cfg:
            status = str(iface_cfg.get("status", "")).strip().lower()
            ip = str(iface_cfg.get("ip", "")).strip().lower()
            if status == "administratively down" and ip in {"unassigned", "none", ""}:
                return True

        return False

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
        if re.search(
            r"^verification\.(show_ip_eigrp|show_ip_ospf|show_ip_rip_database|show_ip_route_static|show_ip_route)(\.|$)",
            path,
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

    def _dhcp_pool_match_key(name):
        text = re.sub(r"[^a-z0-9]+", "", str(name or "").strip().lower())
        if text.endswith("s"):
            text = text[:-1]
        return text

    if parent_key.endswith("dhcp_pools") and isinstance(template, dict) and isinstance(student, dict):
        t_by_norm = {_dhcp_pool_match_key(name): name for name in template.keys()}
        s_by_norm = {_dhcp_pool_match_key(name): name for name in student.keys()}
        common_pools = sorted(t_by_norm.keys() & s_by_norm.keys())

        for norm_name in common_pools:
            t_name = t_by_norm[norm_name]
            s_name = s_by_norm[norm_name]
            pool_key = f"{parent_key}.{t_name}"
            t_pool = template.get(t_name)
            s_pool = student.get(s_name)
            if isinstance(t_pool, dict) and isinstance(s_pool, dict):
                results.extend(compare_dicts(t_pool, s_pool, pool_key))
            elif t_pool == s_pool:
                results.append(_make_result(pool_key, "correct"))
            else:
                results.append(
                    _make_result(
                        pool_key,
                        "mismatch",
                        t_pool,
                        s_pool,
                        "MISMATCH_DHCP_POOL",
                    )
                )

        for norm_name in sorted(t_by_norm.keys() - s_by_norm.keys()):
            t_name = t_by_norm[norm_name]
            t_pool = template.get(t_name)
            if _iface_has_config(t_pool):
                missing_key = f"{parent_key}.{t_name}"
                results.append(
                    _make_result(
                        missing_key,
                        "missing",
                        t_pool,
                        VALUE_NOT_PRESENT,
                        "MISSING_DHCP_POOL",
                    )
                )

        for norm_name in sorted(s_by_norm.keys() - t_by_norm.keys()):
            s_name = s_by_norm[norm_name]
            s_pool = student.get(s_name)
            if _iface_has_config(s_pool):
                extra_key = f"{parent_key}.{s_name}"
                results.append(
                    _make_result(
                        extra_key,
                        "extra",
                        None,
                        s_pool,
                        "EXTRA_DHCP_POOL",
                    )
                )
        return results

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
        results.extend(
            _check_nat_pool_ranges(
                t_nat.get("pools", []),
                s_nat.get("pools", []),
            )
        )

        # Configured-but-shutdown detection
        s_interfaces = student_show_run.get("interfaces", {}) or {}
        results.extend(_check_configured_but_shutdown(s_interfaces))

        # ACL not applied detection
        t_acls = template_show_run.get("access_lists", {}) or {}
        s_acls = student_show_run.get("access_lists", {}) or {}
        results.extend(_check_acl_not_applied(s_acls, s_interfaces, s_nat, t_acls))

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
                results.append(
                    {
                        "feature": f"show_running_config.access_lists.{acl_name}.rules.{rule_idx}",
                        "expected": "Effective ACL rule",
                        "actual": f"Rule shadowed: {rules[rule_idx]}",
                        "status": "mismatch",
                        "outcome_code": code,
                    }
                )

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

        # Routing verification chain (neighbors -> protocol DB/interfaces -> route table)
        results.extend(_check_routing_verification(template, student))

    for key, t_val in template.items():
        full_key = f"{parent_key}.{key}" if parent_key else key

        # Ignore volatile NAT statistics values that naturally drift over time.
        if _is_nat_stats_path(full_key) and key in NAT_STATS_VOLATILE_KEYS:
            continue
        if _should_skip_path(full_key):
            continue

        has_student_key = key in student
        s_val = student.get(key)

        if full_key.endswith("verification.show_ip_nat_translations") and isinstance(
            t_val, dict
        ):
            if t_val.get("tested") is False:
                continue
        if full_key.endswith("verification.show_ip_dhcp_binding") and isinstance(
            t_val, dict
        ):
            if t_val.get("has_assignments") is False:
                continue
        if full_key.endswith("verification.show_ip_dhcp_pool") and isinstance(
            t_val, dict
        ):
            if t_val.get("tested") is False:
                continue

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
                    results.append(
                        _make_result(
                            full_key,
                            "mismatch",
                            t_val,
                            VALUE_NOT_PRESENT,
                            _verification_outcome_code_for_path(
                                full_key, "mismatch", t_val, None
                            ),
                        )
                    )
            else:
                if t_val == s_val:
                    results.append(_make_result(full_key, "correct"))
                else:
                    results.append(
                        _make_result(
                            full_key,
                            "mismatch",
                            t_val,
                            s_val,
                            _verification_outcome_code_for_path(
                                full_key, "mismatch", t_val, s_val
                            ),
                        )
                    )
            continue

        if not has_student_key:
            # For password_type fields (console/vty), emit human-readable labels
            # instead of the raw Cisco type code ("0" = clear-text, "7" = encrypted).
            if full_key.endswith(".password_type") and (
                ".console." in full_key or ".vty." in full_key
            ):
                _pw_type_labels = {"0": "clear-text", "5": "MD5 secret", "7": "encrypted", "9": "scrypt"}
                _t_label = _pw_type_labels.get(str(t_val), f"type {t_val}")
                results.append(
                    _make_result(
                        full_key,
                        "missing",
                        f"password configured ({_t_label})",
                        "no password configured",
                    )
                )
            else:
                results.append(
                    _make_result(
                        full_key,
                        "missing",
                        t_val,
                        VALUE_NOT_PRESENT,
                        _verification_outcome_code_for_path(
                            full_key, "missing", t_val, None
                        ),
                    )
                )
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
            results.append(
                _make_result(
                    full_key,
                    "extra",
                    None,
                    s_val,
                    _verification_outcome_code_for_path(full_key, "extra", None, s_val),
                )
            )
        elif t_val is not None and s_val is None:
            results.append(
                _make_result(
                    full_key,
                    "missing",
                    t_val,
                    None,
                    _verification_outcome_code_for_path(
                        full_key, "missing", t_val, None
                    ),
                )
            )
        elif isinstance(t_val, dict):
            if full_key.endswith("dhcp_pools") and isinstance(s_val, dict):
                t_by_norm = {_dhcp_pool_match_key(name): name for name in t_val.keys()}
                s_by_norm = {_dhcp_pool_match_key(name): name for name in s_val.keys()}
                common_pools = sorted(t_by_norm.keys() & s_by_norm.keys())

                for norm_name in common_pools:
                    t_name = t_by_norm[norm_name]
                    s_name = s_by_norm[norm_name]
                    pool_key = f"{full_key}.{t_name}"
                    t_pool = t_val.get(t_name)
                    s_pool = s_val.get(s_name)
                    if isinstance(t_pool, dict) and isinstance(s_pool, dict):
                        results.extend(compare_dicts(t_pool, s_pool, pool_key))
                    elif t_pool == s_pool:
                        results.append(_make_result(pool_key, "correct"))
                    else:
                        results.append(
                            _make_result(
                                pool_key,
                                "mismatch",
                                t_pool,
                                s_pool,
                                "MISMATCH_DHCP_POOL",
                            )
                        )

                for norm_name in sorted(t_by_norm.keys() - s_by_norm.keys()):
                    t_name = t_by_norm[norm_name]
                    t_pool = t_val.get(t_name)
                    if _iface_has_config(t_pool):
                        missing_key = f"{full_key}.{t_name}"
                        results.append(
                            _make_result(
                                missing_key,
                                "missing",
                                t_pool,
                                VALUE_NOT_PRESENT,
                                "MISSING_DHCP_POOL",
                            )
                        )

                for norm_name in sorted(s_by_norm.keys() - t_by_norm.keys()):
                    s_name = s_by_norm[norm_name]
                    s_pool = s_val.get(s_name)
                    if _iface_has_config(s_pool):
                        extra_key = f"{full_key}.{s_name}"
                        results.append(
                            _make_result(
                                extra_key,
                                "extra",
                                None,
                                s_pool,
                                "EXTRA_DHCP_POOL",
                            )
                        )
                continue

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
                        results.append(
                            _make_result(
                                iface_key,
                                "mismatch",
                                t_iface,
                                s_iface,
                                _verification_outcome_code_for_path(
                                    iface_key, "mismatch", t_iface, s_iface
                                ),
                            )
                        )
                # Detect configured interfaces missing on either side.
                missing_ifaces = sorted(t_ifaces - s_ifaces)
                extra_ifaces = sorted(s_ifaces - t_ifaces)

                missing_items = []
                extra_items = []
                for iface in missing_ifaces:
                    t_iface = t_val.get(iface)
                    if _iface_has_config(t_iface):
                        if _is_hw_absent_interface(iface, t_iface):
                            continue
                        missing_items.append((iface, t_iface))
                for iface in extra_ifaces:
                    s_iface = s_val.get(iface)
                    if _iface_has_config(s_iface):
                        if _is_hw_absent_interface(iface, s_iface):
                            continue
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
                for base in sorted(
                    set(missing_by_base.keys()) & set(extra_by_base.keys())
                ):
                    m_list = sorted(missing_by_base[base], key=lambda x: x[0])
                    e_list = sorted(extra_by_base[base], key=lambda x: x[0])
                    for (m_iface, m_cfg), (e_iface, e_cfg) in zip(m_list, e_list):
                        results.append(
                            _make_result(
                                f"{full_key}.{base}.subinterface",
                                "mismatch",
                                {"name": m_iface, "config": m_cfg},
                                {"name": e_iface, "config": e_cfg},
                                "MISMATCH_SUB_INT_VLAN",
                            )
                        )
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
                        results.append(
                            _make_result(
                                f"{full_key}.Vlan.interface",
                                "mismatch",
                                {"name": m_iface, "config": m_cfg},
                                {"name": e_iface, "config": e_cfg},
                                "MISMATCH_VLAN_INTERFACE",
                            )
                        )
                        merged_missing.add(m_iface)
                        merged_extra.add(e_iface)

                for iface, t_iface in missing_items:
                    if iface in merged_missing:
                        continue
                    missing_key = f"{full_key}.{iface}"
                    results.append(
                        _make_result(
                            missing_key,
                            "missing",
                            t_iface,
                            None,
                            _verification_outcome_code_for_path(
                                missing_key, "missing", t_iface, None
                            ),
                        )
                    )
                for iface, s_iface in extra_items:
                    if iface in merged_extra:
                        continue
                    extra_key = f"{full_key}.{iface}"
                    results.append(
                        _make_result(
                            extra_key,
                            "extra",
                            None,
                            s_iface,
                            _verification_outcome_code_for_path(
                                extra_key, "extra", None, s_iface
                            ),
                        )
                    )
                continue
            if full_key.endswith("show_running_config.routing") and isinstance(
                s_val, dict
            ):
                t_static_routes = t_val.get("static_routes") or []
                s_static_routes = s_val.get("static_routes") or []
                results.extend(
                    _compare_static_route_lists(
                        t_static_routes,
                        s_static_routes,
                        template.get("interfaces") or {},
                        student.get("interfaces") or {},
                        f"{full_key}.static_routes",
                    )
                )
                t_val = dict(t_val)
                s_val = dict(s_val)
                t_val.pop("static_routes", None)
                s_val.pop("static_routes", None)

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
                    results.append(
                        _make_result(
                            f"{full_key}.protocol",
                            "mismatch",
                            sorted(t_protocols),
                            sorted(s_protocols),
                            "MISMATCH_ROUTING_PROTOCOL",
                        )
                    )
                    continue
                if t_protocols and not s_protocols:
                    results.append(
                        _make_result(
                            f"{full_key}.protocol",
                            "missing",
                            sorted(t_protocols),
                            [],
                            "MISSING_ROUTING_PROTOCOL",
                        )
                    )
                    continue
                if s_protocols and not t_protocols:
                    results.append(
                        _make_result(
                            f"{full_key}.protocol",
                            "extra",
                            [],
                            sorted(s_protocols),
                            "EXTRA_ROUTING_PROTOCOL",
                        )
                    )
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
                        results.append(
                            _make_result(
                                vlan_key,
                                "mismatch",
                                t_vlan,
                                s_vlan,
                                _verification_outcome_code_for_path(
                                    vlan_key, "mismatch", t_vlan, s_vlan
                                ),
                            )
                        )

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
                        results.append(
                            _make_result(
                                f"{full_key}.vlan",
                                "mismatch",
                                {"id": m_id, "config": m_cfg},
                                {"id": e_id, "config": e_cfg},
                                "MISMATCH_VLAN_ID",
                            )
                        )
                        merged_missing.add(m_id)
                        merged_extra.add(e_id)

                for vlan_id, t_vlan in missing_items:
                    if vlan_id in merged_missing:
                        continue
                    missing_key = f"{full_key}.{vlan_id}"
                    results.append(
                        _make_result(
                            missing_key,
                            "missing",
                            t_vlan,
                            None,
                            _verification_outcome_code_for_path(
                                missing_key, "missing", t_vlan, None
                            )
                            or "MISSING_VLAN",
                        )
                    )
                for vlan_id, s_vlan in extra_items:
                    if vlan_id in merged_extra:
                        continue
                    extra_key = f"{full_key}.{vlan_id}"
                    results.append(
                        _make_result(
                            extra_key,
                            "extra",
                            None,
                            s_vlan,
                            _verification_outcome_code_for_path(
                                extra_key, "extra", None, s_vlan
                            )
                            or "EXTRA_VLAN",
                        )
                    )
                continue
            results.extend(compare_dicts(t_val, s_val, full_key))
        elif isinstance(t_val, list):
            if not isinstance(s_val, list):
                results.append(
                    _make_result(
                        full_key,
                        "mismatch",
                        t_val,
                        s_val,
                        _verification_outcome_code_for_path(
                            full_key, "mismatch", t_val, s_val
                        ),
                    )
                )
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
                        results.append(
                            _make_result(
                                f"{full_key}.{uname}",
                                "missing",
                                udata,
                                None,
                                "MISSING_USER",
                            )
                        )
                    else:
                        if udata.get("privilege") != s_users[uname].get("privilege"):
                            results.append(
                                _make_result(
                                    f"{full_key}.{uname}.privilege",
                                    "mismatch",
                                    udata.get("privilege"),
                                    s_users[uname].get("privilege"),
                                    "MISMATCH_USER_PRIVILEGE",
                                )
                            )
                        if udata.get("auth_type") != s_users[uname].get("auth_type"):
                            results.append(
                                _make_result(
                                    f"{full_key}.{uname}.auth_type",
                                    "mismatch",
                                    udata.get("auth_type"),
                                    s_users[uname].get("auth_type"),
                                    "MISMATCH_USER_AUTH_TYPE",
                                )
                            )
                for uname in s_users:
                    if uname not in t_users:
                        results.append(
                            _make_result(
                                f"{full_key}.{uname}",
                                "extra",
                                None,
                                s_users[uname],
                                "EXTRA_USER",
                            )
                        )
                continue

            # Generic list comparison: order-insensitive multiset compare.
            t_counter = Counter(json.dumps(item, sort_keys=True) for item in t_val)
            s_counter = Counter(json.dumps(item, sort_keys=True) for item in s_val)
            if t_counter != s_counter:
                results.append(
                    _make_result(
                        full_key,
                        "mismatch",
                        t_val,
                        s_val,
                        _verification_outcome_code_for_path(
                            full_key, "mismatch", t_val, s_val
                        ),
                    )
                )
            else:
                results.append(_make_result(full_key, "correct"))
        else:
            if t_val != s_val:
                results.append(
                    _make_result(
                        full_key,
                        "mismatch",
                        t_val,
                        s_val,
                        _verification_outcome_code_for_path(
                            full_key, "mismatch", t_val, s_val
                        ),
                    )
                )
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
            results.append(
                _make_result(
                    full_key,
                    "extra",
                    None,
                    student[key],
                    _verification_outcome_code_for_path(
                        full_key, "extra", None, student[key]
                    ),
                )
            )

    return results
