import json
import os

from comparator import compare_dicts
from comparator import normalize_config_with_scheme
from parser import normalize_parsed_config
from parser import parse_device_logs

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

    student_showruns = collect_student_hostname_showruns(student_folder, student_id)
    if not student_showruns:
        print(f"No hostname show run files found for student {student_id}.")
        return False

    student_logs = collect_student_hostname_logs(student_folder, student_id)
    if not student_logs:
        print(f"No log files found for student {student_id}.")
        return False

    student_results_dir = os.path.join(results_folder, student_id)
    os.makedirs(student_results_dir, exist_ok=True)

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
            continue

        student_config = normalize_parsed_config(
            parse_device_logs(student_logs[hostname])
        )

        # Save parsed student config for debugging/traceability.
        parsed_debug_file = os.path.join(
            student_results_dir, f"{hostname}_student_parsed.json"
        )
        with open(parsed_debug_file, "w") as parsed_handle:
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
                    "student_show_run_file": student_showruns[hostname],
                    "student_parsed_file": parsed_debug_file,
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

    print(f"Summary saved to {summary_file}")
    return True
