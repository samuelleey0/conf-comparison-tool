"""
Readable result generation for comparison reports.

This script turns machine-readable summary/report data into readableResult.txt
files for student result folders. The current GUI/server path calls
write_readable_result_from_report(), while the bottom CLI helpers can process
older summary.json session folders manually.
"""
import os
import json

SUMMARY_FILE = "summary.json"
OUTPUT_FILE = "readableResult.txt"
MAJOR_KEYWORDS = ["ACL", "NAT", "ROUTING", "USER", "PPP"]


def is_major_error(outcome_code):
    """Infer whether an older outcome code should count as a major error."""
    if not outcome_code:
        return False
    outcome_code = str(outcome_code).upper()
    return any(k in outcome_code for k in MAJOR_KEYWORDS)


def _format_value(value):
    """Convert expected/actual values into readable text for result output."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _humanize_feature(feature):
    """Convert internal feature paths into labels suitable for student feedback."""
    text = str(feature or "Unknown")
    replacements = {
        "show_running_config": "Configuration",
        "verification": "Verification",
        "interfaces": "Interfaces",
        "access_lists": "Access lists",
        "routing": "Routing",
        "static_routes": "Static routes",
        "dhcp_pools": "DHCP pools",
        "switching": "Switching",
        "switchport_mode": "Switchport mode",
        "access_groups": "ACL applied to interface",
        "shutdown": "Interface shutdown setting",
        "applied": "Applied setting",
    }
    parts = [part for part in text.split(".") if part]
    readable = []
    for part in parts:
        if part in replacements:
            readable.append(replacements[part])
        elif "/" in part or any(char.isdigit() for char in part):
            readable.append(part.replace("_", " "))
        else:
            readable.append(part.replace("_", " ").title())
    return " > ".join(readable) if readable else "Unknown"


def format_error(device, item, severity):
    """Build one readable error block for a failed comparison item."""
    feature = item.get("feature", "Unknown")
    expected = _format_value(item.get("expected"))
    actual = _format_value(item.get("actual"))
    outcome = item.get("rule_code") or item.get("outcome_code") or "UNKNOWN"

    message = f"[{severity}] {device} - {_humanize_feature(feature)}"

    if expected is not None and actual is not None:
        message += f"\n  → Expected: {expected}\n  → Actual: {actual}"
    elif expected is not None:
        message += f"\n  → Expected: {expected}"
    elif actual is not None:
        message += f"\n  → Actual: {actual}"

    message += f"\n  → Code: {outcome}"

    return message


def _iter_summary_errors(summary_data):
    """Yield non-correct items from an older summary.json structure."""
    results = summary_data.get("results", {})
    for device, checks in results.items():
        for item in checks:
            status = item.get("status")
            if status == "correct":
                continue
            yield device, item


def process_summary(summary_path):
    """
    Read an older summary.json file and count major/minor marking errors.

    Used by the manual CLI batch flow below, not by the main Electron path.
    """
    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    total_minor = 0
    total_major = 0
    errors = []

    for device, item in _iter_summary_errors(data):
        severity = str(item.get("severity") or "").upper()
        if severity not in {"MAJOR", "MINOR"}:
            outcome = item.get("outcome_code", "")
            severity = "MAJOR" if is_major_error(outcome) else "MINOR"

        if severity == "MAJOR":
            total_major += 1
        else:
            total_minor += 1

        error_msg = format_error(device, item, severity)
        errors.append(error_msg)

    return total_minor, total_major, errors


def determine_result(minor, major, major_threshold=1, minor_threshold=5):
    """Return PASS/FAIL from major and minor counts using configurable thresholds."""
    return "FAIL" if (major >= major_threshold or minor >= minor_threshold) else "PASS"


def write_results(student_path, result, errors, minor=0, major=0, output_name=OUTPUT_FILE):
    """
    Write readableResult.txt under a student's results folder.

    Both the current report-based path and the older summary-based CLI path use
    this final writer.
    """
    results_folder_path = os.path.join(student_path, "results")
    os.makedirs(results_folder_path, exist_ok=True)

    output_file = os.path.join(results_folder_path, output_name)

    minor_errors = [e for e in errors if "[MINOR]" in e]
    major_errors = [e for e in errors if "[MAJOR]" in e]

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"Overall result: {result}\n")
        f.write(f"Major errors: {major}\n")
        f.write(f"Minor errors: {minor}\n\n")

        if result == "PASS":
            f.write("This student passed based on the current marking rules.\n")
            if errors:
                f.write("There are still items listed below that may be useful for feedback.\n\n")

        else:
            f.write(
                "This student did not meet the pass requirement. "
                "A major error causes an automatic fail, or five minor errors also cause a fail.\n\n"
            )

        f.write("Major errors\n")
        if major_errors:
            for err in major_errors:
                f.write(f"- {err}\n\n")
        else:
            f.write("None\n\n")

        f.write("Minor errors\n")
        if minor_errors:
            for err in minor_errors:
                f.write(f"- {err}\n\n")
        else:
            f.write("None\n")

    return output_file


def write_readable_result_from_report(student_path, report, policy=None):
    """
    Convert a current comparison report dictionary into readableResult.txt.

    Called by server.py after grading/comparison completes for a student.
    """
    policy = policy or {}
    summary = report.get("summary") or {}
    major = int(summary.get("major") or 0)
    minor = int(summary.get("minor") or 0)
    major_threshold = int(policy.get("major_threshold") or 1)
    minor_threshold = int(policy.get("minor_threshold") or 5)
    if "pass" in report:
        result = "PASS" if report.get("pass") else "FAIL"
    else:
        result = determine_result(minor, major, major_threshold, minor_threshold)

    errors = []
    for item in report.get("items") or []:
        if item.get("status") in {"correct", "skipped"}:
            continue
        if not item.get("counts_toward_marking", True):
            continue
        severity = str(item.get("severity") or "minor").upper()
        device = item.get("hostname") or "Unknown device"
        errors.append(format_error(device, item, severity))

    return write_results(student_path, result, errors, minor, major)


def choose_session(base_path):
    """Prompt the CLI user to select one session folder under a base path."""
    sessions = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]

    if not sessions:
        print("[ERROR] No session folders found.")
        return None

    print("Available Sessions:")
    for i, session in enumerate(sessions, 1):
        print(f"{i}. {session}")

    while True:
        choice = input("Select a session (number): ")

        if choice.isdigit() and 1 <= int(choice) <= len(sessions):
            return os.path.join(base_path, sessions[int(choice) - 1])
        else:
            print("Invalid choice. Try again.")


def process_students(session_path):
    """Process every student in a CLI-selected session using summary.json files."""
    print(f"\n[INFO] Processing session: {session_path}\n")

    for student_id in os.listdir(session_path):
        student_path = os.path.join(session_path, student_id)

        if not os.path.isdir(student_path):
            continue

        summary_path = os.path.join(student_path, "results", SUMMARY_FILE)

        if not os.path.exists(summary_path):
            print(f"[WARNING] No summary.json for {student_id}")
            continue

        print(f"[INFO] Checking {student_id}...")

        minor, major, errors = process_summary(summary_path)
        result = determine_result(minor, major)

        write_results(student_path, result, errors, minor, major)

        print(f"[RESULT] {student_id}: {result} (Minor={minor}, Major={major})\n")


def main():
    """CLI helper for manually regenerating readable results from old summaries."""
   
    base_path = os.path.join(os.path.expanduser("~"), "Documents", "B408", "MarkTee")

    if not os.path.exists(base_path):
        print("[ERROR] Base directory not found.")
        return

    session_path = choose_session(base_path)

    if session_path:
        process_students(session_path)


if __name__ == "__main__":
    main()
