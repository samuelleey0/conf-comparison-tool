import json
import os

from .comparator import compare_dicts
from .comparator import normalize_config_with_scheme
from .parser import detect_command_type
from .parser import normalize_parsed_config
from .parser import parse_device_logs
import ipaddress

SHOW_RUN_TOKENS = [
    "showrun",
    "show_run",
    "show-run",
    "show run",
    "shrun",
    "sh_run",
    "sh run",
]


def normalize_text(value):
    """Normalize text for flexible filename matching."""
    lowered = value.lower()
    for char in ["_", "-", ".", "(", ")", "[", "]"]:
        lowered = lowered.replace(char, " ")
    return " ".join(lowered.split())


def find_show_run_file(logs_dir):
    """Find show run file in a logs directory."""
    if not os.path.isdir(logs_dir):
        return None

    candidate_files = []
    for entry in os.listdir(logs_dir):
        full_path = os.path.join(logs_dir, entry)
        if os.path.isfile(full_path):
            normalized = normalize_text(entry)
            if any(token in normalized for token in SHOW_RUN_TOKENS):
                candidate_files.append(full_path)

    if not candidate_files:
        return None

    # Prefer files that look exactly like show run first.
    def score(path):
        name = normalize_text(os.path.basename(path))
        if "show run" in name:
            return 0
        if "showrun" in name:
            return 1
        if "sh run" in name:
            return 2
        return 3

    candidate_files.sort(key=score)
    return candidate_files[0]


def collect_student_hostname_showruns(student_folder, student_id):
    """Collect show run path for each hostname under students/<student_id>/<hostname>/..."""
    student_dir = os.path.join(student_folder, student_id)
    if not os.path.isdir(student_dir):
        print(f"Error: student directory '{student_dir}' not found.")
        return None

    hostname_showruns = {}
    for entry in sorted(os.listdir(student_dir)):
        hostname_dir = os.path.join(student_dir, entry)
        if not os.path.isdir(hostname_dir):
            continue

        show_run_file = find_show_run_file(hostname_dir)
        if show_run_file is None:
            print(f"Warning: no show run file found for {student_id}/{entry}/.")
            continue

        hostname_showruns[entry] = show_run_file

    return hostname_showruns


def collect_student_hostname_logs(student_folder, student_id):
    """Collect all log files for each hostname under students/<student_id>/<hostname>/."""
    student_dir = os.path.join(student_folder, student_id)
    if not os.path.isdir(student_dir):
        print(f"Error: student directory '{student_dir}' not found.")
        return None

    hostname_logs = {}
    for entry in sorted(os.listdir(student_dir)):
        hostname_dir = os.path.join(student_dir, entry)
        if not os.path.isdir(hostname_dir):
            continue

        file_paths = []
        for child in sorted(os.listdir(hostname_dir)):
            full_path = os.path.join(hostname_dir, child)
            if os.path.isfile(full_path):
                file_paths.append(full_path)

        if file_paths:
            hostname_logs[entry] = file_paths

    return hostname_logs


def _load_student_config(student_host_dir, log_files):
    """
    Load parsed student config from config.json if present, otherwise parse logs.
    Returns (config_dict_or_none, config_path_or_none).
    """
    config_path = os.path.join(student_host_dir, "config.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r") as handle:
                data = json.load(handle) or {}
            return normalize_parsed_config(data), config_path
        except Exception:
            return None, config_path

    if not log_files:
        return None, None

    try:
        parsed = parse_device_logs(log_files)
        return normalize_parsed_config(parsed), None
    except Exception:
        return None, None


def compare_student_hostnames(
    student_id,
    template_name,
    templates,
    student_folder,
    results_folder,
    scheme_mode="strict",
    template_scheme=None,
    student_scheme=None,
):
    """Compare each student hostname show run with the matching teacher hostname template."""
    print(f"\n--- Grading student {student_id} (template: {template_name}) ---")

    student_showruns = (
        collect_student_hostname_showruns(student_folder, student_id) or {}
    )
    student_logs = collect_student_hostname_logs(student_folder, student_id)
    if not student_logs:
        print(f"No log files found for student {student_id}.")
        return False

    student_results_dir = os.path.join(results_folder, student_id)
    os.makedirs(student_results_dir, exist_ok=True)
    student_results_dir_student = os.path.join(student_folder, student_id, "results")
    os.makedirs(student_results_dir_student, exist_ok=True)

    summary = {
        "student_id": student_id,
        "template_name": template_name,
        "grading_mode": scheme_mode,
        "hostnames_compared": [],
        "hostnames_missing_template": [],
        "hostnames_missing_show_run": [],
        "results": {},
    }

    for hostname, template_config in templates.items():
        if template_config is None:
            summary["hostnames_missing_template"].append(hostname)
            continue

        template_config = normalize_parsed_config(template_config)

        if hostname not in student_showruns:
            summary["hostnames_missing_show_run"].append(hostname)

        student_host_dir = os.path.join(student_folder, student_id, hostname)
        student_config, student_config_path = _load_student_config(
            student_host_dir, student_logs.get(hostname, [])
        )
        if student_config is None:
            print(
                f"Warning: no config found for {student_id}/{hostname}. Marking all as missing."
            )
            comparison_results = compare_dicts(template_config, {})
            summary["hostnames_compared"].append(hostname)
            summary["results"][hostname] = comparison_results

            hostname_result_file = os.path.join(
                student_results_dir, f"{hostname}_result.json"
            )
            with open(hostname_result_file, "w") as result_file:
                json.dump(
                    {
                        "student_id": student_id,
                        "template_name": template_name,
                        "grading_mode": scheme_mode,
                        "hostname": hostname,
                        "student_show_run_file": student_showruns.get(hostname),
                        "student_config_file": student_config_path,
                        "student_parsed_file": None,
                        "results": comparison_results,
                    },
                    result_file,
                    indent=4,
                )

            hostname_result_file_student = os.path.join(
                student_results_dir_student, f"{hostname}_result.json"
            )
            with open(hostname_result_file_student, "w") as result_file:
                json.dump(
                    {
                        "student_id": student_id,
                        "template_name": template_name,
                        "grading_mode": scheme_mode,
                        "hostname": hostname,
                        "student_show_run_file": student_showruns.get(hostname),
                        "student_config_file": student_config_path,
                        "student_parsed_file": None,
                        "results": comparison_results,
                    },
                    result_file,
                    indent=4,
                )
            continue

        # Save parsed student config for debugging/traceability.
        parsed_debug_file = os.path.join(
            student_results_dir, f"{hostname}_student_parsed.json"
        )
        with open(parsed_debug_file, "w") as parsed_handle:
            json.dump(student_config, parsed_handle, indent=4)
        parsed_debug_file_student = os.path.join(
            student_results_dir_student, f"{hostname}_student_parsed.json"
        )
        with open(parsed_debug_file_student, "w") as parsed_handle:
            json.dump(student_config, parsed_handle, indent=4)

        compare_template = template_config
        compare_student = student_config
        if scheme_mode == "scheme-aware":
            compare_template = normalize_config_with_scheme(
                compare_template, template_scheme
            )
            compare_student = normalize_config_with_scheme(
                compare_student, student_scheme
            )

        comparison_results = compare_dicts(compare_template, compare_student)

        summary["hostnames_compared"].append(hostname)
        summary["results"][hostname] = comparison_results

        hostname_result_file = os.path.join(
            student_results_dir, f"{hostname}_result.json"
        )
        with open(hostname_result_file, "w") as result_file:
            json.dump(
                {
                    "student_id": student_id,
                    "template_name": template_name,
                    "grading_mode": scheme_mode,
                    "hostname": hostname,
                    "student_show_run_file": student_showruns.get(hostname),
                    "student_config_file": student_config_path,
                    "student_parsed_file": parsed_debug_file,
                    "results": comparison_results,
                },
                result_file,
                indent=4,
            )
        hostname_result_file_student = os.path.join(
            student_results_dir_student, f"{hostname}_result.json"
        )
        with open(hostname_result_file_student, "w") as result_file:
            json.dump(
                {
                    "student_id": student_id,
                    "template_name": template_name,
                    "grading_mode": scheme_mode,
                    "hostname": hostname,
                    "student_show_run_file": student_showruns.get(hostname),
                    "student_config_file": student_config_path,
                    "student_parsed_file": parsed_debug_file_student,
                    "results": comparison_results,
                },
                result_file,
                indent=4,
            )
        print(
            f"Compared {hostname}: result saved to {hostname_result_file} | "
            f"parsed debug saved to {parsed_debug_file}"
        )

    summary_file = os.path.join(student_results_dir, "summary.json")
    with open(summary_file, "w") as summary_handle:
        json.dump(summary, summary_handle, indent=4)
    summary_file_student = os.path.join(student_results_dir_student, "summary.json")
    with open(summary_file_student, "w") as summary_handle:
        json.dump(summary, summary_handle, indent=4)

    print(f"Summary saved to {summary_file}")
    return True


def load_template_manifest(template_folder, template_name, hostname):
    """Load per-hostname template manifest (teacher command requirements)."""
    if not template_folder:
        return {}

    manifest_path = os.path.join(template_folder, template_name, hostname, "logs.json")
    if not os.path.exists(manifest_path):
        return {}

    try:
        with open(manifest_path, "r") as handle:
            return json.load(handle) or {}
    except Exception:
        return {}


def collect_detected_command_types(file_paths):
    """Detect parsed command types from a list of log files."""
    detected = []
    for path in file_paths:
        command_type = detect_command_type(path)
        if command_type and command_type not in detected:
            detected.append(command_type)
    return detected


def find_dhcp_excluded_conflicts(parsed_config):
    """Return assigned DHCP IPs that fall inside configured excluded ranges."""
    show_run = parsed_config.get("show_running_config", {}) or {}
    verification = parsed_config.get("verification", {}) or {}

    excluded_ranges = show_run.get("dhcp_excluded", [])
    binding_data = verification.get("show_ip_dhcp_binding", {}) or {}
    assigned_ips = binding_data.get("assigned_ips", [])

    excluded_networks = []
    for item in excluded_ranges:
        try:
            start_ip = ipaddress.ip_address(item.get("start"))
            end_ip = ipaddress.ip_address(item.get("end"))
            excluded_networks.append((start_ip, end_ip))
        except Exception:
            continue

    conflicts = []
    for ip_text in assigned_ips:
        try:
            assigned_ip = ipaddress.ip_address(ip_text)
        except Exception:
            continue

        for start_ip, end_ip in excluded_networks:
            if start_ip <= assigned_ip <= end_ip:
                conflicts.append(ip_text)
                break

    return sorted(set(conflicts))
