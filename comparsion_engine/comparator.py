import re
import json
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


def compare_dicts(template: dict, student: dict, parent_key="") -> list:
    """
    Recursively compares two dictionaries and returns structured results.
    """
    results = []

    for key, t_val in template.items():
        full_key = f"{parent_key}.{key}" if parent_key else key

        # Ignore volatile NAT statistics values that naturally drift over time.
        if _is_nat_stats_path(full_key) and key in NAT_STATS_VOLATILE_KEYS:
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

        if not has_student_key:
            results.append(
                {
                    "feature": full_key,
                    "expected": t_val,
                    "actual": None,
                    "status": "missing",
                }
            )
        elif full_key.endswith("banner_motd"):
            # Banner content is not graded by exact string; only presence matters.
            if t_val is None and s_val is None:
                results.append({"feature": full_key, "status": "correct"})
            elif t_val is None and s_val is not None:
                results.append(
                    {
                        "feature": full_key,
                        "expected": None,
                        "actual": s_val,
                        "status": "extra",
                    }
                )
            elif t_val is not None and s_val is None:
                results.append(
                    {
                        "feature": full_key,
                        "expected": t_val,
                        "actual": None,
                        "status": "missing",
                    }
                )
            else:
                results.append({"feature": full_key, "status": "correct"})
        elif t_val is None and s_val is None:
            results.append({"feature": full_key, "status": "correct"})
        elif t_val is None and s_val is not None:
            results.append(
                {
                    "feature": full_key,
                    "expected": None,
                    "actual": s_val,
                    "status": "extra",
                }
            )
        elif t_val is not None and s_val is None:
            results.append(
                {
                    "feature": full_key,
                    "expected": t_val,
                    "actual": None,
                    "status": "missing",
                }
            )
        elif isinstance(t_val, dict):
            # Hardware models can expose different interface sets.
            # Compare only interfaces present on both sides.
            if full_key.endswith("interfaces") and isinstance(s_val, dict):
                common_interfaces = sorted(set(t_val.keys()) & set(s_val.keys()))
                for iface in common_interfaces:
                    iface_key = f"{full_key}.{iface}"
                    t_iface = t_val.get(iface)
                    s_iface = s_val.get(iface)
                    if isinstance(t_iface, dict) and isinstance(s_iface, dict):
                        results.extend(compare_dicts(t_iface, s_iface, iface_key))
                    elif t_iface == s_iface:
                        results.append({"feature": iface_key, "status": "correct"})
                    else:
                        results.append(
                            {
                                "feature": iface_key,
                                "expected": t_iface,
                                "actual": s_iface,
                                "status": "mismatch",
                            }
                        )
                continue
            results.extend(compare_dicts(t_val, s_val, full_key))
        elif isinstance(t_val, list):
            if not isinstance(s_val, list):
                results.append(
                    {
                        "feature": full_key,
                        "expected": t_val,
                        "actual": s_val,
                        "status": "mismatch",
                    }
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
                            {"feature": f"{full_key}.{uname}", "status": "missing"}
                        )
                    else:
                        if udata.get("privilege") != s_users[uname].get("privilege"):
                            results.append(
                                {
                                    "feature": f"{full_key}.{uname}.privilege",
                                    "expected": udata.get("privilege"),
                                    "actual": s_users[uname].get("privilege"),
                                    "status": "mismatch",
                                }
                            )
                for uname in s_users:
                    if uname not in t_users:
                        results.append(
                            {
                                "feature": f"{full_key}.{uname}",
                                "expected": None,
                                "actual": s_users[uname],
                                "status": "extra",
                            }
                        )
                continue

            # Generic list comparison: order-insensitive multiset compare.
            t_counter = Counter(json.dumps(item, sort_keys=True) for item in t_val)
            s_counter = Counter(json.dumps(item, sort_keys=True) for item in s_val)
            if t_counter != s_counter:
                results.append(
                    {
                        "feature": full_key,
                        "expected": t_val,
                        "actual": s_val,
                        "status": "mismatch",
                    }
                )
            else:
                results.append({"feature": full_key, "status": "correct"})
        else:
            if t_val != s_val:
                results.append(
                    {
                        "feature": full_key,
                        "expected": t_val,
                        "actual": s_val,
                        "status": "mismatch",
                    }
                )
            else:
                results.append({"feature": full_key, "status": "correct"})

    # Detect extra fields in student config
    for key in student.keys():
        full_key = f"{parent_key}.{key}" if parent_key else key
        if _is_nat_stats_path(full_key) and key in NAT_STATS_VOLATILE_KEYS:
            continue
        if key not in template:
            results.append(
                {
                    "feature": full_key,
                    "expected": None,
                    "actual": student[key],
                    "status": "extra",
                }
            )

    return results
