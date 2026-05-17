"""Grading summary and Layer 1/Layer 2 deduplication helpers."""

import copy
import json
import re
from pathlib import Path


DEDUP_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "grading_dedup.json"

DEFAULT_DEDUP_CONFIG = {
    "interface_commands": [
        "show_ip_interface_brief",
        "show_interfaces_trunk",
        "show_port_security",
    ],
    "routing_command_map": {
        "show_ip_eigrp_neighbor": "show_running_config.routing.eigrp",
        "show_ip_eigrp_topology": "show_running_config.routing.eigrp",
        "show_ip_eigrp_interfaces": "show_running_config.routing.eigrp",
        "show_ip_ospf_neighbor": "show_running_config.routing.ospf",
        "show_ip_ospf_database": "show_running_config.routing.ospf",
        "show_ip_ospf_interface": "show_running_config.routing.ospf",
        "show_ip_rip_database": "show_running_config.routing.rip",
        "show_ip_route_static": "show_running_config.routing.static_routes",
    },
    "collection_parent_map": {
        "show_access_lists": {
            "collection_key": "acls",
            "parent_prefix": "show_running_config.access_lists",
        },
        "show_etherchannel_summary": {
            "collection_key": "groups",
            "parent_prefix": "show_running_config.switching.etherchannel.groups",
        },
    },
    "command_parent_map": {
        "show_ip_nat_statistics": "show_running_config.nat",
        "show_ip_nat_translations": "show_running_config.nat",
        "show_ip_dhcp_binding": "show_running_config.dhcp_pools",
        "show_ip_dhcp_pool": "show_running_config.dhcp_pools",
    },
    "show_ip_route": {
        "default_parent": "show_running_config.routing",
        "protocol_parent_map": {
            "eigrp": "show_running_config.routing.eigrp",
            "ospf": "show_running_config.routing.ospf",
            "rip": "show_running_config.routing.rip",
            "static": "show_running_config.routing.static_routes",
        },
        "dedup_feature_prefixes": [
            "show_running_config.routing",
            "show_running_config.switching.default_gateway",
        ],
    },
    "vlan_scheme": {
        "command": "show_vlan_brief",
        "parent": "show_running_config.__vlan_scheme__",
        "block_name": "show_vlan_brief.vlan_scheme",
        "dedup_feature_tokens": [
            ".access_vlan",
            ".switchport_mode",
            ".trunk_native_vlan",
            ".trunk_allowed_vlans",
            ".Vlan.interface",
            ".subinterface",
        ],
    },
    "dedup_options": {
        "dedup_routing_when_protocol_failed": True,
        "dedup_exact_or_nested_parent_match": True,
    },
}


def _deep_merge(defaults, override):
    if not isinstance(defaults, dict):
        return copy.deepcopy(override if override is not None else defaults)
    merged = copy.deepcopy(defaults)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _string_list(value):
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _string_map(value):
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for key, item in value.items():
        key_text = str(key or "").strip()
        item_text = str(item or "").strip()
        if key_text and item_text:
            cleaned[key_text] = item_text
    return cleaned


def _collection_parent_map(value):
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for command, data in value.items():
        command_text = str(command or "").strip()
        if not command_text or not isinstance(data, dict):
            continue
        collection_key = str(data.get("collection_key") or "").strip()
        parent_prefix = str(data.get("parent_prefix") or "").strip()
        if collection_key and parent_prefix:
            cleaned[command_text] = {
                "collection_key": collection_key,
                "parent_prefix": parent_prefix,
            }
    return cleaned


def normalize_dedup_config(data):
    data = data or {}
    config = _deep_merge(DEFAULT_DEDUP_CONFIG, data)
    # List/map sections are treated as editable replacements, not additive merges.
    for key in [
        "interface_commands",
        "routing_command_map",
        "collection_parent_map",
        "command_parent_map",
    ]:
        if key in data:
            config[key] = data.get(key)

    if isinstance(data.get("show_ip_route"), dict):
        route_override = data["show_ip_route"]
        for key in ["protocol_parent_map", "dedup_feature_prefixes"]:
            if key in route_override:
                config.setdefault("show_ip_route", {})[key] = route_override.get(key)

    if isinstance(data.get("vlan_scheme"), dict):
        vlan_override = data["vlan_scheme"]
        if "dedup_feature_tokens" in vlan_override:
            config.setdefault("vlan_scheme", {})["dedup_feature_tokens"] = (
                vlan_override.get("dedup_feature_tokens")
            )
    config["interface_commands"] = _string_list(config.get("interface_commands"))
    config["routing_command_map"] = _string_map(config.get("routing_command_map"))
    config["collection_parent_map"] = _collection_parent_map(
        config.get("collection_parent_map")
    )
    config["command_parent_map"] = _string_map(config.get("command_parent_map"))

    route = config.get("show_ip_route") if isinstance(config.get("show_ip_route"), dict) else {}
    config["show_ip_route"] = {
        "default_parent": str(route.get("default_parent") or "").strip()
        or DEFAULT_DEDUP_CONFIG["show_ip_route"]["default_parent"],
        "protocol_parent_map": _string_map(route.get("protocol_parent_map")),
        "dedup_feature_prefixes": _string_list(route.get("dedup_feature_prefixes")),
    }

    vlan = config.get("vlan_scheme") if isinstance(config.get("vlan_scheme"), dict) else {}
    config["vlan_scheme"] = {
        "command": str(vlan.get("command") or "").strip()
        or DEFAULT_DEDUP_CONFIG["vlan_scheme"]["command"],
        "parent": str(vlan.get("parent") or "").strip()
        or DEFAULT_DEDUP_CONFIG["vlan_scheme"]["parent"],
        "block_name": str(vlan.get("block_name") or "").strip()
        or DEFAULT_DEDUP_CONFIG["vlan_scheme"]["block_name"],
        "dedup_feature_tokens": _string_list(vlan.get("dedup_feature_tokens")),
    }

    options = config.get("dedup_options") if isinstance(config.get("dedup_options"), dict) else {}
    config["dedup_options"] = {
        "dedup_routing_when_protocol_failed": bool(
            options.get("dedup_routing_when_protocol_failed", True)
        ),
        "dedup_exact_or_nested_parent_match": bool(
            options.get("dedup_exact_or_nested_parent_match", True)
        ),
    }
    return config


def load_dedup_config(path=None):
    target = Path(path or DEDUP_CONFIG_PATH)
    if not target.exists():
        return normalize_dedup_config({})
    try:
        with target.open("r", encoding="utf-8") as handle:
            return normalize_dedup_config(json.load(handle))
    except Exception:
        return normalize_dedup_config({})


def save_dedup_config(data, path=None):
    config = normalize_dedup_config(data)
    target = Path(path or DEDUP_CONFIG_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    return config


def reset_dedup_config(path=None):
    target = Path(path or DEDUP_CONFIG_PATH)
    if target.exists():
        target.unlink()
    return normalize_dedup_config({})


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


def _verification_layer1_ref(feature: str, config=None):
    config = config or load_dedup_config()
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 2 or parts[0] != "verification":
        return None

    routing_command_map = config.get("routing_command_map") or {}
    if parts[1] in routing_command_map:
        return routing_command_map[parts[1]]

    if (
        len(parts) >= 4
        and parts[1] in set(config.get("interface_commands") or [])
        and parts[2] == "interfaces"
    ):
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    if len(parts) >= 4 and parts[1] in set(config.get("interface_commands") or []):
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    collection_parent_map = config.get("collection_parent_map") or {}
    collection_rule = collection_parent_map.get(parts[1])
    if (
        collection_rule
        and len(parts) >= 4
        and parts[2] == collection_rule.get("collection_key")
    ):
        return f"{collection_rule.get('parent_prefix')}.{parts[3]}"

    command_parent_map = config.get("command_parent_map") or {}
    if parts[1] in command_parent_map:
        return command_parent_map[parts[1]]

    if parts[1] == "show_ip_route":
        route_config = config.get("show_ip_route") or {}
        protocol_parent_map = route_config.get("protocol_parent_map") or {}
        if len(parts) >= 3 and parts[2] in protocol_parent_map:
            return protocol_parent_map[parts[2]]
        return route_config.get("default_parent") or "show_running_config.routing"

    vlan_config = config.get("vlan_scheme") or {}
    if parts[1] == vlan_config.get("command"):
        return vlan_config.get("parent")

    return None


def verification_block_name(feature: str):
    config = load_dedup_config()
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
    if command in set(config.get("interface_commands") or []) and len(parts) >= 4:
        return f"{command}.{parts[3]}"
    if command in (config.get("collection_parent_map") or {}) and len(parts) >= 4:
        return f"{command}.{parts[3]}"
    vlan_config = config.get("vlan_scheme") or {}
    if command == vlan_config.get("command"):
        return vlan_config.get("block_name") or command
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
    config = load_dedup_config()
    options = config.get("dedup_options") or {}
    layer1_ref = _verification_layer1_ref(feature, config)
    if layer1_ref and layer1_ref.startswith("show_running_config.routing."):
        if options.get("dedup_routing_when_protocol_failed", True):
            if any(
                failed_ref == "show_running_config.routing.protocol"
                for failed_ref in failed_config_refs
            ) or any(
                str(failed_feature).startswith("show_running_config.routing.protocol")
                for failed_feature in failed_config_features
            ):
                return True, layer1_ref
    vlan_parent = (config.get("vlan_scheme") or {}).get("parent")
    if (
        options.get("dedup_exact_or_nested_parent_match", True)
        and layer1_ref
        and layer1_ref != vlan_parent
    ):
        for failed_ref in failed_config_refs:
            if (
                failed_ref == layer1_ref
                or failed_ref.startswith(layer1_ref)
                or layer1_ref.startswith(failed_ref)
            ):
                return True, layer1_ref

    vlan_config = config.get("vlan_scheme") or {}
    vlan_command = vlan_config.get("command") or "show_vlan_brief"
    if str(feature or "").startswith(f"verification.{vlan_command}."):
        for failed_feature in failed_config_features:
            if any(
                token in failed_feature
                for token in vlan_config.get("dedup_feature_tokens", [])
            ):
                return True, vlan_config.get("parent")

    if str(feature or "").startswith("verification.show_ip_route."):
        route_config = config.get("show_ip_route") or {}
        for failed_feature in failed_config_features:
            if any(
                failed_feature.startswith(prefix)
                for prefix in route_config.get("dedup_feature_prefixes", [])
            ):
                return True, route_config.get("default_parent")

    return False, layer1_ref
