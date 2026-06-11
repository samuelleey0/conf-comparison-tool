#!/usr/bin/env python3
"""Format raw student Cisco logs into folders accepted by the marking system.

This is a standalone testing helper. Put raw logs in a source folder, choose an
output folder, then move/use the generated output with the marking app.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

IGNORED_NAMES = {
    ".ds_store",
    "config.json",
    "logs.json",
    "summary.json",
    "readableresult.txt",
}

COMMAND_ALIASES = {
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
    "show_ip_eigrp": ["show ip eigrp", "show_ip_eigrp", "sh ip eigrp"],
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
    "show_ip_ospf": ["show ip ospf", "show_ip_ospf", "sh ip ospf"],
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
}


def clean_segment(value, fallback):
    value = str(value or "").strip() or fallback
    value = re.sub(r'[<>:"/\\|?*\x00]+', "-", value)
    value = re.sub(r"\s+", " ", value).strip().strip(".")
    return value or fallback


def normalize_text(value):
    lowered = value.lower()
    for char in ["_", "-", ".", "(", ")", "[", "]"]:
        lowered = lowered.replace(char, " ")
    return " ".join(lowered.split())


def detect_command_from_name(path):
    name = normalize_text(path.name)
    candidates = []
    for command, aliases in COMMAND_ALIASES.items():
        for alias in aliases:
            candidates.append((command, alias))
    candidates.sort(key=lambda item: len(item[1]), reverse=True)
    for command, alias in candidates:
        if alias in name:
            return command
    return None


def extract_hostname_from_show_run(path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = re.match(r"^\s*hostname\s+(\S+)\s*$", line)
                if match:
                    return match.group(1)
    except OSError:
        return None
    return None


def is_log_file(path):
    return (
        path.is_file()
        and not path.name.startswith(".")
        and path.name.lower() not in IGNORED_NAMES
        and path.suffix.lower() in {"", ".txt", ".log"}
    )


def direct_logs(folder):
    return sorted(
        [entry for entry in folder.iterdir() if is_log_file(entry)],
        key=lambda item: item.name.lower(),
    )


def logs_for_device_folder(folder):
    nested = folder / "logs"
    if nested.is_dir():
        return direct_logs(nested)
    return direct_logs(folder)


def infer_hostname_from_files(files, folder_name):
    for path in files:
        if detect_command_from_name(path) == "show_running_config":
            hostname = extract_hostname_from_show_run(path)
            if hostname:
                return hostname
    return folder_name or "UNKNOWN"


def discover_students(source):
    students = {}
    student_dirs = [
        entry
        for entry in source.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    ]

    for student_dir in sorted(student_dirs, key=lambda item: item.name.lower()):
        devices = {}
        for child in sorted(student_dir.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            files = logs_for_device_folder(child)
            if files:
                hostname = clean_segment(child.name, "UNKNOWN")
                devices[hostname] = files

        loose_files = direct_logs(student_dir)
        if loose_files:
            hostname = clean_segment(
                infer_hostname_from_files(loose_files, "UNKNOWN"), "UNKNOWN"
            )
            devices.setdefault(hostname, []).extend(loose_files)

        if devices:
            students[clean_segment(student_dir.name, "student")] = devices

    if students:
        return students

    devices = {}
    loose_files = direct_logs(source)
    if loose_files:
        hostname = clean_segment(
            infer_hostname_from_files(loose_files, "UNKNOWN"), "UNKNOWN"
        )
        devices[hostname] = loose_files
    for child in sorted(source.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir() and not child.name.startswith("."):
            files = logs_for_device_folder(child)
            if files:
                devices[clean_segment(child.name, "UNKNOWN")] = files
    if devices:
        return {clean_segment(source.name, "student"): devices}
    return {}


def load_project_parser(repo):
    if not repo:
        return None
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repo path not found: {repo_path}")
    sys.path.insert(0, str(repo_path))
    from comparison_engine.parser import parse_device_logs_with_report

    return parse_device_logs_with_report


def copy_logs(files, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    used = set()
    for source in files:
        name = source.name
        stem = source.stem
        suffix = source.suffix
        index = 2
        while name.lower() in used:
            name = f"{stem}_{index}{suffix}"
            index += 1
        used.add(name.lower())
        target = target_dir / name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def write_student_config_json(target_dir, copied, parse_device_logs_with_report):
    if not parse_device_logs_with_report:
        return False
    parsed_config, _skipped_logs = parse_device_logs_with_report(
        [str(path) for path in copied]
    )
    with (target_dir / "config.json").open("w", encoding="utf-8") as handle:
        json.dump(parsed_config, handle, indent=4)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Raw log folder.")
    parser.add_argument("output", help="Output folder for formatted marking files.")
    parser.add_argument("--classroom", required=True)
    parser.add_argument("--tutor", required=True)
    parser.add_argument("--exam-time", required=True)
    parser.add_argument(
        "--repo",
        help="Optional path to conf-comparison-tool. Enables config.json generation.",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"Source folder not found: {source}")

    parse_device_logs_with_report = load_project_parser(args.repo)
    students = discover_students(source)
    if not students:
        raise SystemExit("No raw logs found.")

    session = output / clean_segment(args.classroom, "classroom")
    session = session / clean_segment(args.tutor, "tutor")
    session = session / clean_segment(args.exam_time, "exam-time")

    result = {"session": str(session), "students": []}
    for student_id, devices in students.items():
        student_info = {"student_id": student_id, "devices": []}
        for hostname, files in devices.items():
            target_dir = session / student_id / hostname
            copied = copy_logs(files, target_dir)
            has_config = write_student_config_json(
                target_dir, copied, parse_device_logs_with_report
            )
            student_info["devices"].append(
                {
                    "hostname": hostname,
                    "log_count": len(copied),
                    "config_json": has_config,
                    "path": str(target_dir),
                }
            )
        result["students"].append(student_info)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
