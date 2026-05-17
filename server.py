# server.py
from flask import Flask, jsonify, request, Response, stream_with_context
import threading
from queue import SimpleQueue
import os
import json
import traceback
from pathlib import Path
import time
import sys
import yaml
import re
import glob
import shutil
import logging
import string

# Reuse your helpers
from file_utils import save_output_to_file, del_partial_logs
from serial_utils import (
    connect_to_serial,
    READ_TIMEOUT,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    detect_hostname_with_prompt_retry,
    wait_serial_prompt_ready,
)
from remote_utils import (
    remote_connect,
    disable_paging_remote,
    enter_enable_mode_remote,
    send_command_remote,
    get_hostname_remote,
)
from command_manager import load_commands, save_commands
from comparison_engine.parser import parse_device_logs, normalize_parsed_config
from comparison_engine.comparator import compare_dicts
from comparison_engine.student_manager import find_show_run_file
from cisco_reset import reload_cisco_device
from generate_results import write_readable_result_from_report
from grading_dedup import (
    empty_phase1_summary,
    load_dedup_config,
    reset_dedup_config,
    save_dedup_config,
)
from grading_rules import (
    classify_items,
    evaluate_pass_fail,
    load_grading_policy,
    load_rubric_rules,
    reset_rubric_rules,
    save_grading_policy,
    save_rubric_rules,
)
from comparison_wrapper import import_logs_folder_strict, import_template_from_logs_dir, save_template_setup
from export_melbourne import export_to_melbourne

app = Flask(__name__)


@app.after_request
def add_local_app_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response

# Base directory for consistent absolute paths
BASE_DIR = Path(__file__).resolve().parent

# Grading Directories
SCHEMES_DIR = BASE_DIR / "schemes"
RUBRICS_DIR = BASE_DIR / "rubrics"
TEMPLATES_DIR = BASE_DIR / "comparison_engine" / "templates"
ENGINE_STUDENTS_DIR = BASE_DIR / "comparison_engine" / "students"
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
RESULTS_DIR = None
DOCS_DIR = (Path.home() / "Documents").resolve()
WINDOWS_DRIVES_ROOT = "__WINDOWS_DRIVES__"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
ENGINE_STUDENTS_DIR.mkdir(parents=True, exist_ok=True)

WINDOWS_INVALID_SEGMENT_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

connection_lock = threading.Lock()

current_mode = None  # "serial" or "ssh"
serial_conn = None
serial_hostname = None
last_used_serial_settings = {"port": "/dev/ttyUSB0", "baudrate": 9600}

ssh_client = None
ssh_hostname = None
last_used_ssh_credentials = {
    "host": None,
    "username": None,
    "password": None,
    "port": 22,
}

execution_abort = threading.Event()


def _is_windows_platform():
    return os.name == "nt"


def _normalize_directory_segment(value, field_label):
    segment = str(value or "").strip()
    if not segment:
        raise ValueError(f"Missing {field_label}.")
    if segment in {".", ".."}:
        raise ValueError(f"{field_label} cannot be '.' or '..'.")
    if "/" in segment or "\\" in segment:
        raise ValueError(f"{field_label} cannot contain path separators.")
    if "\x00" in segment:
        raise ValueError(f"{field_label} contains an invalid null character.")
    if not _is_windows_platform():
        return segment

    cleaned = "".join(
        "-" if ch in WINDOWS_INVALID_SEGMENT_CHARS else ch for ch in segment
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(" .")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        raise ValueError(
            f"{field_label} cannot be empty after Windows-safe cleanup."
        )

    reserved_name = cleaned.split(".")[0].upper()
    if reserved_name in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned


def _close_serial_connection():
    global serial_conn, serial_hostname
    ser = None
    with connection_lock:
        if serial_conn:
            ser = serial_conn
            serial_conn = None
            serial_hostname = None
    if ser:
        try:
            logout_close_connection(ser)
        except Exception:
            pass


def _close_ssh_connection():
    global ssh_client, ssh_hostname
    client = None
    with connection_lock:
        if ssh_client:
            client = ssh_client
            ssh_client = None
            ssh_hostname = None
    if client:
        try:
            shell = getattr(client, "_shell", None)
            if shell:
                shell.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def _is_ssh_client_active(client):
    if not client:
        return False
    try:
        transport = client.get_transport()
        return transport and transport.is_active()
    except Exception:
        return False


def _update_serial_state(ser, port, baudrate, hostname):
    global serial_conn, serial_hostname, current_mode
    with connection_lock:
        last_used_serial_settings["port"] = port
        last_used_serial_settings["baudrate"] = baudrate
        serial_conn = ser
        serial_hostname = hostname
        current_mode = "serial"


def _update_ssh_state(client, host, username, password, hostname, port):
    global ssh_client, ssh_hostname, current_mode
    with connection_lock:
        last_used_ssh_credentials["host"] = host
        last_used_ssh_credentials["username"] = username
        last_used_ssh_credentials["password"] = password
        last_used_ssh_credentials["port"] = port
        ssh_client = client
        ssh_hostname = hostname
        current_mode = "ssh"



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

    host_results = {}
    all_items = []
    for file_path in sorted(results_dir.glob("*_result.json")):
        try:
            with open(file_path, "r") as handle:
                data = json.load(handle) or {}
        except Exception:
            continue

        hostname = data.get("hostname") or file_path.stem.replace("_result", "")
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


def _acquire_ssh_connection(host, username, password, port=None):
    try:
        port_value = int(str(port)) if port is not None else 22
    except ValueError:
        port_value = 22
    client = remote_connect(host, username, password, port=port_value)
    if not client:
        return None, None, port_value
    shell = getattr(client, "_shell", None)
    if shell is None:
        try:
            shell = client.invoke_shell()
            client._shell = shell
        except Exception:
            shell = None
    return client, shell, port_value


def stream_json_line(obj):
    return json.dumps(obj) + "\n"


def _expand_path(path):
    """Expand ~ in user supplied paths."""
    return os.path.expanduser(path) if path else None


def _hostname_matches_target(expected, actual):
    expected_name = str(expected or "").strip().upper()
    actual_name = str(actual or "").strip().upper()
    if not expected_name or not actual_name:
        return True
    return expected_name == actual_name


def _engine_student_logs_dir(
    classroom, tutor_name, time_slot, student_id, hostname=None
):
    safe_classroom = str(classroom or "").strip()
    safe_tutor = str(tutor_name or "").strip()
    safe_time = str(time_slot or "").strip()
    safe_student = str(student_id or "").strip()
    if not all([safe_classroom, safe_tutor, safe_time, safe_student]):
        return None
    if safe_student.lower() in {"sample", "unknown"}:
        return None
    target_dir = (
        ENGINE_STUDENTS_DIR / safe_classroom / safe_tutor / safe_time / safe_student
    )
    if hostname:
        target_dir = target_dir / str(hostname).strip()
    return target_dir


def _delete_engine_student_logs_for_docs_target(target):
    try:
        relative = target.resolve().relative_to(DOCS_DIR)
    except Exception:
        return

    if len(relative.parts) < 1:
        return

    mirror_target = ENGINE_STUDENTS_DIR.joinpath(*relative.parts)
    if mirror_target.exists():
        shutil.rmtree(mirror_target)


def _session_student_names_path(session_dir: Path) -> Path:
    return session_dir / "students.json"


def _load_session_student_names(session_dir: Path) -> dict:
    path = _session_student_names_path(session_dir)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(k).strip()}
    except Exception:
        return {}
    return {}


def _save_session_student_names(session_dir: Path, names: dict):
    path = _session_student_names_path(session_dir)
    cleaned = {
        str(k): str(v)
        for k, v in (names or {}).items()
        if str(k).strip() and str(v).strip()
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)


def _safe_is_visible_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not path.name.startswith(".")
    except (OSError, PermissionError):
        return False


def _safe_iterdir(path: Path):
    try:
        return list(path.iterdir())
    except (OSError, PermissionError):
        return []


def _save_output_to_engine_students(
    command, output, classroom, tutor_name, time_slot, student_id, hostname
):
    """
    Save command output under
    comparison_engine/students/<classroom>/<tutor_name>/<time_slot>/<student_id>/<hostname>/.
    Only stores command logs (no config.json).
    """
    if not hostname:
        return None
    target_dir = _engine_student_logs_dir(
        classroom, tutor_name, time_slot, student_id, hostname
    )
    if target_dir is None:
        return None
    safe_command = command.replace(" ", "_").replace("/", "_")
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{safe_command}.txt"
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(output)
    return str(file_path)


# -------------------------------------------------
# ✅ Directory Endpoints
# -------------------------------------------------
def _validate_directory_payload(data):
    classroom = (
        data.get("classroom") or data.get("examName") or data.get("exam_name") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name")
        or data.get("tutorName")
        or data.get("sessionId")
        or data.get("session_id")
        or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    student_id = (data.get("studentId") or data.get("student_id") or "").strip()

    if not all([classroom, tutor_name, time_slot, student_id]):
        return (
            None,
            jsonify(
                {
                    "status": "error",
                    "message": "Missing classroom/tutor_name/time_slot/studentId",
                }
            ),
            400,
        )

    try:
        classroom = _normalize_directory_segment(classroom, "Classroom")
        tutor_name = _normalize_directory_segment(tutor_name, "Tutor name")
        time_slot = _normalize_directory_segment(time_slot, "Time slot")
        student_id = _normalize_directory_segment(student_id, "Student ID")
    except ValueError as exc:
        return None, jsonify({"status": "error", "message": str(exc)}), 400

    return (classroom, tutor_name, time_slot, student_id), None, None


@app.route("/api/create_directory", methods=["POST"])
def api_create_directory():
    """
    Create the standard directory hierarchy for a student.
    """
    data = request.get_json() or {}
    validated, error_resp, status = _validate_directory_payload(data)
    if error_resp:
        return error_resp, status

    classroom, tutor_name, time_slot, student_id = validated
    student_name = (data.get("studentName") or data.get("student_name") or "").strip()
    base_path = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    if student_name:
        session_dir = Path.home() / "Documents" / classroom / tutor_name / time_slot
        names = _load_session_student_names(session_dir)
        names[student_id] = student_name
        _save_session_student_names(session_dir, names)
    return jsonify(
        {
            "status": "ok",
            "message": f"Directory ready: {base_path}",
            "path": base_path,
            "classroom": classroom,
            "tutor_name": tutor_name,
            "time_slot": time_slot,
            # Backward-compatible response keys
            "exam_name": classroom,
            "session_id": tutor_name,
            "student_id": student_id,
            "student_name": student_name,
        }
    )


@app.route("/api/select_directory", methods=["POST"])
def api_select_directory():
    """
    Reuse an existing directory path provided by the user.
    """
    data = request.get_json() or {}
    existing_path = _expand_path(data.get("existingPath"))
    if not existing_path:
        return (
            jsonify(
                {"status": "error", "message": "Missing existingPath for selection"}
            ),
            400,
        )

    if os.path.exists(existing_path):
        parts = Path(existing_path).parts
        if len(parts) >= 4:
            classroom, tutor_name, time_slot, student_id = (
                parts[-4],
                parts[-3],
                parts[-2],
                parts[-1],
            )
        else:
            classroom = (
                data.get("classroom") or data.get("examName") or data.get("exam_name")
            )
            tutor_name = (
                data.get("tutor_name")
                or data.get("tutorName")
                or data.get("sessionId")
                or data.get("session_id")
            )
            time_slot = data.get("time_slot") or data.get("timeSlot")
            student_id = data.get("studentId") or data.get("student_id")
        return jsonify(
            {
                "status": "ok",
                "message": f"Using existing directory: {existing_path}",
                "path": existing_path,
                "classroom": classroom,
                "tutor_name": tutor_name,
                "time_slot": time_slot,
                # Backward-compatible response keys
                "exam_name": classroom,
                "session_id": tutor_name,
                "student_id": student_id,
            }
        )

    return (
        jsonify({"status": "error", "message": f"Path not found: {existing_path}"}),
        404,
    )


def _list_existing_directories():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for classroom_dir in _safe_iterdir(docs_path):
        if not _safe_is_visible_dir(classroom_dir):
            continue
        for tutor_dir in _safe_iterdir(classroom_dir):
            if not _safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in _safe_iterdir(tutor_dir):
                if not _safe_is_visible_dir(time_dir):
                    continue
                student_names = _load_session_student_names(time_dir)
                for student_dir in _safe_iterdir(time_dir):
                    if not _safe_is_visible_dir(student_dir):
                        continue
                    results.append(
                        {
                            "path": str(student_dir),
                            "classroom": classroom_dir.name,
                            "tutor_name": tutor_dir.name,
                            "time_slot": time_dir.name,
                            # Backward-compatible keys
                            "exam_name": classroom_dir.name,
                            "session_id": tutor_dir.name,
                            "student_id": student_dir.name,
                            "student_name": student_names.get(student_dir.name, ""),
                            "display": (
                                f"{classroom_dir.name}/{tutor_dir.name}/"
                                f"{time_dir.name}/{student_dir.name}"
                            ),
                        }
                    )
    return sorted(results, key=lambda x: x["display"])


def _list_existing_sessions():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for classroom_dir in _safe_iterdir(docs_path):
        if not _safe_is_visible_dir(classroom_dir):
            continue
        for tutor_dir in _safe_iterdir(classroom_dir):
            if not _safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in _safe_iterdir(tutor_dir):
                if not _safe_is_visible_dir(time_dir):
                    continue
                results.append(
                    {
                        "path": str(time_dir),
                        "classroom": classroom_dir.name,
                        "tutor_name": tutor_dir.name,
                        "time_slot": time_dir.name,
                        "exam_name": classroom_dir.name,
                        "session_id": tutor_dir.name,
                        "display": f"{classroom_dir.name}/{tutor_dir.name}/{time_dir.name}",
                    }
                )
    return sorted(results, key=lambda x: x["display"])


def _list_existing_exams():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for classroom_dir in _safe_iterdir(docs_path):
        if not _safe_is_visible_dir(classroom_dir):
            continue
        # Only include dirs that contain at least one tutor/time subdirectory
        has_session = any(
            _safe_is_visible_dir(d) for d in _safe_iterdir(classroom_dir)
        )
        if has_session:
            results.append(
                {
                    "path": str(classroom_dir),
                    "classroom": classroom_dir.name,
                    "exam_name": classroom_dir.name,
                    "display": classroom_dir.name,
                }
            )
    return sorted(results, key=lambda x: x["display"])


def _is_windows_drives_root(path_val):
    return os.name == "nt" and str(path_val or "") == WINDOWS_DRIVES_ROOT


def _list_windows_drive_roots():
    drives = []
    if os.name != "nt":
        return drives

    for letter in string.ascii_uppercase:
        drive_path = f"{letter}:\\"
        if os.path.exists(drive_path):
            drives.append(
                {
                    "name": f"{letter}:",
                    "path": drive_path,
                    "is_drive": True,
                }
            )
    return drives


def _resolve_picker_path(path_val, fallback):
    if _is_windows_drives_root(path_val):
        return WINDOWS_DRIVES_ROOT
    if path_val:
        return Path(_expand_path(path_val)).resolve()
    return fallback


@app.route("/api/directories", methods=["GET"])
def api_list_directories():
    path_val = request.args.get("path")
    docs_path = (Path.home() / "Documents").resolve()

    # If a path is provided, use it as the "current" one, otherwise default to ~/Documents
    try:
        current = _resolve_picker_path(path_val, docs_path)
    except Exception:
        current = docs_path

    # Only return the managed "directories" list if we are explicitly at the managed root.
    # Otherwise, we want the frontend to fall back to 'loadSubfolders' to show the actual directory contents.
    directories = []
    if current == docs_path:
        directories = _list_existing_directories()

    if current == WINDOWS_DRIVES_ROOT:
        parent_path = WINDOWS_DRIVES_ROOT
    else:
        parent_path = str(current.parent)
        if os.name == "nt" and current.anchor:
            try:
                if current.resolve() == Path(current.anchor).resolve():
                    parent_path = WINDOWS_DRIVES_ROOT
            except Exception:
                if str(current) == current.anchor:
                    parent_path = WINDOWS_DRIVES_ROOT

    return jsonify(
        {
            "status": "ok",
            "directories": directories,
            "current_path": str(current),
            "parent_path": parent_path,
        }
    )


@app.route("/api/subfolders", methods=["GET"])
def api_list_subfolders():
    path_val = request.args.get("path")

    if _is_windows_drives_root(path_val):
        return jsonify(
            {
                "status": "ok",
                "subfolders": _list_windows_drive_roots(),
                "current_path": WINDOWS_DRIVES_ROOT,
                "parent_path": WINDOWS_DRIVES_ROOT,
            }
        )

    # If path not provided, default to user home so they can see Documents, Downloads etc.
    if not path_val:
        target = Path.home()
    else:
        try:
            target = Path(_expand_path(path_val)).resolve()
        except:
            return jsonify({"status": "error", "message": "Invalid path"}), 400

    if not target.exists() or not target.is_dir():
        return jsonify({"status": "error", "message": "Path not found"}), 404

    subfolders = []
    try:
        # List directories only
        for item in _safe_iterdir(target):
            if _safe_is_visible_dir(item):
                subfolders.append({"name": item.name, "path": str(item)})
        subfolders.sort(key=lambda x: x["name"].lower())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    parent_path = str(target.parent)
    if os.name == "nt" and target.anchor:
        try:
            if target.resolve() == Path(target.anchor).resolve():
                parent_path = WINDOWS_DRIVES_ROOT
        except Exception:
            if str(target) == target.anchor:
                parent_path = WINDOWS_DRIVES_ROOT

    return jsonify(
        {
            "status": "ok",
            "subfolders": subfolders,
            "current_path": str(target),
            "parent_path": parent_path,
        }
    )


@app.route("/api/directories/bulk", methods=["POST"])
def api_bulk_directories():
    data = request.get_json() or {}
    classroom = (
        data.get("classroom") or data.get("examName") or data.get("exam_name") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name")
        or data.get("tutorName")
        or data.get("sessionId")
        or data.get("session_id")
        or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    students = data.get("students") or []

    if not classroom or not tutor_name or not time_slot or not students:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing classroom/tutor_name/time_slot/students for bulk creation.",
                }
            ),
            400,
        )

    try:
        classroom = _normalize_directory_segment(classroom, "Classroom")
        tutor_name = _normalize_directory_segment(tutor_name, "Tutor name")
        time_slot = _normalize_directory_segment(time_slot, "Time slot")
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    created = []
    base_docs_path = Path.home() / "Documents"
    session_dir = base_docs_path / classroom / tutor_name / time_slot
    session_dir.mkdir(parents=True, exist_ok=True)
    student_names = _load_session_student_names(session_dir)

    for student in students:
        student_id = (student.get("id") or "").strip()
        student_name = (student.get("name") or "").strip()
        if not student_id:
            continue
        try:
            student_id = _normalize_directory_segment(student_id, "Student ID")
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        student_dir = session_dir / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        if student_name:
            student_names[student_id] = student_name
        created.append(
            {
                "path": str(student_dir),
                "classroom": classroom,
                "tutor_name": tutor_name,
                "time_slot": time_slot,
                "exam_name": classroom,
                "session_id": tutor_name,
                "student_id": student_id,
                "student_name": student_name,
                "display": f"{classroom}/{tutor_name}/{time_slot}/{student_id}",
            }
        )

    _save_session_student_names(session_dir, student_names)

    return jsonify({"status": "ok", "created": created})


# -------------------------------------------------
# ✅ Connection Test Endpoint
# -------------------------------------------------


@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Stream connection progress to the client for serial/SSH tests."""
    data = request.get_json() or {}
    mode = (data.get("mode") or data.get("connection") or "").lower()

    def stream_error(message, trace=None):
        print(f"[API][connect] ERROR: {message}", flush=True)
        payload = {"type": "error", "msg": message}
        if trace:
            payload["trace"] = trace
        payload["done"] = True
        return stream_json_line(payload)

    if mode not in {"serial", "ssh"}:
        return Response(
            stream_error("Invalid connection type"), mimetype="text/plain", status=400
        )

    def serial_generator():
        global current_mode
        serial_cfg = data.get("serial") or {}
        with connection_lock:
            stored_port = last_used_serial_settings.get("port")
            stored_baud = last_used_serial_settings.get("baudrate", 9600)
            existing_hostname = serial_hostname or "device"
        port = serial_cfg.get("port") or stored_port or "/dev/ttyUSB0"
        baudrate = (
            serial_cfg.get("baudrate") or serial_cfg.get("baud") or stored_baud or 9600
        )
        with connection_lock:
            last_used_serial_settings["port"] = port
            last_used_serial_settings["baudrate"] = baudrate

        _close_serial_connection()
        _close_ssh_connection()

        yield stream_json_line(
            {
                "type": "progress",
                "msg": f"Connecting over serial: {port}",
            }
        )

        queue = SimpleQueue()

        def status_cb(message):
            queue.put(("progress", message))

        def worker():
            ser = None
            try:
                ser = connect_to_serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=READ_TIMEOUT,
                    retry_interval=3,
                    max_retries=5,
                    status_cb=status_cb,
                )
                if not ser:
                    queue.put(("error", f"Failed to open serial port {port}"))
                    return

                queue.put(("progress", "Ensuring privileged access..."))
                enter_enable_mode(ser)
                queue.put(("progress", "Disabling paging..."))
                disable_paging(ser)
                queue.put(("progress", "Waking console and detecting hostname..."))
                hostname = detect_hostname_with_prompt_retry(
                    ser, fallback="device", attempts=2
                )

                _update_serial_state(ser, port, baudrate, hostname)

                queue.put(("success", {"hostname": hostname, "port": port}))
            except Exception as exc:
                if ser:
                    try:
                        logout_close_connection(ser)
                    except Exception:
                        pass
                queue.put(("exception", (str(exc), traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()

        while True:
            event, payload = queue.get()
            if event == "progress":
                print(f"[API][connect][serial] {payload}", flush=True)
                yield stream_json_line({"type": "progress", "msg": payload})
            elif event == "success":
                hostname = payload.get("hostname", "device")
                port_value = payload.get("port")
                print(
                    f"[API][connect][serial] Connected to {hostname} (port={port_value})",
                    flush=True,
                )
                yield stream_json_line(
                    {
                        "type": "success",
                        "msg": f"Connected to {hostname}",
                        "hostname": hostname,
                        "port": port_value,
                        "persistent": True,
                    }
                )
                yield stream_json_line(
                    {
                        "type": "done",
                        "success": True,
                        "hostname": hostname,
                        "port": port_value,
                    }
                )
                return
            elif event == "error":
                print(f"[API][connect][serial] ERROR: {payload}", flush=True)
                yield stream_json_line({"type": "error", "msg": payload})
                yield stream_json_line({"type": "done", "success": False})
                return
            elif event == "exception":
                msg, tb = payload
                print(f"[API][connect][serial] EXCEPTION: {msg}", flush=True)
                yield stream_json_line({"type": "error", "msg": msg, "trace": tb})
                yield stream_json_line({"type": "done", "success": False})
                return

    def ssh_generator():
        global current_mode
        ssh = data.get("ssh") or {}
        host = ssh.get("host") or data.get("host")
        user = ssh.get("username") or data.get("username")
        pwd = ssh.get("password") or data.get("password")
        raw_port = (
            ssh.get("port")
            or data.get("port")
            or last_used_ssh_credentials.get("port", 22)
        )
        try:
            port_value = int(str(raw_port))
        except (TypeError, ValueError):
            port_value = 22

        if not all([host, user, pwd]):
            print("[API][connect][ssh] Missing credentials.", flush=True)
            yield stream_json_line(
                {
                    "type": "error",
                    "msg": "Missing SSH credentials (host, username, password).",
                }
            )
            yield stream_json_line({"type": "done", "success": False})
            return

        with connection_lock:
            active_client = ssh_client if _is_ssh_client_active(ssh_client) else None
            cached_host = last_used_ssh_credentials.get("host")
            cached_user = last_used_ssh_credentials.get("username")
            cached_port = last_used_ssh_credentials.get("port")
            cached_hostname = ssh_hostname or host

        if (
            active_client
            and cached_host == host
            and cached_user == user
            and (cached_port or port_value) == port_value
        ):
            print(f"[API][connect][ssh] Reusing SSH session to {host}", flush=True)
            with connection_lock:
                current_mode = "ssh"
            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Reusing existing SSH session to {host}",
                }
            )
            yield stream_json_line(
                {
                    "type": "success",
                    "msg": f"Connected to {cached_hostname}",
                    "hostname": cached_hostname,
                    "host": host,
                    "port": port_value,
                    "persistent": True,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "success": True,
                    "hostname": cached_hostname,
                    "host": host,
                    "port": port_value,
                }
            )
            return

        _close_serial_connection()
        _close_ssh_connection()

        print(f"[API][connect][ssh] Connecting to {host}:{port_value} ...", flush=True)
        yield stream_json_line(
            {
                "type": "progress",
                "msg": f"Connecting to {host}:{port_value} via SSH...",
            }
        )

        try:
            result = _acquire_ssh_connection(host, user, pwd, port_value)
            client, shell, resolved_port = result
            if not client:
                print(
                    f"[API][connect][ssh] Connection to {host}:{port_value} failed.",
                    flush=True,
                )
                yield stream_json_line(
                    {"type": "error", "msg": "SSH connection failed."}
                )
                yield stream_json_line({"type": "done", "success": False})
                return

            print("[API][connect][ssh] Entering enable mode...", flush=True)
            yield stream_json_line(
                {"type": "progress", "msg": "Entering enable mode..."}
            )
            enter_enable_mode_remote(client)
            print("[API][connect][ssh] Disabling paging...", flush=True)
            yield stream_json_line({"type": "progress", "msg": "Disabling paging..."})
            disable_paging_remote(client)
            try:
                hostname = get_hostname_remote(client) or host
                print(f"[API][connect][ssh] Detected hostname: {hostname}", flush=True)
                yield stream_json_line(
                    {"type": "progress", "msg": f"Detected hostname: {hostname}"}
                )
            except Exception:
                hostname = host
                print("[API][connect][ssh] Hostname detection failed.", flush=True)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Connected but hostname detection failed.",
                    }
                )

            _update_ssh_state(client, host, user, pwd, hostname, resolved_port)

            print(f"[API][connect][ssh] Connected to {hostname}", flush=True)
            yield stream_json_line(
                {
                    "type": "success",
                    "msg": f"Connected to {hostname}",
                    "hostname": hostname,
                    "host": host,
                    "port": resolved_port,
                    "persistent": True,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "success": True,
                    "hostname": hostname,
                    "host": host,
                    "port": resolved_port,
                }
            )
        except Exception as exc:
            print(f"[API][connect][ssh] EXCEPTION: {exc}", flush=True)
            yield stream_json_line(
                {"type": "error", "msg": str(exc), "trace": traceback.format_exc()}
            )
            yield stream_json_line({"type": "done", "success": False})

    generator = serial_generator() if mode == "serial" else ssh_generator()
    return Response(stream_with_context(generator), mimetype="text/plain")


@app.route("/api/reset_device", methods=["POST"])
def api_reset_device():
    execution_abort.clear()
    data = request.get_json() or {}
    mode = (data.get("mode") or data.get("connection") or "serial").lower()
    device_type = str(data.get("device_type") or "switch").strip().lower()
    if mode != "serial":
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Device reset is only supported over serial.",
                }
            ),
            400,
        )

    serial_payload = data.get("serial") or {}
    with connection_lock:
        stored_port = last_used_serial_settings.get("port")
        stored_baud = last_used_serial_settings.get("baudrate", 9600)
    port = (
        serial_payload.get("port") or data.get("port") or stored_port or "/dev/ttyUSB0"
    )
    raw_baud = (
        serial_payload.get("baudrate")
        or serial_payload.get("baud")
        or data.get("baudrate")
        or stored_baud
        or 9600
    )
    try:
        baudrate = int(raw_baud)
    except (TypeError, ValueError):
        baudrate = 9600

    if not port:
        return (
            jsonify({"status": "error", "message": "No serial port configured."}),
            400,
        )

    _close_serial_connection()
    _close_ssh_connection()

    result = reload_cisco_device(
        port=port,
        baudrate=baudrate,
        delete_vlan_database=(device_type != "router"),
        abort_event=execution_abort,
    )
    logs = result.get("logs") or []
    message = result.get("message") or "Reset completed."
    if result.get("aborted"):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": message,
                    "logs": logs,
                    "aborted": True,
                    "port": port,
                    "baudrate": baudrate,
                    "device_type": device_type,
                }
            ),
            499,
        )
    if result.get("success"):
        return jsonify(
            {
                "status": "ok",
                "message": message,
                "logs": logs,
                "port": port,
                "baudrate": baudrate,
                "device_type": device_type,
            }
        )
    return (
        jsonify(
            {
                "status": "error",
                "message": message,
                "logs": logs,
                "port": port,
                "baudrate": baudrate,
            }
        ),
        500,
    )


# -------------------------------------------------
# ✅ Get Commands
# -------------------------------------------------
@app.route("/api/commands", methods=["GET"])
def api_get_commands():
    try:
        commands = load_commands()
        return jsonify({"status": "ok", "commands": commands})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/commands", methods=["POST"])
def api_add_command():
    data = request.get_json() or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"status": "error", "message": "Command text is required."}), 400

    commands = load_commands()
    if command in commands:
        return jsonify({"status": "error", "message": "Command already exists."}), 400

    commands.append(command)
    save_commands(commands)
    return jsonify({"status": "ok", "commands": commands})


@app.route("/api/commands", methods=["DELETE"])
def api_delete_command():
    data = request.get_json() or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"status": "error", "message": "Command text is required."}), 400

    commands = load_commands()
    if command not in commands:
        return jsonify({"status": "error", "message": "Command not found."}), 404

    commands = [c for c in commands if c != command]
    save_commands(commands)
    return jsonify({"status": "ok", "commands": commands})


# -------------------------------------------------
# ✅ Save Log Endpoint
# -------------------------------------------------
@app.route("/api/save_log", methods=["POST"])
def api_save_log():
    data = request.get_json() or {}
    classroom = (
        data.get("classroom") or data.get("exam_name") or data.get("examName") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name") or data.get("session_id") or data.get("sessionId") or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    student_id = data.get("student_id")
    filename = data.get("filename", "log.txt")
    content = data.get("content", "")

    if not (classroom and tutor_name and time_slot and student_id):
        return jsonify({"status": "error", "message": "Missing directory info"}), 400

    base_dir = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, filename)

    try:
        with open(path, "w") as f:
            f.write(content)
        return jsonify({"status": "ok", "message": f"Saved log to {path}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# -------------------------------------------------
# ✅ Execute Endpoint
# -------------------------------------------------
def _ensure_base_path(data):
    """
    Resolve the base directory for log storage based on payload.
    """
    mode = data.get("log_mode", "create")
    log_dir = data.get("log_dir")
    classroom = data.get("classroom") or data.get("exam_name") or data.get("examName")
    tutor_name = (
        data.get("tutor_name") or data.get("session_id") or data.get("sessionId")
    )
    time_slot = data.get("time_slot") or data.get("timeSlot")
    student_id = data.get("student_id")

    if mode == "existing":
        if not log_dir:
            raise ValueError("Missing log_dir for existing directory mode.")
        expanded = _expand_path(log_dir)
        if not expanded or not os.path.exists(expanded):
            raise FileNotFoundError(f"Existing directory not found: {log_dir}")
        return expanded, classroom, tutor_name, time_slot, student_id

    if not all([classroom, tutor_name, time_slot, student_id]):
        raise ValueError(
            "Missing classroom/tutor/time/student details for directory creation."
        )

    classroom = _normalize_directory_segment(classroom, "Classroom")
    tutor_name = _normalize_directory_segment(tutor_name, "Tutor name")
    time_slot = _normalize_directory_segment(time_slot, "Time slot")
    student_id = _normalize_directory_segment(student_id, "Student ID")

    base_path = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    return base_path, classroom, tutor_name, time_slot, student_id


@app.route("/api/abort", methods=["POST"])
def api_abort():
    """Signal the running execution to stop immediately."""
    execution_abort.set()
    # Close connections to force any blocking read to fail
    _close_serial_connection()
    _close_ssh_connection()
    return jsonify({"status": "ok", "message": "Abort signal sent."})


@app.route("/api/execute", methods=["POST"])
def api_execute():
    execution_abort.clear()
    data = request.get_json() or {}
    commands = data.get("commands") or []
    target_device = data.get("deviceId") or data.get("target_device")
    requested_mode = (
        data.get("mode") or data.get("connection") or current_mode or "serial"
    ).lower()

    print(
        f"[DEBUG] /api/execute called with mode={requested_mode}, current_mode={current_mode}, deviceId={target_device}",
        flush=True,
    )

    if not commands:
        return jsonify({"status": "error", "message": "No commands provided"}), 400
    if not requested_mode:
        return (
            jsonify({"status": "error", "message": "No connection mode selected."}),
            400,
        )
    if requested_mode not in {"serial", "ssh"}:
        return (
            jsonify({"status": "error", "message": "Invalid connection type"}),
            400,
        )

    try:
        base_path, classroom, tutor_name, time_slot, student_id = _ensure_base_path(
            data
        )
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    def generate():
        hostname = None
        files_written = []
        skip_config = bool(data.get("skip_config"))
        skip_hostname_check = bool(data.get("skip_hostname_check"))

        def run_serial():
            global current_mode
            nonlocal hostname
            serial_payload = data.get("serial") or {}
            with connection_lock:
                stored_port = last_used_serial_settings.get("port")
                stored_baud = last_used_serial_settings.get("baudrate", 9600)
                existing_ser = (
                    serial_conn if serial_conn and serial_conn.is_open else None
                )
                stored_hostname = serial_hostname or "device"
            port = serial_payload.get("port") or stored_port or "/dev/ttyUSB0"
            baudrate = (
                serial_payload.get("baudrate")
                or serial_payload.get("baud")
                or stored_baud
                or 9600
            )
            if not port:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Serial mode selected but no port configured. Please connect via serial first.",
                    }
                )
                return False
            with connection_lock:
                last_used_serial_settings["port"] = port
                last_used_serial_settings["baudrate"] = baudrate

            ser = None
            reuse = False
            _close_ssh_connection()
            _close_serial_connection()
            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Connecting over serial: {port}",
                    "progress_pct": 0,
                }
            )
            try:
                ser = connect_to_serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=READ_TIMEOUT,
                    retry_interval=3,
                    max_retries=5,
                    abort_event=execution_abort,
                )
            except Exception as exc:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: {exc}",
                    }
                )
                return False
            if not ser:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: device not responding.",
                    }
                )
                return False
            try:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Ensuring privileged access...",
                        "progress_pct": 0,
                    }
                )
                enter_enable_mode(ser)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Disabling paging...",
                        "progress_pct": 0,
                    }
                )
                disable_paging(ser)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Waking console and detecting hostname...",
                        "progress_pct": 0,
                    }
                )
                hostname = detect_hostname_with_prompt_retry(
                    ser, fallback="device", attempts=2
                )
            except Exception as exc:
                logout_close_connection(ser)
                yield stream_json_line(
                    {"type": "error", "msg": f"Serial initialization failed: {exc}"}
                )
                return False
            if target_device and not _hostname_matches_target(target_device, hostname):
                if skip_hostname_check:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                        }
                    )
                else:
                    logout_close_connection(ser)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "error_code": "HOSTNAME_MISMATCH",
                            "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                        }
                    )
                    return False
            _update_serial_state(ser, port, baudrate, hostname)

            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Connected to {hostname} via serial.",
                    "progress_pct": 0,
                }
            )

            local_ser = ser or serial_conn
            if not local_ser:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Serial connection unavailable after setup.",
                    }
                )
                return False

            completed = 0
            total_commands = len(commands)
            try:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Waiting for device prompt before command run...",
                    }
                )
                wait_serial_prompt_ready(local_ser, timeout=6)
            except Exception as exc:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Prompt wake warning before commands: {exc}. Continuing...",
                    }
                )
            for cmd in commands:
                cli_cmd = _canonical_cli_command(cmd)
                yield stream_json_line(
                    {"type": "progress", "msg": f"Running '{cli_cmd}'..."}
                )
                try:
                    output = send_command(local_ser, cli_cmd, timeout=30)
                    yield stream_json_line(
                        {
                            "type": "raw_output",
                            "msg": f"{hostname}# {cli_cmd}\n{output}"
                        }
                    )
                    file_path = save_output_to_file(
                        cli_cmd,
                        output,
                        classroom=classroom,
                        tutor_name=tutor_name,
                        time_slot=time_slot,
                        student_id=student_id,
                        hostname=target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cli_cmd,
                        output,
                        classroom,
                        tutor_name,
                        time_slot,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = (
                        round((completed / total_commands) * 100)
                        if total_commands
                        else 100
                    )
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cli_cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, target_device or hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cli_cmd}' failed: {exc}",
                        }
                    )
                    _close_serial_connection()
                    return False

            if not skip_config:
                # Build parsed config.json for the student device logs.
                try:
                    host_folder = target_device or hostname or "device"
                    host_dir = os.path.join(base_path, host_folder)
                    os.makedirs(host_dir, exist_ok=True)
                    config = parse_device_logs(files_written)
                    config_path = os.path.join(host_dir, "config.json")
                    with open(config_path, "w") as handle:
                        json.dump(config, handle, indent=4)
                    yield stream_json_line(
                        {"type": "result", "msg": f"Saved config.json to {config_path}"}
                    )
                except Exception as exc:
                    try:
                        host_folder = target_device or hostname or "device"
                        host_dir = os.path.join(base_path, host_folder)
                        os.makedirs(host_dir, exist_ok=True)
                        fallback = parse_device_logs([])
                        fallback["parse_error"] = str(exc)
                        config_path = os.path.join(host_dir, "config.json")
                        with open(config_path, "w") as handle:
                            json.dump(fallback, handle, indent=4)
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to parse logs ({exc}). Wrote fallback config.json to {config_path}",
                            }
                        )
                    except Exception as exc2:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to save config.json: {exc}; fallback failed: {exc2}",
                            }
                        )

            # Close the port so the user can physically unplug the cable for the next queue item
            _close_serial_connection()
            return True

        def run_ssh():
            global current_mode
            nonlocal hostname
            ssh_payload = data.get("ssh") or {}
            with connection_lock:
                active_client = (
                    ssh_client if _is_ssh_client_active(ssh_client) else None
                )
                cached_host = last_used_ssh_credentials.get("host")
                cached_user = last_used_ssh_credentials.get("username")
                cached_port = last_used_ssh_credentials.get("port")
                stored_hostname = ssh_hostname or ssh_payload.get("host")
            host = ssh_payload.get("host") or cached_host
            username = ssh_payload.get("username") or cached_user
            password = ssh_payload.get("password") or last_used_ssh_credentials.get(
                "password"
            )
            raw_port = ssh_payload.get("port") or cached_port or 22
            try:
                port_value = int(str(raw_port))
            except (TypeError, ValueError):
                port_value = 22

            if not all([host, username, password]):
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Missing SSH credentials (host/username/password).",
                    }
                )
                return False

            client = None
            reuse = False
            if (
                active_client
                and cached_host == host
                and cached_user == username
                and (cached_port or port_value) == port_value
            ):
                client = active_client
                hostname = stored_hostname or host
                reuse = True
                with connection_lock:
                    current_mode = "ssh"
            else:
                _close_serial_connection()
                _close_ssh_connection()
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connecting to {host} via SSH...",
                        "progress_pct": 0,
                    }
                )
                result = _acquire_ssh_connection(host, username, password, port_value)
                client, shell, resolved_port = result
                if not client:
                    yield stream_json_line(
                        {"type": "error", "msg": "SSH connection failed."}
                    )
                    return False
                try:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": "Entering enable mode...",
                            "progress_pct": 0,
                        }
                    )
                    enter_enable_mode_remote(client)
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": "Disabling paging...",
                            "progress_pct": 0,
                        }
                    )
                    disable_paging_remote(client)
                    try:
                        hostname = get_hostname_remote(client) or host
                    except Exception:
                        hostname = host
                except Exception as exc:
                    try:
                        if client:
                            existing_shell = getattr(client, "_shell", None)
                            if existing_shell:
                                existing_shell.close()
                            client.close()
                    except Exception:
                        pass
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"SSH initialization failed: {exc}",
                        }
                    )
                    return False
                if target_device and not _hostname_matches_target(
                    target_device, hostname
                ):
                    if skip_hostname_check:
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                            }
                        )
                    else:
                        try:
                            existing_shell = getattr(client, "_shell", None)
                            if existing_shell:
                                existing_shell.close()
                        except Exception:
                            pass
                        try:
                            client.close()
                        except Exception:
                            pass
                        yield stream_json_line(
                            {
                                "type": "error",
                                "error_code": "HOSTNAME_MISMATCH",
                                "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                            }
                        )
                        return False
                    _update_ssh_state(
                        client, host, username, password, hostname, resolved_port
                    )

            if not reuse:
                if target_device and not _hostname_matches_target(
                    target_device, hostname
                ):
                    if skip_hostname_check:
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                            }
                        )
                    else:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "error_code": "HOSTNAME_MISMATCH",
                                "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                            }
                        )
                        return False
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connected to {hostname} via SSH.",
                        "progress_pct": 0,
                    }
                )

            active = client or ssh_client
            if not active:
                yield stream_json_line(
                    {"type": "error", "msg": "SSH connection unavailable after setup."}
                )
                return False

            completed = 0
            total_commands = len(commands)
            for cmd in commands:
                cli_cmd = _canonical_cli_command(cmd)
                yield stream_json_line(
                    {"type": "progress", "msg": f"Running '{cli_cmd}'..."}
                )
                try:
                    output = send_command_remote(active, cli_cmd, timeout=30)
                    yield stream_json_line(
                        {
                            "type": "raw_output",
                            "msg": f"{hostname}# {cli_cmd}\n{output}"
                        }
                    )
                    file_path = save_output_to_file(
                        cli_cmd,
                        output,
                        classroom=classroom,
                        tutor_name=tutor_name,
                        time_slot=time_slot,
                        student_id=student_id,
                        hostname=target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cli_cmd,
                        output,
                        classroom,
                        tutor_name,
                        time_slot,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = (
                        round((completed / total_commands) * 100)
                        if total_commands
                        else 100
                    )
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cli_cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, target_device or hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cli_cmd}' failed: {exc}",
                        }
                    )
                    return False

            if not skip_config:
                # Build parsed config.json for the student device logs.
                try:
                    host_folder = target_device or hostname or "device"
                    host_dir = os.path.join(base_path, host_folder)
                    os.makedirs(host_dir, exist_ok=True)
                    config = parse_device_logs(files_written)
                    config_path = os.path.join(host_dir, "config.json")
                    with open(config_path, "w") as handle:
                        json.dump(config, handle, indent=4)
                    yield stream_json_line(
                        {"type": "result", "msg": f"Saved config.json to {config_path}"}
                    )
                except Exception as exc:
                    try:
                        host_folder = target_device or hostname or "device"
                        host_dir = os.path.join(base_path, host_folder)
                        os.makedirs(host_dir, exist_ok=True)
                        fallback = parse_device_logs([])
                        fallback["parse_error"] = str(exc)
                        config_path = os.path.join(host_dir, "config.json")
                        with open(config_path, "w") as handle:
                            json.dump(fallback, handle, indent=4)
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to parse logs ({exc}). Wrote fallback config.json to {config_path}",
                            }
                        )
                    except Exception as exc2:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to save config.json: {exc}; fallback failed: {exc2}",
                            }
                        )
            return True

        yield stream_json_line(
            {
                "type": "progress",
                "msg": "Starting execution workflow...",
                "progress_pct": 0,
            }
        )

        try:
            if requested_mode == "serial":
                if not (yield from run_serial()):
                    return
            else:
                if not (yield from run_ssh()):
                    return

            yield stream_json_line(
                {
                    "type": "result",
                    "msg": "All commands executed successfully.",
                    "files": files_written,
                    "progress_pct": 100,
                    "hostname": hostname,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "msg": "Execution complete.",
                    "progress_pct": 100,
                }
            )
        except Exception as exc:
            tb = traceback.format_exc()
            cleanup_hostname = target_device or hostname
            if cleanup_hostname:
                del_partial_logs(base_path, cleanup_hostname)
            yield stream_json_line({"type": "error", "msg": str(exc), "trace": tb})

    return Response(generate(), mimetype="text/plain")


# -------------------------------------------------
# ✅ Grading System Endpoints
# -------------------------------------------------


def _get_yaml_file(directory, file_id):
    path = directory / f"{file_id}.yaml"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _save_yaml_file(directory, file_id, data):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{file_id}.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f)
    return str(path)


def _delete_yaml_file(directory, file_id):
    path = directory / f"{file_id}.yaml"
    if path.exists():
        path.unlink()
        return True
    return False


def _list_yaml_files(directory):
    items = []
    if not directory.exists():
        return items
    for f in directory.glob("*.yaml"):
        try:
            with open(f, "r") as yf:
                data = yaml.safe_load(yf) or {}
                # Ensure ID is present
                if "id" not in data:
                    data["id"] = f.stem
                items.append(data)
        except Exception:
            continue
    return sorted(items, key=lambda x: x.get("name", ""))


# --- Schemes ---


@app.route("/api/schemes", methods=["GET"])
def api_list_schemes():
    return jsonify({"status": "ok", "schemes": _list_yaml_files(SCHEMES_DIR)})


@app.route("/api/schemes", methods=["POST"])
def api_save_scheme():
    data = request.get_json() or {}
    scheme_id = data.get("id")
    if not scheme_id:
        import uuid

        scheme_id = str(uuid.uuid4())[:8]
        data["id"] = scheme_id

    try:
        _save_yaml_file(SCHEMES_DIR, scheme_id, data)
        return jsonify({"status": "ok", "message": "Scheme saved", "id": scheme_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/schemes/<scheme_id>", methods=["DELETE"])
def api_delete_scheme(scheme_id):
    if _delete_yaml_file(SCHEMES_DIR, scheme_id):
        return jsonify({"status": "ok", "message": "Scheme deleted"})
    return jsonify({"status": "error", "message": "Scheme not found"}), 404


# --- Rubrics ---


@app.route("/api/rubrics", methods=["GET"])
def api_list_rubrics():
    return jsonify({"status": "ok", "rubrics": _list_yaml_files(RUBRICS_DIR)})


@app.route("/api/rubrics", methods=["POST"])
def api_save_rubric():
    data = request.get_json() or {}
    rubric_id = data.get("id")
    if not rubric_id:
        import uuid

        rubric_id = str(uuid.uuid4())[:8]
        data["id"] = rubric_id

    try:
        _save_yaml_file(RUBRICS_DIR, rubric_id, data)
        return jsonify({"status": "ok", "message": "Rubric saved", "id": rubric_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/rubrics/<rubric_id>", methods=["DELETE"])
def api_delete_rubric(rubric_id):
    if _delete_yaml_file(RUBRICS_DIR, rubric_id):
        return jsonify({"status": "ok", "message": "Rubric deleted"})
    return jsonify({"status": "error", "message": "Rubric not found"}), 404


# --- Grading Policy ---


@app.route("/api/grading_policy", methods=["GET"])
def api_get_grading_policy():
    return jsonify({"status": "ok", "policy": load_grading_policy()})


@app.route("/api/grading_policy", methods=["POST"])
def api_save_grading_policy():
    data = request.get_json() or {}
    policy = load_grading_policy()

    major_threshold = data.get("major_threshold", policy.get("major_threshold"))
    minor_threshold = data.get("minor_threshold", policy.get("minor_threshold"))

    try:
        major_threshold = int(major_threshold)
        minor_threshold = int(minor_threshold)
    except Exception:
        return (
            jsonify({"status": "error", "message": "Thresholds must be integers."}),
            400,
        )

    if major_threshold < 1 or minor_threshold < 1:
        return (
            jsonify({"status": "error", "message": "Thresholds must be at least 1."}),
            400,
        )

    policy = save_grading_policy(
        {
            "major_threshold": major_threshold,
            "minor_threshold": minor_threshold,
        }
    )
    return jsonify({"status": "ok", "policy": policy})


@app.route("/api/rubric_rules", methods=["GET"])
def api_get_rubric_rules():
    return jsonify({"status": "ok", "rules": load_rubric_rules()})


@app.route("/api/rubric_rules", methods=["POST"])
def api_save_rubric_rules():
    data = request.get_json() or {}
    rules = data.get("rules")
    if rules is None:
        return jsonify({"status": "error", "message": "Missing rules."}), 400
    try:
        saved = save_rubric_rules(rules)
        return jsonify({"status": "ok", "rules": saved})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/rubric_rules/reset", methods=["POST"])
def api_reset_rubric_rules():
    try:
        rules = reset_rubric_rules()
        return jsonify({"status": "ok", "rules": rules})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/grading_dedup", methods=["GET"])
def api_get_grading_dedup():
    return jsonify({"status": "ok", "config": load_dedup_config()})


@app.route("/api/grading_dedup", methods=["POST"])
def api_save_grading_dedup():
    data = request.get_json() or {}
    config = data.get("config")
    if config is None:
        return jsonify({"status": "error", "message": "Missing dedup config."}), 400
    try:
        saved = save_dedup_config(config)
        return jsonify({"status": "ok", "config": saved})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/grading_dedup/reset", methods=["POST"])
def api_reset_grading_dedup():
    try:
        config = reset_dedup_config()
        return jsonify({"status": "ok", "config": config})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


# --- Results View ---


@app.route("/api/results", methods=["GET"])
def api_get_results():
    target_path = request.args.get("target_path")
    if not target_path:
        return jsonify({"status": "error", "message": "Missing target_path."}), 400
    if not os.path.isdir(target_path):
        return jsonify({"status": "error", "message": "target_path not found."}), 404

    return jsonify(
        {
            "status": "ok",
            "reports": _build_session_reports(target_path),
            "policy": load_grading_policy(),
        }
    )


@app.route("/api/error_context", methods=["POST"])
def api_error_context():
    data = request.get_json() or {}
    target_path = data.get("target_path")
    student_id = (data.get("student_id") or "").strip()
    template_name = (data.get("template_name") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    feature = (data.get("feature") or "").strip()
    expected = data.get("expected")
    actual = data.get("actual")

    if not all([target_path, student_id, template_name, hostname, feature]):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing target_path, student_id, template_name, hostname, or feature.",
                }
            ),
            400,
        )

    session_dir = Path(target_path).resolve()
    if not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    safe_session_dir = _safe_resolve_child(DOCS_DIR, session_dir)
    if not safe_session_dir:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_config_path = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname / "config.json"
    )
    template_config_path = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "config.json"
    )
    student_log_dir = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname
    )
    template_log_dir = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "logs"
    )

    template_config = (
        _load_json_file(template_config_path)
        if template_config_path and template_config_path.exists()
        else {}
    )
    student_config = (
        _load_json_file(student_config_path)
        if student_config_path and student_config_path.exists()
        else {}
    )
    command_hint = _command_hint_for_feature(feature)
    template_raw_path = _find_log_file(template_log_dir, command_hint)
    student_raw_path = _find_log_file(student_log_dir, command_hint)

    context = _extract_error_context(
        template_config, student_config, feature, expected=expected, actual=actual
    )
    template_raw_excerpt = _extract_raw_excerpt(
        template_raw_path, feature, expected=expected, actual=actual
    )
    student_raw_excerpt = _extract_raw_excerpt(
        student_raw_path, feature, expected=expected, actual=actual
    )

    return jsonify(
        {
            "status": "ok",
            "feature": feature,
            "hostname": hostname,
            "student_id": student_id,
            "template_name": template_name,
            "context_path": context["context_path"],
            "highlight_key": context["highlight_key"],
            "template_context": context["template_context"],
            "student_context": context["student_context"],
            "template_config_path": (
                str(template_config_path)
                if template_config_path and template_config_path.exists()
                else None
            ),
            "student_config_path": (
                str(student_config_path)
                if student_config_path and student_config_path.exists()
                else None
            ),
            "command_hint": command_hint,
            "template_raw_path": (
                str(template_raw_path)
                if template_raw_path and template_raw_path.exists()
                else None
            ),
            "student_raw_path": (
                str(student_raw_path)
                if student_raw_path and student_raw_path.exists()
                else None
            ),
            "template_raw_excerpt": template_raw_excerpt,
            "student_raw_excerpt": student_raw_excerpt,
        }
    )


@app.route("/api/raw_log_preview", methods=["POST"])
def api_raw_log_preview():
    data = request.get_json() or {}
    target_path = data.get("target_path")
    student_id = (data.get("student_id") or "").strip()
    template_name = (data.get("template_name") or "").strip()
    hostname = (data.get("hostname") or "").strip()

    if not all([target_path, student_id, template_name, hostname]):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing target_path, student_id, template_name, or hostname.",
                }
            ),
            400,
        )

    session_dir = Path(target_path).resolve()
    if not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    safe_session_dir = _safe_resolve_child(DOCS_DIR, session_dir)
    if not safe_session_dir:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_log_dir = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname
    )
    template_log_dir = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "logs"
    )

    template_logs = _raw_log_map(template_log_dir)
    student_logs = _raw_log_map(student_log_dir)
    command_keys = sorted(
        set(template_logs) | set(student_logs),
        key=lambda key: (
            template_logs.get(key, student_logs.get(key, {})).get("command") or key
        ).lower(),
    )

    paired_logs = []
    for key in command_keys:
        template_item = template_logs.get(key)
        student_item = student_logs.get(key)
        paired_logs.append(
            {
                "command": (
                    (template_item or {}).get("command")
                    or (student_item or {}).get("command")
                    or key
                ),
                "template": template_item,
                "student": student_item,
            }
        )

    template_items = [item["template"] for item in paired_logs if item.get("template")]
    student_items = [item["student"] for item in paired_logs if item.get("student")]

    return jsonify(
        {
            "status": "ok",
            "student_id": student_id,
            "template_name": template_name,
            "hostname": hostname,
            "template_log_dir": (
                str(template_log_dir)
                if template_log_dir and template_log_dir.exists()
                else None
            ),
            "student_log_dir": (
                str(student_log_dir)
                if student_log_dir and student_log_dir.exists()
                else None
            ),
            "logs": paired_logs,
            "template_combined": _render_combined_raw_logs(template_items, "Template"),
            "student_combined": _render_combined_raw_logs(student_items, "Student"),
        }
    )


@app.route("/api/melbourne/send", methods=["POST"])
def api_melbourne_send():
    try:
        payload = request.get_json(silent=True) or {}
        result = export_to_melbourne(payload)
        return jsonify({"status": "ok", **result})
    except Exception as exc:
        logging.exception("Melbourne export failed")
        return jsonify({"status": "error", "message": str(exc)}), 400


# -------------------------------------------------
# ✅ Admin Cleanup
# -------------------------------------------------
@app.route("/api/admin/templates", methods=["GET"])
def api_admin_list_templates():
    templates = []
    if TEMPLATES_DIR.is_dir():
        for entry in sorted(TEMPLATES_DIR.iterdir()):
            if entry.is_dir():
                templates.append(entry.name)
    return jsonify({"status": "ok", "templates": templates})


def _template_manifest_path(template_name: str) -> Path:
    return TEMPLATES_DIR / template_name / "template_manifest.json"


def _load_template_manifest(template_name: str):
    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not target or not target.exists():
        return None

    manifest_path = target / "template_manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "r") as handle:
                manifest = json.load(handle) or {}
            manifest.setdefault("template_name", template_name)
            manifest.setdefault("devices_meta", {})
            manifest["has_baseline"] = bool(manifest.get("has_baseline"))
            return manifest
        except Exception:
            pass

    devices_meta = {}
    for hostname_dir in sorted(target.iterdir()):
        if not hostname_dir.is_dir():
            continue
        logs_manifest = hostname_dir / "logs.json"
        commands = []
        if logs_manifest.exists():
            try:
                with open(logs_manifest, "r") as handle:
                    manifest = json.load(handle) or {}
                for name in manifest.get("logs", []):
                    base = os.path.splitext(name)[0]
                    commands.append(base.replace("_", " "))
            except Exception:
                commands = []
        if commands:
            devices_meta[hostname_dir.name] = commands

    has_baseline = False
    for hostname_dir in sorted(target.iterdir()):
        if hostname_dir.is_dir() and (hostname_dir / "config.json").exists():
            has_baseline = True
            break

    return {
        "template_name": template_name,
        "devices_meta": devices_meta,
        "has_baseline": has_baseline,
    }


def _template_command_key(command: str) -> str:
    return _normalize_text(command)


@app.route("/api/templates/<template_name>", methods=["GET"])
def api_get_template_details(template_name):
    if not template_name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Template not found."}), 404

    template_manifest = _load_template_manifest(template_name) or {}
    devices_meta = dict(template_manifest.get("devices_meta") or {})
    logs_by_command = {}
    has_baseline = bool(template_manifest.get("has_baseline"))
    for hostname_dir in sorted(target.iterdir()):
        if not hostname_dir.is_dir():
            continue
        logs_manifest = hostname_dir / "logs.json"
        commands = [
            _canonical_cli_command(command)
            for command in list(devices_meta.get(hostname_dir.name) or [])
        ]
        command_keys = {_template_command_key(command) for command in commands}
        if logs_manifest.exists():
            try:
                with open(logs_manifest, "r") as handle:
                    logs_payload = json.load(handle) or {}
                for name in logs_payload.get("logs", []):
                    base = os.path.splitext(name)[0]
                    cmd = _canonical_cli_command(base.replace("_", " "))
                    cmd_key = _template_command_key(cmd)
                    if cmd_key not in command_keys:
                        commands.append(cmd)
                        command_keys.add(cmd_key)
                    else:
                        cmd = next(
                            (
                                existing
                                for existing in commands
                                if _template_command_key(existing) == cmd_key
                            ),
                            cmd,
                        )
                    logs_by_command.setdefault(hostname_dir.name, {})[cmd] = name
            except Exception:
                pass
        if (hostname_dir / "config.json").exists():
            has_baseline = True
        if commands:
            devices_meta[hostname_dir.name] = commands

    return jsonify(
        {
            "status": "ok",
            "template": template_name,
            "devices_meta": devices_meta,
            "logs_by_command": logs_by_command,
            "has_baseline": has_baseline,
        }
    )


@app.route("/api/templates/save_setup", methods=["POST"])
def api_save_template_setup():
    data = request.get_json() or {}
    template_name = (data.get("template_name") or "").strip()
    devices_meta = data.get("devices_meta") or {}
    source_template_name = (data.get("source_template_name") or "").strip()

    if not template_name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400
    if not isinstance(devices_meta, dict) or not devices_meta:
        return jsonify({"status": "error", "message": "No devices provided."}), 400

    cleaned_devices = {}
    seen = set()
    for hostname, commands in devices_meta.items():
        safe_hostname = str(hostname or "").strip()
        if not safe_hostname:
            return jsonify({"status": "error", "message": "All devices must have a hostname."}), 400
        if safe_hostname.lower() in seen:
            return jsonify({"status": "error", "message": f'Duplicate hostname "{safe_hostname}".'}), 400
        seen.add(safe_hostname.lower())
        if not isinstance(commands, list) or not commands:
            return jsonify({"status": "error", "message": f'Device "{safe_hostname}" has no commands.'}), 400
        cleaned_devices[safe_hostname] = [str(cmd).strip() for cmd in commands if str(cmd).strip()]

    try:
        result = save_template_setup(
            str(BASE_DIR),
            template_name,
            cleaned_devices,
            source_template_name=source_template_name,
        )
        return jsonify({"status": "ok", **result})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/templates/import_logs_folder", methods=["POST"])
def api_import_template_logs_folder():
    data = request.get_json() or {}
    template_name = (data.get("template_name") or "").strip()
    source_dir = _expand_path(data.get("source_dir"))
    source_template_name = (data.get("source_template_name") or "").strip()
    strict = bool(data.get("strict"))
    devices_meta = data.get("devices_meta") or {}

    if not template_name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400
    if not source_dir or not os.path.isdir(source_dir):
        return jsonify({"status": "error", "message": "Selected logs folder was not found."}), 400
    if strict and (not isinstance(devices_meta, dict) or not devices_meta):
        return jsonify({"status": "error", "message": "Strict folder import requires template devices."}), 400
    if strict:
        cleaned_devices = {}
        seen = set()
        for hostname, commands in devices_meta.items():
            safe_hostname = str(hostname or "").strip()
            if not safe_hostname:
                return jsonify({"status": "error", "message": "All devices must have a hostname."}), 400
            if safe_hostname.lower() in seen:
                return jsonify({"status": "error", "message": f'Duplicate hostname "{safe_hostname}".'}), 400
            seen.add(safe_hostname.lower())
            if not isinstance(commands, list) or not commands:
                return jsonify({"status": "error", "message": f'Device "{safe_hostname}" has no commands.'}), 400
            cleaned_commands = [str(cmd).strip() for cmd in commands if str(cmd).strip()]
            if not cleaned_commands:
                return jsonify({"status": "error", "message": f'Device "{safe_hostname}" has no commands.'}), 400
            cleaned_devices[safe_hostname] = cleaned_commands
        devices_meta = cleaned_devices

    try:
        if strict:
            result = import_logs_folder_strict(
                str(BASE_DIR),
                template_name,
                source_dir,
                devices_meta,
                source_template_name=source_template_name,
            )
        else:
            result = import_template_from_logs_dir(
                str(BASE_DIR),
                template_name,
                source_dir,
                source_template_name=source_template_name,
            )
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify({"status": "ok", **result})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/admin/templates", methods=["DELETE"])
def api_admin_delete_templates():
    data = request.get_json() or {}
    name = data.get("name")
    delete_all = bool(data.get("all"))

    if delete_all:
        for entry in TEMPLATES_DIR.iterdir():
            if entry.is_dir():
                try:
                    shutil.rmtree(entry)
                except Exception:
                    pass
        return jsonify({"status": "ok", "message": "All templates deleted"})

    if not name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / name)
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Template not found."}), 404

    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Template '{name}' deleted"})


@app.route("/api/admin/results", methods=["GET"])
def api_admin_list_results():
    results = []
    docs_path = Path.home() / "Documents"
    if docs_path.exists():
        for classroom_dir in _safe_iterdir(docs_path):
            if not _safe_is_visible_dir(classroom_dir):
                continue
            for tutor_dir in _safe_iterdir(classroom_dir):
                if not _safe_is_visible_dir(tutor_dir):
                    continue
                for time_dir in _safe_iterdir(tutor_dir):
                    if not _safe_is_visible_dir(time_dir):
                        continue
                    for student_dir in _safe_iterdir(time_dir):
                        if not _safe_is_visible_dir(student_dir):
                            continue
                        results_dir = student_dir / "results"
                        if results_dir.is_dir():
                            results.append(
                                {
                                    "path": str(results_dir),
                                    "classroom": classroom_dir.name,
                                    "tutor_name": tutor_dir.name,
                                    "time_slot": time_dir.name,
                                    "exam_name": classroom_dir.name,
                                    "session_id": tutor_dir.name,
                                    "student_id": student_dir.name,
                                    "display": (
                                        f"{classroom_dir.name}/{tutor_dir.name}/"
                                        f"{time_dir.name}/{student_dir.name}"
                                    ),
                                }
                            )
    return jsonify({"status": "ok", "results": results})


@app.route("/api/admin/results", methods=["DELETE"])
def api_admin_delete_results():
    data = request.get_json() or {}
    path = data.get("path")
    delete_all = bool(data.get("all"))

    if delete_all:
        docs_dir = (Path.home() / "Documents").resolve()
        deleted = 0
        if docs_dir.exists():
            for classroom_dir in _safe_iterdir(docs_dir):
                if not _safe_is_visible_dir(classroom_dir):
                    continue
                for tutor_dir in _safe_iterdir(classroom_dir):
                    if not _safe_is_visible_dir(tutor_dir):
                        continue
                    for time_dir in _safe_iterdir(tutor_dir):
                        if not _safe_is_visible_dir(time_dir):
                            continue
                        for student_dir in _safe_iterdir(time_dir):
                            if not _safe_is_visible_dir(student_dir):
                                continue
                            results_dir = student_dir / "results"
                            if results_dir.is_dir():
                                try:
                                    shutil.rmtree(results_dir)
                                    deleted += 1
                                except Exception:
                                    pass
        return jsonify({"status": "ok", "message": f"All results deleted ({deleted})."})

    if not path:
        return jsonify({"status": "error", "message": "Missing path."}), 400

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, Path(path))
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Result not found."}), 404

    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Results deleted: {target}"})


@app.route("/api/admin/students", methods=["GET"])
def api_admin_list_students():
    return jsonify(
        {
            "status": "ok",
            "exams": _list_existing_exams(),
            "students": _list_existing_directories(),
            "sessions": _list_existing_sessions(),
        }
    )


@app.route("/api/admin/students", methods=["DELETE"])
def api_admin_delete_students():
    data = request.get_json() or {}
    path = data.get("path")
    if not path:
        return jsonify({"status": "error", "message": "Missing path."}), 400

    target = _safe_resolve_child(DOCS_DIR, Path(path))
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Path not found."}), 404

    if target == DOCS_DIR:
        return (
            jsonify(
                {"status": "error", "message": "Refusing to delete Documents root."}
            ),
            400,
        )

    if len(target.parts) >= len(DOCS_DIR.parts) + 3:
        relative = target.relative_to(DOCS_DIR)
        if len(relative.parts) == 3:
            session_dir = DOCS_DIR / relative.parts[0] / relative.parts[1]
            names = _load_session_student_names(session_dir)
            if relative.parts[2] in names:
                names.pop(relative.parts[2], None)
                _save_session_student_names(session_dir, names)

    _delete_engine_student_logs_for_docs_target(target)
    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Deleted {target}"})


@app.route("/api/admin/sync_mirror", methods=["POST"])
def api_admin_sync_mirror():
    """Remove engine/students dirs whose corresponding Documents folders no longer exist."""
    removed = []
    if not ENGINE_STUDENTS_DIR.exists():
        return jsonify({"status": "ok", "message": "Nothing to sync.", "removed": []})

    for classroom_dir in list(ENGINE_STUDENTS_DIR.iterdir()):
        if not classroom_dir.is_dir():
            continue
        docs_classroom = DOCS_DIR / classroom_dir.name
        if not docs_classroom.exists():
            shutil.rmtree(classroom_dir)
            removed.append(classroom_dir.name)
            continue
        for tutor_dir in list(classroom_dir.iterdir()):
            if not tutor_dir.is_dir():
                continue
            docs_tutor = docs_classroom / tutor_dir.name
            if not docs_tutor.exists():
                shutil.rmtree(tutor_dir)
                removed.append(f"{classroom_dir.name}/{tutor_dir.name}")
                continue
            for time_dir in list(tutor_dir.iterdir()):
                if not time_dir.is_dir():
                    continue
                docs_time = docs_tutor / time_dir.name
                if not docs_time.exists():
                    shutil.rmtree(time_dir)
                    removed.append(
                        f"{classroom_dir.name}/{tutor_dir.name}/{time_dir.name}"
                    )
                    continue
                for student_dir in list(time_dir.iterdir()):
                    if not student_dir.is_dir():
                        continue
                    docs_student = docs_time / student_dir.name
                    if not docs_student.exists():
                        shutil.rmtree(student_dir)
                        removed.append(
                            f"{classroom_dir.name}/{tutor_dir.name}/{time_dir.name}/{student_dir.name}"
                        )

                if time_dir.exists() and not any(time_dir.iterdir()):
                    time_dir.rmdir()
                if tutor_dir.exists() and not any(tutor_dir.iterdir()):
                    tutor_dir.rmdir()
        if classroom_dir.exists() and not any(classroom_dir.iterdir()):
            classroom_dir.rmdir()

    if removed:
        msg = f"Removed {len(removed)} orphaned mirror folder(s):\n" + "\n".join(
            removed
        )
    else:
        msg = "All mirror folders are in sync. Nothing to remove."
    return jsonify({"status": "ok", "message": msg, "removed": removed})


@app.route("/api/add_student", methods=["POST"])
def api_add_student():
    data = request.get_json() or {}
    session_path = _expand_path(data.get("session_path"))
    student_id = (data.get("student_id") or "").strip()
    student_name = (data.get("student_name") or "").strip()

    if not session_path or not student_id:
        return (
            jsonify(
                {"status": "error", "message": "Missing session_path or student_id."}
            ),
            400,
        )

    try:
        student_id = _normalize_directory_segment(student_id, "Student ID")
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    session_dir = Path(session_path)
    if not session_dir.exists() or not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, session_dir)
    if not target:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_dir = session_dir / student_id
    student_dir.mkdir(parents=True, exist_ok=True)
    names = _load_session_student_names(session_dir)
    if student_name:
        names[student_id] = student_name
    existing_name = names.get(student_id, "")
    _save_session_student_names(session_dir, names)

    parts = student_dir.parts
    classroom = parts[-4] if len(parts) >= 4 else ""
    tutor_name = parts[-3] if len(parts) >= 3 else ""
    time_slot = parts[-2] if len(parts) >= 2 else ""
    return jsonify(
        {
            "status": "ok",
            "message": f"Student directory created: {student_dir}",
            "path": str(student_dir),
            "classroom": classroom,
            "tutor_name": tutor_name,
            "time_slot": time_slot,
            "exam_name": classroom,
            "session_id": tutor_name,
            "student_id": student_id,
            "student_name": student_name or existing_name,
        }
    )


# --- Grading Logic ---


def _substitute_variables(pattern, variables):
    """
    Replace {{key}} in pattern with value from variables.
    """
    for key, val in variables.items():
        # strict replacement of {{key}}
        pattern = pattern.replace(f"{{{{{key}}}}}", str(val))
    return pattern


def _check_criteria(content, criteria, variables):
    """
    Check if content matches the criteria pattern.
    """
    pattern = criteria.get("pattern", "")
    # Substitute variables
    final_pattern = _substitute_variables(pattern, variables)

    # Try Regex search
    try:
        if re.search(final_pattern, content, re.MULTILINE | re.IGNORECASE):
            return True, final_pattern
    except re.error:
        pass

    # Check for simple string inclusion if regex fails or is simple
    if final_pattern in content:
        return True, final_pattern

    return False, final_pattern


from comparison_engine.compare_main import grading_pipeline


def _load_template_configs(template_name: str):
    template_dir = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not template_dir or not template_dir.is_dir():
        return None

    template_configs = {}
    for host_dir in sorted(template_dir.iterdir()):
        if not host_dir.is_dir():
            continue
        config_path = host_dir / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r") as handle:
                data = json.load(handle) or {}
            template_configs[host_dir.name] = normalize_parsed_config(data)
        except Exception:
            continue

    return template_configs


def _template_has_baseline(template_name: str) -> bool:
    manifest = _load_template_manifest(template_name) or {}
    if manifest.get("has_baseline"):
        return True
    template_configs = _load_template_configs(template_name) or {}
    return bool(template_configs)


def _grade_session_from_config(target_path: str, template_name: str):
    if not _template_has_baseline(template_name):
        return [], (
            f"Template '{template_name}' has device/command setup only. "
            "Upload template baseline logs before grading."
        )

    template_configs = _load_template_configs(template_name)
    if not template_configs:
        return [], f"No template configs found for '{template_name}'."

    results_summary = []
    target = Path(target_path)
    if not target.is_dir():
        return [], f"Target path {target_path} not found."

    def _student_has_collected_data(student_dir: Path) -> bool:
        if not student_dir.is_dir():
            return False
        for child in student_dir.iterdir():
            if not child.is_dir() or child.name == "results":
                continue
            if (child / "config.json").exists():
                return True
            try:
                if find_show_run_file(str(child)):
                    return True
            except Exception:
                continue
        return False

    skipped_students = []

    for student_entry in sorted(target.iterdir()):
        if not student_entry.is_dir():
            continue
        student_id = student_entry.name
        if not _student_has_collected_data(student_entry):
            skipped_students.append(student_id)
            continue
        student_results_dir_student = student_entry / "results"
        student_results_dir_student.mkdir(parents=True, exist_ok=True)

        summary = {
            "student_id": student_id,
            "template_name": template_name,
            "grading_mode": "strict",
            "hostnames_compared": [],
            "hostnames_missing_template": [],
            "hostnames_missing_show_run": [],
            "results": {},
        }

        for hostname, template_config in template_configs.items():
            template_config = normalize_parsed_config(template_config)
            student_host_dir = student_entry / hostname
            student_config_path = student_host_dir / "config.json"
            student_config = {}
            if student_config_path.exists():
                try:
                    with open(student_config_path, "r") as handle:
                        student_config = json.load(handle) or {}
                except Exception:
                    student_config = {}
            student_config = normalize_parsed_config(student_config)

            show_run_file = None
            if student_host_dir.is_dir():
                show_run_file = find_show_run_file(str(student_host_dir))
            if not show_run_file:
                summary["hostnames_missing_show_run"].append(hostname)

            results = compare_dicts(template_config, student_config)
            summary["hostnames_compared"].append(hostname)
            summary["results"][hostname] = results

            parsed_file = (
                student_results_dir_student / f"{hostname}_student_parsed.json"
            )
            with open(parsed_file, "w") as handle:
                json.dump(student_config, handle, indent=4)

            result_payload = {
                "student_id": student_id,
                "template_name": template_name,
                "grading_mode": "strict",
                "hostname": hostname,
                "student_show_run_file": show_run_file,
                "student_config_file": (
                    str(student_config_path) if student_config_path.exists() else None
                ),
                "student_parsed_file": str(parsed_file),
                "results": results,
            }

            student_result_file = (
                student_results_dir_student / f"{hostname}_result.json"
            )
            with open(student_result_file, "w") as handle:
                json.dump(result_payload, handle, indent=4)

        summary_file_student = student_results_dir_student / "summary.json"
        with open(summary_file_student, "w") as handle:
            json.dump(summary, handle, indent=4)

        results_summary.append(
            {"student_id": student_id, "status": "Graded", "template": template_name}
        )

    if not results_summary:
        return (
            [],
            "No collected student logs found in this session. Select a student and collect logs before grading.",
        )

    if skipped_students:
        return (
            results_summary,
            f"Grading completed for {len(results_summary)} student(s). "
            f"Skipped {len(skipped_students)} student(s) with no collected logs.",
        )

    return results_summary, "Grading completed."


@app.route("/api/grade", methods=["POST"])
def api_run_grading():
    data = request.get_json() or {}
    classroom = data.get("classroom") or data.get("exam_name")
    tutor_name = data.get("tutor_name") or data.get("session_id")
    time_slot = data.get("time_slot")
    target_path = data.get("target_path")
    template_name = data.get("template_name")
    include_reports = bool(data.get("include_reports"))

    if not target_path:
        return jsonify({"status": "error", "message": "Missing arguments"}), 400

    try:
        # Determine template to use
        available_templates = []
        if TEMPLATES_DIR.is_dir():
            available_templates = [
                p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()
            ]

        chosen_template = template_name
        if not chosen_template:
            if len(available_templates) == 1:
                chosen_template = available_templates[0]
            else:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Multiple templates available. Please select a template.",
                            "templates": available_templates,
                        }
                    ),
                    400,
                )

        summary_results, message = _grade_session_from_config(
            target_path, chosen_template
        )

        if not summary_results:
            return jsonify({"status": "error", "message": message}), 400

        payload = {
            "status": "success",
            "message": message,
            "results": summary_results,
        }
        reports = _build_session_reports(target_path)
        policy = load_grading_policy()
        _write_session_readable_results(target_path, reports, policy)
        if include_reports:
            payload["reports"] = reports
            payload["policy"] = policy

        return jsonify(payload)

    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Grading failed: {str(e)}"}), 500


# -------------------------------------------------
# ✅ Template Upload
# -------------------------------------------------
@app.route("/api/templates/upload", methods=["POST"])
def api_upload_templates():
    """
    Handles form-data upload from device_setup.html.
    Creates template config.json using parsing logic.
    """
    from comparison_wrapper import handle_template_upload

    form_data = request.form
    files = request.files

    print(f"\n[API][templates/upload] Uploading new template...")
    # Base dir for templates
    base_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        results = handle_template_upload(files, form_data, base_dir)
        if results.get("status") == "error":
            return jsonify(results), 400
        print(f"[API][templates/upload] Extraction successful: {results}")
        return jsonify({"status": "success", "results": results})
    except Exception as e:
        print(f"[API][templates/upload] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# -------------------------------------------------
# ✅ Run Flask
# -------------------------------------------------
def main():

    class _SuppressDevServerWarning(logging.Filter):
        def filter(self, record):
            message = record.getMessage()
            return (
                "This is a development server. Do not use it in a production deployment."
                not in message
            )

    logging.getLogger("werkzeug").addFilter(_SuppressDevServerWarning())
    print("[*] Running Flask server on http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, threaded=True)


if __name__ == "__main__":
    main()
