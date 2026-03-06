import json
import os

from comparator import compare_dicts
from parser import parse_showrun

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


def find_show_run_file(device_dir):
    """Find the most likely show run file in a device command directory."""
    candidate_files = []
    for entry in os.listdir(device_dir):
        full_path = os.path.join(device_dir, entry)
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


def collect_student_device_showruns(student_folder, student_id):
    """Collect show run path for each device under students/<student_id>/<device>/..."""
    student_dir = os.path.join(student_folder, student_id)
    if not os.path.isdir(student_dir):
        print(f"Error: student directory '{student_dir}' not found.")
        return None

    device_showruns = {}
    for entry in sorted(os.listdir(student_dir)):
        device_dir = os.path.join(student_dir, entry)
        if not os.path.isdir(device_dir):
            continue

        show_run_file = find_show_run_file(device_dir)
        if show_run_file is None:
            print(f"Warning: no show run file found for {student_id}/{entry}.")
            continue

        device_showruns[entry] = show_run_file

    return device_showruns


def compare_student_devices(
    student_id, profile_name, templates, student_folder, results_folder
):
    """Compare each student device show run with the matching teacher device template."""
    print(f"\n--- Grading student {student_id} (profile: {profile_name}) ---")

    student_showruns = collect_student_device_showruns(student_folder, student_id)
    if not student_showruns:
        print(f"No device show run files found for student {student_id}.")
        return False

    student_results_dir = os.path.join(results_folder, student_id)
    os.makedirs(student_results_dir, exist_ok=True)

    summary = {
        "student_id": student_id,
        "template_profile": profile_name,
        "devices_compared": [],
        "devices_missing_template": [],
        "devices_missing_show_run": [],
        "results": {},
    }

    for device_name, template_config in templates.items():
        if template_config is None:
            summary["devices_missing_template"].append(device_name)
            continue

        if device_name not in student_showruns:
            summary["devices_missing_show_run"].append(device_name)
            continue

        student_config = parse_showrun(student_showruns[device_name])
        comparison_results = compare_dicts(template_config, student_config)

        summary["devices_compared"].append(device_name)
        summary["results"][device_name] = comparison_results

        device_result_file = os.path.join(
            student_results_dir, f"{device_name}_result.json"
        )
        with open(device_result_file, "w") as result_file:
            json.dump(
                {
                    "student_id": student_id,
                    "template_profile": profile_name,
                    "device": device_name,
                    "teacher_template_device": device_name,
                    "student_show_run_file": student_showruns[device_name],
                    "results": comparison_results,
                },
                result_file,
                indent=4,
            )
        print(f"Compared {device_name}: result saved to {device_result_file}")

    summary_file = os.path.join(student_results_dir, "summary.json")
    with open(summary_file, "w") as summary_handle:
        json.dump(summary, summary_handle, indent=4)

    print(f"Summary saved to {summary_file}")
    return True
