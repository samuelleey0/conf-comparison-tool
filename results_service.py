"""Result report building, error context, and raw log preview helpers.

Raw comparator JSON stays on disk; this service reloads it with the current
grading policy/rubric rules so Result page changes such as disabling a rule can
be reflected without re-running device comparison.
"""

import json
import os
import re
import traceback
from pathlib import Path

from generate_results import write_readable_result_from_report
from grading_dedup import empty_phase1_summary
from grading_rules import classify_items, evaluate_pass_fail, load_grading_policy, load_rubric_rules

def _safe_resolve_child(base: Path, target: Path) -> Path:
    base = base.resolve()
    target = target.resolve()
    if base == target or base in target.parents:
        return target
    return None


def _iter_session_students(target_path: str):
    if not target_path or not os.path.isdir(target_path):
        return []
    students = []
    for entry in sorted(os.listdir(target_path)):
        full = os.path.join(target_path, entry)
        if os.path.isdir(full):
            students.append({"student_id": entry, "path": full})
    return students


def _load_student_results(student_dir: Path, student_id: str):
    if not student_dir.is_dir():
        return None

    results_dir = student_dir / "results"
    if not results_dir.is_dir():
        return None

    current_hostnames = None
    summary_path = results_dir / "summary.json"
    if summary_path.exists():
        try:
            with open(summary_path, "r") as handle:
                summary_data = json.load(handle) or {}
            hostnames = summary_data.get("hostnames_compared") or []
            if isinstance(hostnames, list) and hostnames:
                current_hostnames = {str(hostname) for hostname in hostnames}
        except Exception:
            current_hostnames = None

    host_results = {}
    all_items = []
    for file_path in sorted(results_dir.glob("*_result.json")):
        try:
            with open(file_path, "r") as handle:
                data = json.load(handle) or {}
        except Exception:
            continue

        hostname = data.get("hostname") or file_path.stem.replace("_result", "")
        if current_hostnames is not None and hostname not in current_hostnames:
            continue
        results = data.get("results") or []
        host_results[hostname] = {
            "hostname": hostname,
            "grading_mode": data.get("grading_mode"),
            "template_name": data.get("template_name"),
            "student_show_run_file": data.get("student_show_run_file"),
            "student_parsed_file": data.get("student_parsed_file"),
            "results": results,
        }
        for item in results:
            item_copy = dict(item)
            item_copy["hostname"] = hostname
            all_items.append(item_copy)

    if not host_results:
        return None

    template_name = None
    grading_mode = None
    for host in host_results.values():
        if not template_name:
            template_name = host.get("template_name")
        if not grading_mode:
            grading_mode = host.get("grading_mode")

    return {
        "student_id": student_id,
        "template_name": template_name,
        "grading_mode": grading_mode,
        "hostnames": host_results,
        "items": all_items,
    }



def _build_session_reports(target_path: str):
    policy = load_grading_policy()
    rubric_rules = load_rubric_rules()
    reports = []
    for student in _iter_session_students(target_path):
        student_id = student.get("student_id")
        student_dir = Path(student.get("path") or "")
        report = _load_student_results(student_dir, student_id)
        if not report:
            reports.append(
                {
                    "student_id": student_id,
                    "status": "no_results",
                    "pass": False,
                    "summary": empty_phase1_summary(),
                    "hostnames": {},
                    "items": [],
                    "config_results": [],
                    "verification_results": [],
                }
            )
            continue

        items, summary, config_results, verification_results = classify_items(
            report["items"], policy, rubric_rules
        )
        passed = evaluate_pass_fail(summary, policy)
        report["items"] = items
        report["config_results"] = config_results
        report["verification_results"] = verification_results
        report["summary"] = summary
        report["pass"] = passed
        report["status"] = "graded"
        reports.append(report)

    return reports


def _write_session_readable_results(target_path: str, reports, policy):
    written = []
    for report in reports or []:
        if report.get("status") != "graded":
            continue
        student_id = report.get("student_id")
        if not student_id:
            continue
        student_dir = Path(target_path) / student_id
        try:
            output_path = write_readable_result_from_report(
                str(student_dir), report, policy
            )
            written.append(output_path)
        except Exception:
            traceback.print_exc()
    return written


_MISSING = object()


def _load_json_file(path: Path):
    try:
        with open(path, "r") as handle:
            return json.load(handle) or {}
    except Exception:
        return {}


def _resolve_json_parts(data, parts):
    """Walk into a nested dict using parts as keys.
    Handles keys that contain dots (e.g., 'GigabitEthernet0/0/1.20') by trying
    greedy multi-part matching before single-part matching.
    """

    def _walk(current, idx):
        if idx >= len(parts):
            return current
        if not isinstance(current, dict):
            return _MISSING
        # Try greedy: join parts[idx..j] and see if it's a key (longest first)
        for j in range(len(parts) - 1, idx, -1):
            candidate = ".".join(parts[idx : j + 1])
            if candidate in current:
                result = _walk(current[candidate], j + 1)
                if result is not _MISSING:
                    return result
        # Try single part
        key = parts[idx]
        if key in current:
            return _walk(current[key], idx + 1)
        return _MISSING

    return _walk(data, 0)


def _preferred_context_parts(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if not parts:
        return [], None

    if (
        len(parts) == 2
        and parts[0] == "show_running_config"
        and parts[1] in {"hostname", "banner_motd"}
    ):
        return parts, parts[1]

    if (
        len(parts) == 3
        and parts[0] == "show_running_config"
        and parts[1] == "switching"
        and parts[2] == "default_gateway"
    ):
        return parts, parts[2]

    if (
        len(parts) >= 4
        and parts[0] == "show_running_config"
        and parts[1] == "interfaces"
    ):
        # For subinterfaces like GigabitEthernet0/0/1.20.vlan,
        # parts = ["show_running_config", "interfaces", "GigabitEthernet0/0/1", "20", "vlan"]
        # The actual JSON key is "GigabitEthernet0/0/1.20", so we need to include
        # enough parts for _resolve_json_parts to reconstruct the dotted key.
        # Include up to the parent interface + subinterface number, leaving only
        # the terminal field name (e.g. "vlan") as the highlight_key.
        #
        # Heuristic: if parts[3] is numeric (subinterface id), include it in context.
        if len(parts) >= 5 and parts[3].isdigit():
            # e.g., parts[:4] = ["show_running_config", "interfaces", "GigabitEthernet0/0/1", "20"]
            return parts[:4], parts[4] if len(parts) > 4 else None
        # Check for "subinterface" as the field key (entire subinterface object mismatch)
        if parts[3] == "subinterface":
            return parts[:4], None
        return parts[:3], parts[3] if len(parts) > 3 else None

    if (
        len(parts) >= 5
        and parts[0] == "verification"
        and parts[1] == "show_ip_interface_brief"
        and parts[2] == "interfaces"
    ):
        return parts[:4], parts[4] if len(parts) > 4 else None

    if (
        len(parts) >= 5
        and parts[0] == "verification"
        and parts[1] == "show_vlan_brief"
        and parts[2] == "vlans"
    ):
        return parts[:4], parts[4] if len(parts) > 4 else None

    if len(parts) >= 2:
        return parts[:-1], parts[-1]

    return parts, None


def _extract_error_context(
    template_config, student_config, feature: str, expected=None, actual=None
):
    parts, highlight_key = _preferred_context_parts(feature)
    if not parts:
        return {
            "context_path": "",
            "highlight_key": None,
            "template_context": None,
            "student_context": None,
        }

    # Special handling for subinterface mismatches:
    # For features like .GigabitEthernet0/0/1.subinterface, show only the paired
    # expected/actual subinterfaces from the comparison result rather than every
    # sibling subinterface under the same parent.
    feature_parts = [p for p in str(feature or "").split(".") if p]
    if (
        len(feature_parts) >= 4
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "interfaces"
        and feature_parts[3] == "subinterface"
    ):
        parent_iface = feature_parts[2]
        t_ifaces = _resolve_json_parts(
            template_config, ["show_running_config", "interfaces"]
        )
        s_ifaces = _resolve_json_parts(
            student_config, ["show_running_config", "interfaces"]
        )
        if t_ifaces is not _MISSING or s_ifaces is not _MISSING:
            expected_name = expected.get("name") if isinstance(expected, dict) else None
            actual_name = actual.get("name") if isinstance(actual, dict) else None
            t_subs = None
            s_subs = None
            if isinstance(t_ifaces, dict):
                if expected_name and expected_name in t_ifaces:
                    t_subs = {expected_name: t_ifaces.get(expected_name)}
                else:
                    t_subs = {
                        k: v
                        for k, v in t_ifaces.items()
                        if k.startswith(f"{parent_iface}.")
                    }
            if isinstance(s_ifaces, dict):
                if actual_name and actual_name in s_ifaces:
                    s_subs = {actual_name: s_ifaces.get(actual_name)}
                else:
                    s_subs = {
                        k: v
                        for k, v in s_ifaces.items()
                        if k.startswith(f"{parent_iface}.")
                    }
            return {
                "context_path": f"show_running_config.interfaces.{parent_iface}.*",
                "highlight_key": "subinterface",
                "template_context": t_subs if t_subs else None,
                "student_context": s_subs if s_subs else None,
            }

    # Special handling for VLAN SVI mismatches:
    # For features like .Vlan.interface, show only the paired expected/actual
    # SVI entries from the comparison result rather than the entire interfaces map.
    if (
        len(feature_parts) >= 4
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "interfaces"
        and feature_parts[2] == "Vlan"
        and feature_parts[3] == "interface"
    ):
        t_ifaces = _resolve_json_parts(template_config, ["show_running_config", "interfaces"])
        s_ifaces = _resolve_json_parts(student_config, ["show_running_config", "interfaces"])
        expected_name = expected.get("name") if isinstance(expected, dict) else None
        actual_name = actual.get("name") if isinstance(actual, dict) else None
        template_vlan = _MISSING
        student_vlan = _MISSING
        if isinstance(t_ifaces, dict) and expected_name:
            template_vlan = t_ifaces.get(expected_name, _MISSING)
        if isinstance(s_ifaces, dict) and actual_name:
            student_vlan = s_ifaces.get(actual_name, _MISSING)
        if template_vlan is not _MISSING or student_vlan is not _MISSING:
            return {
                "context_path": "show_running_config.interfaces.Vlan.interface",
                "highlight_key": "interface",
                "template_context": None if template_vlan is _MISSING else {expected_name: template_vlan},
                "student_context": None if student_vlan is _MISSING else {actual_name: student_vlan},
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "interfaces"
        and len(feature_parts) == 3
    ):
        iface_name = feature_parts[2]
        t_ifaces = _resolve_json_parts(template_config, ["show_running_config", "interfaces"])
        s_ifaces = _resolve_json_parts(student_config, ["show_running_config", "interfaces"])
        template_iface = t_ifaces.get(iface_name, _MISSING) if isinstance(t_ifaces, dict) else _MISSING
        student_iface = s_ifaces.get(iface_name, _MISSING) if isinstance(s_ifaces, dict) else _MISSING
        if template_iface is not _MISSING or student_iface is not _MISSING:
            return {
                "context_path": f"show_running_config.interfaces.{iface_name}",
                "highlight_key": iface_name,
                "template_context": None if template_iface is _MISSING else {iface_name: template_iface},
                "student_context": None if student_iface is _MISSING else {iface_name: student_iface},
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "users"
    ):
        username = feature_parts[2]
        t_users = _resolve_json_parts(template_config, ["show_running_config", "users"])
        s_users = _resolve_json_parts(student_config, ["show_running_config", "users"])

        def _find_user(users_list, wanted):
            if not isinstance(users_list, list):
                return _MISSING
            for entry in users_list:
                if (
                    isinstance(entry, dict)
                    and str(entry.get("username") or "").lower() == wanted.lower()
                ):
                    return entry
            return _MISSING

        template_user = _find_user(t_users, username)
        student_user = _find_user(s_users, username)
        if template_user is not _MISSING or student_user is not _MISSING:
            return {
                "context_path": f"show_running_config.users.{username}",
                "highlight_key": username,
                "template_context": (
                    None if template_user is _MISSING else template_user
                ),
                "student_context": None if student_user is _MISSING else student_user,
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "access_lists"
    ):
        acl_name = feature_parts[2]
        t_acls = _resolve_json_parts(
            template_config, ["show_running_config", "access_lists"]
        )
        s_acls = _resolve_json_parts(
            student_config, ["show_running_config", "access_lists"]
        )

        template_acl = (
            t_acls.get(acl_name, _MISSING) if isinstance(t_acls, dict) else _MISSING
        )
        student_acl = (
            s_acls.get(acl_name, _MISSING) if isinstance(s_acls, dict) else _MISSING
        )

        if template_acl is not _MISSING or student_acl is not _MISSING:
            return {
                "context_path": f"show_running_config.access_lists.{acl_name}",
                "highlight_key": acl_name,
                "template_context": None if template_acl is _MISSING else template_acl,
                "student_context": None if student_acl is _MISSING else student_acl,
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "nat"
    ):
        nat_field = feature_parts[2]
        t_nat = _resolve_json_parts(template_config, ["show_running_config", "nat"])
        s_nat = _resolve_json_parts(student_config, ["show_running_config", "nat"])

        template_nat = (
            t_nat.get(nat_field, _MISSING) if isinstance(t_nat, dict) else _MISSING
        )
        student_nat = (
            s_nat.get(nat_field, _MISSING) if isinstance(s_nat, dict) else _MISSING
        )

        if template_nat is not _MISSING or student_nat is not _MISSING:
            return {
                "context_path": f"show_running_config.nat.{nat_field}",
                "highlight_key": nat_field,
                "template_context": None if template_nat is _MISSING else template_nat,
                "student_context": None if student_nat is _MISSING else student_nat,
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "routing"
        and feature_parts[2] == "protocol"
    ):
        t_routing = _resolve_json_parts(
            template_config, ["show_running_config", "routing"]
        )
        s_routing = _resolve_json_parts(
            student_config, ["show_running_config", "routing"]
        )

        expected_protocols = expected if isinstance(expected, list) else []
        actual_protocols = actual if isinstance(actual, list) else []

        def _protocol_subset(routing_obj, protocols):
            if not isinstance(routing_obj, dict):
                return _MISSING
            subset = {}
            for proto in protocols:
                value = routing_obj.get(proto, _MISSING)
                if value is not _MISSING:
                    subset[proto] = value
            return subset if subset else _MISSING

        template_subset = _protocol_subset(t_routing, expected_protocols)
        student_subset = _protocol_subset(s_routing, actual_protocols)

        if template_subset is not _MISSING or student_subset is not _MISSING:
            return {
                "context_path": "show_running_config.routing.protocol",
                "highlight_key": "protocol",
                "template_context": (
                    None if template_subset is _MISSING else template_subset
                ),
                "student_context": (
                    None if student_subset is _MISSING else student_subset
                ),
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "routing"
        and feature_parts[2] in {"rip", "eigrp", "ospf", "static_routes"}
    ):
        routing_key = feature_parts[2]
        t_routing = _resolve_json_parts(template_config, ["show_running_config", "routing"])
        s_routing = _resolve_json_parts(student_config, ["show_running_config", "routing"])
        template_routing = t_routing.get(routing_key, _MISSING) if isinstance(t_routing, dict) else _MISSING
        student_routing = s_routing.get(routing_key, _MISSING) if isinstance(s_routing, dict) else _MISSING
        if template_routing is not _MISSING or student_routing is not _MISSING:
            return {
                "context_path": f"show_running_config.routing.{routing_key}",
                "highlight_key": routing_key,
                "template_context": None if template_routing is _MISSING else template_routing,
                "student_context": None if student_routing is _MISSING else student_routing,
            }

    if (
        len(feature_parts) >= 4
        and feature_parts[0] == "verification"
        and feature_parts[1] in {"show_ip_interface_brief", "show_port_security", "show_interfaces_trunk"}
        and feature_parts[2] in {"interfaces", "trunks"}
    ):
        command = feature_parts[1]
        collection_key = feature_parts[2]
        iface_name = feature_parts[3]
        t_verify = _resolve_json_parts(template_config, ["verification", command, collection_key])
        s_verify = _resolve_json_parts(student_config, ["verification", command, collection_key])
        template_iface = t_verify.get(iface_name, _MISSING) if isinstance(t_verify, dict) else _MISSING
        student_iface = s_verify.get(iface_name, _MISSING) if isinstance(s_verify, dict) else _MISSING
        if template_iface is not _MISSING or student_iface is not _MISSING:
            return {
                "context_path": f"verification.{command}.{collection_key}.{iface_name}",
                "highlight_key": iface_name,
                "template_context": None if template_iface is _MISSING else {iface_name: template_iface},
                "student_context": None if student_iface is _MISSING else {iface_name: student_iface},
            }

    while parts:
        template_context = _resolve_json_parts(template_config, parts)
        student_context = _resolve_json_parts(student_config, parts)
        if template_context is not _MISSING or student_context is not _MISSING:
            return {
                "context_path": ".".join(parts),
                "highlight_key": highlight_key,
                "template_context": (
                    None if template_context is _MISSING else template_context
                ),
                "student_context": (
                    None if student_context is _MISSING else student_context
                ),
            }
        parts = parts[:-1]

    return {
        "context_path": "",
        "highlight_key": None,
        "template_context": None,
        "student_context": None,
    }


def _normalize_text(value):
    lowered = str(value or "").lower()
    for char in ["_", "-", ".", "(", ")", "[", "]"]:
        lowered = lowered.replace(char, " ")
    return " ".join(lowered.split())


def _canonical_cli_command(command):
    text = str(command or "").strip()
    if _normalize_text(text) == "show running config":
        return "show running-config"
    return text


def _command_hint_for_feature(feature: str):
    if feature.startswith("show_running_config."):
        return "show running-config"
    mapping = {
        "verification.show_ip_interface_brief.": "show ip interface brief",
        "verification.show_ip_route.": "show ip route",
        "verification.show_ip_eigrp_neighbor.": "show ip eigrp neighbor",
        "verification.show_ip_eigrp_topology.": "show ip eigrp topology",
        "verification.show_ip_eigrp_interfaces.": "show ip eigrp interfaces",
        "verification.show_ip_ospf_neighbor.": "show ip ospf neighbor",
        "verification.show_ip_ospf_database.": "show ip ospf database",
        "verification.show_ip_ospf_interface.": "show ip ospf interface",
        "verification.show_ip_rip_database.": "show ip rip database",
        "verification.show_ip_route_static.": "show ip route static",
        "verification.show_vlan_brief.": "show vlan brief",
        "verification.show_access_lists.": "show access-lists",
        "verification.show_interfaces_trunk.": "show interfaces trunk",
        "verification.show_port_security.": "show port-security",
        "verification.show_etherchannel_summary.": "show etherchannel summary",
        "verification.show_ip_nat_statistics.": "show ip nat statistics",
        "verification.show_ip_nat_translations.": "show ip nat translations",
        "verification.show_ip_dhcp_binding.": "show ip dhcp binding",
        "verification.show_ip_dhcp_pool.": "show ip dhcp pool",
    }
    for prefix, hint in mapping.items():
        if feature.startswith(prefix):
            return hint
    return None


def _command_aliases(command_hint: str):
    normalized = _normalize_text(command_hint)
    aliases = {normalized}

    explicit_aliases = {
        "show running config": {
            "show running config",
            "show run",
            "sh run",
            "show running-config",
            "show running_config",
            "show_run",
            "sh_run",
        },
        "show ip interface brief": {
            "show ip interface brief",
            "show ip int brief",
            "sh ip interface brief",
            "sh ip int brief",
            "show interface brief",
            "show int brief",
            "sh int brief",
        },
        "show ip route": {
            "show ip route",
            "sh ip route",
        },
        "show vlan brief": {
            "show vlan brief",
            "sh vlan brief",
        },
        "show access lists": {
            "show access lists",
            "show access-lists",
            "show access_lists",
            "sh access lists",
            "sh access-lists",
            "sh access_lists",
        },
        "show interfaces trunk": {
            "show interfaces trunk",
            "show interface trunk",
            "show int trunk",
            "sh interfaces trunk",
            "sh interface trunk",
            "sh int trunk",
        },
        "show port security": {
            "show port security",
            "show port-security",
            "show port_security",
            "sh port security",
            "sh port-security",
            "sh port_security",
        },
        "show etherchannel summary": {
            "show etherchannel summary",
            "sh etherchannel summary",
        },
        "show ip nat statistics": {
            "show ip nat statistics",
            "sh ip nat statistics",
            "show ip nat stats",
            "sh ip nat stats",
        },
        "show ip nat translations": {
            "show ip nat translations",
            "sh ip nat translations",
        },
        "show ip dhcp binding": {
            "show ip dhcp binding",
            "sh ip dhcp binding",
        },
        "show ip dhcp pool": {
            "show ip dhcp pool",
            "sh ip dhcp pool",
        },
    }

    for alias in explicit_aliases.get(normalized, set()):
        aliases.add(_normalize_text(alias))

    return {alias for alias in aliases if alias}


def _find_log_file(log_dir: Path, command_hint: str):
    if not log_dir or not log_dir.is_dir() or not command_hint:
        return None
    desired_aliases = _command_aliases(command_hint)
    candidates = []
    for entry in sorted(log_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.lower() in {"config.json", "logs.json", "summary.json"}:
            continue
        score = 0
        normalized = _normalize_text(entry.name)
        if normalized in desired_aliases:
            score = 0
        elif any(
            alias in normalized or normalized in alias for alias in desired_aliases
        ):
            score = 1
        else:
            continue
        candidates.append((score, len(entry.name), entry))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2].name))
    return candidates[0][2]


def _log_command_label(file_path: Path):
    stem = file_path.stem if file_path else ""
    return " ".join(stem.replace("_", " ").replace("-", " ").split()) or "raw log"


def _iter_raw_log_files(log_dir: Path):
    if not log_dir or not log_dir.is_dir():
        return []
    files = []
    for entry in sorted(log_dir.iterdir(), key=lambda item: item.name.lower()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.name.lower() in {"config.json", "logs.json", "summary.json"}:
            continue
        files.append(entry)
    return files


def _read_raw_log_file(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[Unable to read {path.name}: {exc}]"


def _raw_log_map(log_dir: Path):
    mapped = {}
    for file_path in _iter_raw_log_files(log_dir):
        label = _log_command_label(file_path)
        key = _normalize_text(label)
        mapped[key] = {
            "command": label,
            "path": str(file_path),
            "content": _read_raw_log_file(file_path),
        }
    return mapped


def _render_combined_raw_logs(items, side_label):
    if not items:
        return f"({side_label} raw logs not found)"
    sections = []
    for item in items:
        path = item.get("path") or "path not found"
        command = item.get("command") or "raw log"
        content = item.get("content") or ""
        sections.append(f"===== {command} =====\n{path}\n\n{content}".rstrip())
    return "\n\n".join(sections)


def _read_text_lines(path: Path):
    if not path or not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def _find_line_index(lines, matcher):
    for index, line in enumerate(lines):
        try:
            if matcher(line):
                return index
        except Exception:
            continue
    return None


def _extract_cli_block(lines, start_index):
    if start_index is None or start_index < 0 or start_index >= len(lines):
        return None
    block = [lines[start_index]]
    for idx in range(start_index + 1, len(lines)):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            block.append(line)
            continue
        if stripped == "!":
            block.append(line)
            break
        if not line.startswith(" "):
            break
        block.append(line)
    return "\n".join(block).strip()


def _extract_matching_lines(lines, matcher, max_matches=12):
    matched = []
    for line in lines:
        try:
            if matcher(line):
                matched.append(line)
                if len(matched) >= max_matches:
                    break
        except Exception:
            continue
    return "\n".join(matched).strip() if matched else None


def _excerpt_around(lines, index, before=2, after=2):
    if index is None or index < 0 or index >= len(lines):
        return None
    start = max(0, index - before)
    end = min(len(lines), index + after + 1)
    return "\n".join(lines[start:end]).strip()


def _interface_aliases(interface_name: str):
    text = str(interface_name or "").strip()
    if not text:
        return []
    aliases = {text}
    replacements = {
        "GigabitEthernet": "Gi",
        "FastEthernet": "Fa",
        "Serial": "Se",
        "Loopback": "Lo",
        "Vlan": "Vl",
        "Port-channel": "Po",
        "Tunnel": "Tu",
    }
    for long_name, short_name in replacements.items():
        if text.startswith(long_name):
            aliases.add(short_name + text[len(long_name) :])
    return sorted(aliases, key=len, reverse=True)


def _extract_running_config_excerpt(lines, feature: str, expected=None, actual=None):
    parts = [part for part in str(feature or "").split(".") if part]
    if not parts:
        return None

    def _extract_router_protocol_block(proto_name):
        proto = str(proto_name or "").lower()
        if proto == "eigrp":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router eigrp ")
            )
            return _extract_cli_block(lines, idx)
        if proto == "ospf":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router ospf ")
            )
            return _extract_cli_block(lines, idx)
        if proto == "rip":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower() == "router rip"
            )
            return _extract_cli_block(lines, idx)
        return None

    if len(parts) >= 4 and parts[1] == "interfaces":
        iface = parts[2]
        # For subinterface features, try finding the subinterface block first
        # e.g., feature = show_running_config.interfaces.GigabitEthernet0/0/1.20.vlan
        # parts = ["show_running_config", "interfaces", "GigabitEthernet0/0/1", "20", "vlan"]
        if len(parts) >= 5 and parts[3].isdigit():
            sub_iface = f"{iface}.{parts[3]}"  # e.g., GigabitEthernet0/0/1.20
            idx = _find_line_index(
                lines,
                lambda line, si=sub_iface: line.strip().lower()
                == f"interface {si}".lower(),
            )
            block = _extract_cli_block(lines, idx)
            if block:
                return block
        # For "subinterface" field, try to find any subinterface of this parent
        if len(parts) >= 4 and parts[3] == "subinterface":
            expected_name = expected.get("name") if isinstance(expected, dict) else None
            actual_name = actual.get("name") if isinstance(actual, dict) else None
            pair_names = [name for name in [expected_name, actual_name] if name]
            sub_blocks = []
            if pair_names:
                for sub_name in pair_names:
                    idx = _find_line_index(
                        lines,
                        lambda line, si=sub_name: line.strip().lower()
                        == f"interface {si}".lower(),
                    )
                    block = _extract_cli_block(lines, idx)
                    if block:
                        sub_blocks.append(block)
            else:
                # Fallback: show all subinterface blocks for this parent
                for i, line in enumerate(lines):
                    stripped = line.strip().lower()
                    if stripped.startswith(f"interface {iface.lower()}."):
                        block = _extract_cli_block(lines, i)
                        if block:
                            sub_blocks.append(block)
            if sub_blocks:
                return "\n\n".join(sub_blocks)
        # For "Vlan.interface" mismatch, show only the paired VLAN SVI blocks
        if len(parts) >= 4 and iface == "Vlan" and parts[3] == "interface":
            expected_name = expected.get("name") if isinstance(expected, dict) else None
            actual_name = actual.get("name") if isinstance(actual, dict) else None
            svi_blocks = []
            for svi_name in [expected_name, actual_name]:
                if not svi_name:
                    continue
                idx = _find_line_index(
                    lines,
                    lambda line, si=svi_name: line.strip().lower() == f"interface {si}".lower(),
                )
                block = _extract_cli_block(lines, idx)
                if block:
                    svi_blocks.append(block)
            if svi_blocks:
                return "\n\n".join(svi_blocks)
        # Fallback: parent interface
        idx = _find_line_index(
            lines, lambda line: line.strip().lower() == f"interface {iface}".lower()
        )
        block = _extract_cli_block(lines, idx)
        if block:
            return block

    if len(parts) >= 3 and parts[1] == "interfaces":
        iface = parts[2]
        idx = _find_line_index(lines, lambda line: line.strip().lower() == f"interface {iface}".lower())
        block = _extract_cli_block(lines, idx)
        if block:
            return block
        return f"No interface {iface} found in running-config."

    if len(parts) >= 3 and parts[1] == "vty":
        idx = _find_line_index(
            lines, lambda line: line.strip().lower().startswith("line vty ")
        )
        block = _extract_cli_block(lines, idx)
        if block:
            if "password" in feature and "password" not in block.lower():
                block += "\n(no 'password' command visible in this capture)"
            return block

    if len(parts) >= 3 and parts[1] == "console":
        idx = _find_line_index(lines, lambda line: line.strip().lower() == "line con 0")
        block = _extract_cli_block(lines, idx)
        if block:
            # If this is a password-related feature but no 'password' line is in the block,
            # append a note so the user understands why both sides look the same.
            if "password" in feature and "password" not in block.lower():
                block += "\n(no 'password' command visible in this capture)"
            return block

    if len(parts) >= 3 and parts[1] == "routing":
        routing_key = parts[2].lower()
        if routing_key == "protocol":
            protocols = (
                expected
                if isinstance(expected, list) and expected
                else actual if isinstance(actual, list) else []
            )
            blocks = []
            for proto in protocols:
                block = _extract_router_protocol_block(proto)
                if block:
                    blocks.append(block)
            if blocks:
                return "\n\n".join(blocks)
            return "No matching routing protocol block found in running-config."
        if routing_key == "ospf":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router ospf ")
            )
            block = _extract_cli_block(lines, idx)
            if block:
                return block
        if routing_key == "eigrp":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router eigrp ")
            )
            block = _extract_cli_block(lines, idx)
            if block:
                return block
        if routing_key == "rip":
            idx = _find_line_index(lines, lambda line: line.strip().lower() == "router rip")
            block = _extract_cli_block(lines, idx)
            if block:
                return block
        if routing_key == "static_routes":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower().startswith("ip route ")
            )
            if block:
                return block

    if len(parts) >= 3 and parts[1] == "dhcp_pools":
        pool_name = parts[2]
        idx = _find_line_index(
            lines,
            lambda line: line.strip().lower() == f"ip dhcp pool {pool_name}".lower(),
        )
        block = _extract_cli_block(lines, idx)
        if block:
            return block

    if len(parts) >= 2 and parts[1] == "dhcp_excluded":
        block = _extract_matching_lines(
            lines,
            lambda line: line.strip().lower().startswith("ip dhcp excluded-address"),
        )
        if block:
            return block

    if len(parts) >= 3 and parts[1] == "access_lists":
        acl_name = parts[2]
        idx = _find_line_index(
            lines,
            lambda line: line.strip().lower().startswith(f"ip access-list ")
            and line.strip().split()[-1].lower() == acl_name.lower(),
        )
        block = _extract_cli_block(lines, idx)
        if block:
            return block
        block = _extract_matching_lines(
            lines,
            lambda line: line.strip()
            .lower()
            .startswith(f"access-list {acl_name.lower()} "),
        )
        if block:
            return block
        return f"No ACL named '{acl_name}' found in running-config."

    prefix_map = {
        "show_running_config.hostname": "hostname ",
        "show_running_config.banner_motd": "banner motd",
        "show_running_config.switching.default_gateway": "ip default-gateway ",
    }
    for prefix, needle in prefix_map.items():
        if feature.startswith(prefix):
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith(needle)
            )
            excerpt = _excerpt_around(lines, idx, before=0, after=0)
            if excerpt:
                return excerpt

    if feature.startswith("show_running_config.switching.spanning_tree"):
        block = _extract_matching_lines(
            lines, lambda line: line.strip().lower().startswith("spanning-tree ")
        )
        if block:
            return block

    if feature.startswith("show_running_config.http_server"):
        block = _extract_matching_lines(
            lines, lambda line: line.strip().lower().startswith("ip http ")
        )
        if block:
            return block
        field = feature.split(".")[-1] if "." in feature else "http_server"
        if field == "enabled":
            return "No 'ip http server' command found in running-config."
        if field == "secure_server":
            return "No 'ip http secure-server' command found in running-config."
        if field == "authentication":
            return "No 'ip http authentication' command found in running-config."
        return "No 'ip http' configuration found in running-config."

    if feature.startswith("show_running_config.users."):
        parts = feature.split(".")
        username = parts[2] if len(parts) > 2 else None
        block = _extract_matching_lines(
            lines,
            lambda line: line.strip().lower().startswith("username ")
            and (not username or line.strip().split()[1].lower() == username.lower()),
        )
        if block:
            return block
        if username:
            return f"No 'username {username}' command found in running-config."

    if feature.startswith("show_running_config.nat."):
        nat_field = parts[2] if len(parts) > 2 else ""
        if nat_field == "inside_interfaces":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower() == "ip nat inside"
            )
            if block:
                return block
            return "No 'ip nat inside' configuration found in running-config."
        if nat_field == "outside_interfaces":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower() == "ip nat outside"
            )
            if block:
                return block
            return "No 'ip nat outside' configuration found in running-config."
        if nat_field == "pools":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower().startswith("ip nat pool ")
            )
            if block:
                return block
            return "No 'ip nat pool' configuration found in running-config."
        if nat_field == "inside_source":
            block = _extract_matching_lines(
                lines,
                lambda line: line.strip().lower().startswith("ip nat inside source "),
            )
            if block:
                return block
            return "No 'ip nat inside source' configuration found in running-config."
        block = _extract_matching_lines(
            lines, lambda line: "ip nat" in line.strip().lower()
        )
        if block:
            return block

    return None


def _extract_show_ip_interface_brief_excerpt(lines, feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 5 or parts[2] != "interfaces":
        return None
    iface = parts[3]
    aliases = _interface_aliases(iface)
    idx = _find_line_index(
        lines,
        lambda line: any(
            re.search(rf"^{re.escape(alias)}(\s|$)", line.strip(), re.IGNORECASE)
            for alias in aliases
        ),
    )
    excerpt = _excerpt_around(lines, idx, before=2, after=2)
    if excerpt:
        return excerpt
    return f"No interface {iface} found in show ip interface brief."


def _extract_show_vlan_brief_excerpt(lines, feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 4 or parts[2] != "vlans":
        return None
    vlan_id = parts[3]
    idx = _find_line_index(
        lines, lambda line: re.search(rf"^{re.escape(vlan_id)}\s", line.strip())
    )
    return _excerpt_around(lines, idx, before=2, after=1)


def _extract_show_ip_route_excerpt(lines, feature: str, expected=None, actual=None):
    candidates = []
    for value in [expected, actual]:
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
        elif isinstance(value, dict):
            for key in ["destination", "network", "next_hop", "interface"]:
                val = value.get(key)
                if isinstance(val, str) and val.strip() and val.strip() != "-":
                    candidates.append(val.strip())
    for candidate in candidates:
        idx = _find_line_index(lines, lambda line: candidate.lower() in line.lower())
        excerpt = _excerpt_around(lines, idx, before=2, after=2)
        if excerpt:
            return excerpt
    return "\n".join(lines[:8]).strip() if lines else None


def _extract_show_access_lists_excerpt(lines, feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    acl_name = (
        parts[3] if len(parts) >= 4 and parts[2] in {"acls", "access_lists"} else None
    )
    if acl_name:
        idx = _find_line_index(
            lines,
            lambda line: re.search(rf"\b{re.escape(acl_name)}\b", line, re.IGNORECASE),
        )
        if idx is not None:
            start = idx
            while start > 0 and lines[start - 1].strip():
                start -= 1
            end = idx
            while end + 1 < len(lines) and lines[end + 1].strip():
                end += 1
            return "\n".join(lines[start : end + 1]).strip()
    return "\n".join(lines[:12]).strip() if lines else None


def _extract_raw_excerpt(log_path: Path, feature: str, expected=None, actual=None):
    lines = _read_text_lines(log_path)
    if not lines:
        return None

    if feature.startswith("show_running_config."):
        excerpt = _extract_running_config_excerpt(
            lines, feature, expected=expected, actual=actual
        )
        if excerpt:
            return excerpt
        if feature == "show_running_config.banner_motd":
            return "No 'banner motd' command found in running-config."
        if feature == "show_running_config.hostname":
            return "No 'hostname' command found in running-config."

    if feature.startswith("verification.show_ip_interface_brief."):
        excerpt = _extract_show_ip_interface_brief_excerpt(lines, feature)
        if excerpt:
            return excerpt

    if feature.startswith("verification.show_vlan_brief."):
        excerpt = _extract_show_vlan_brief_excerpt(lines, feature)
        if excerpt:
            return excerpt

    if feature.startswith("verification.show_ip_route."):
        excerpt = _extract_show_ip_route_excerpt(
            lines, feature, expected=expected, actual=actual
        )
        if excerpt:
            return excerpt

    if feature.startswith("verification.show_access_lists."):
        excerpt = _extract_show_access_lists_excerpt(lines, feature)
        if excerpt:
            return excerpt

    search_terms = []
    for part in str(feature or "").split("."):
        if "/" in part or part.lower().startswith(
            ("vlan", "fa", "gi", "se", "lo", "po")
        ):
            search_terms.append(part)
    for value in [expected, actual]:
        if isinstance(value, str) and value.strip() and len(value.strip()) < 80:
            search_terms.append(value.strip())
    for term in search_terms:
        idx = _find_line_index(lines, lambda line: str(term).lower() in line.lower())
        excerpt = _excerpt_around(lines, idx, before=2, after=2)
        if excerpt:
            return excerpt

    return "\n".join(lines[:12]).strip()
