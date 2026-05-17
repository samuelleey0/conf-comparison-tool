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


def _format_status_label(item, severity):
    status = str(item.get("status") or "mismatch")
    is_verification = (
        item.get("layer") == "verification"
        or str(item.get("feature") or "").startswith("verification.")
    )

    if item.get("status") == "skipped":
        return "skipped - rule disabled"
    if is_verification and (
        item.get("verification_rule_deduplicated") or item.get("deduplicated")
    ):
        return f"{status} - already counted"
    if item.get("rule_deduplicated"):
        return f"{status} - {severity.lower()} not scored, same rule already counted"
    if not item.get("counts_toward_marking", True):
        return f"{status} - not scored"
    return f"{status} - {severity.lower()}"


def _format_unscored_note(item):
    is_verification = (
        item.get("layer") == "verification"
        or str(item.get("feature") or "").startswith("verification.")
    )
    if is_verification and item.get("verification_rule_deduplicated"):
        ref = item.get("block_name") or item.get("layer1_ref") or "this block"
        return f"Same verification rule already scored for {ref}"
    if is_verification and item.get("deduplicated"):
        ref = item.get("layer1_ref") or item.get("block_name") or "related config error"
        if _is_vlan_scheme_ref(ref):
            return "Counted under: related VLAN or switchport configuration error"
        return f"Counted under: {str(ref).replace('show_running_config.', '')}"
    if item.get("rule_deduplicated"):
        rule_code = item.get("rule_code") or item.get("rule_id") or "matched rule"
        return f"Same rule {rule_code} already scored on another device"
    if item.get("status") == "skipped":
        rule_code = item.get("rule_code") or item.get("rule_id") or "matched rule"
        return f"Hidden from scoring because {rule_code} is disabled in Rubric Rules"
    if not item.get("counts_toward_marking", True):
        return "Not counted toward marking"
    return ""


def format_unscored_finding(device, item):
    """Build one readable block for a GUI-visible finding that did not affect marks."""
    severity = str(item.get("severity") or "minor").upper()
    feature = item.get("feature", "Unknown")
    expected = _format_value(item.get("expected"))
    actual = _format_value(item.get("actual"))
    outcome = item.get("rule_code") or item.get("outcome_code") or "UNKNOWN"
    status_label = _format_status_label(item, severity)
    note = _format_unscored_note(item)

    message = f"[UNSCORED - {severity}] {device} - {_humanize_feature(feature)}"
    message += f"\n  → Status: {status_label}"
    if note:
        message += f"\n  → Note: {note}"

    if expected is not None and actual is not None:
        message += f"\n  → Expected: {expected}\n  → Actual: {actual}"
    elif expected is not None:
        message += f"\n  → Expected: {expected}"
    elif actual is not None:
        message += f"\n  → Actual: {actual}"

    message += f"\n  → Code: {outcome}"
    return message


def _is_vlan_scheme_ref(ref):
    return str(ref or "") == "show_running_config.__vlan_scheme__"


def _find_parent_error_index(unscored_item, scored_entries):
    layer1_ref = str(unscored_item.get("layer1_ref") or "")
    rule_code = str(unscored_item.get("rule_code") or unscored_item.get("outcome_code") or "")
    rule_id = str(unscored_item.get("rule_id") or "")
    hostname = str(unscored_item.get("hostname") or "")

    if unscored_item.get("rule_deduplicated") or unscored_item.get("status") == "skipped":
        for index, entry in enumerate(scored_entries):
            parent = entry["item"]
            parent_rule_code = str(parent.get("rule_code") or parent.get("outcome_code") or "")
            parent_rule_id = str(parent.get("rule_id") or "")
            if rule_code and parent_rule_code == rule_code:
                return index
            if rule_id and parent_rule_id == rule_id:
                return index

    if layer1_ref and not _is_vlan_scheme_ref(layer1_ref):
        for index, entry in enumerate(scored_entries):
            parent_ref = str(entry["item"].get("layer1_ref") or "")
            if not parent_ref:
                continue
            if (
                parent_ref == layer1_ref
                or parent_ref.startswith(layer1_ref)
                or layer1_ref.startswith(parent_ref)
            ):
                return index

    if _is_vlan_scheme_ref(layer1_ref):
        vlan_tokens = (
            ".access_vlan",
            ".switchport_mode",
            ".trunk_native_vlan",
            ".trunk_allowed_vlans",
            ".Vlan.interface",
            ".subinterface",
        )
        for index, entry in enumerate(scored_entries):
            parent = entry["item"]
            parent_feature = str(parent.get("feature") or "")
            parent_host = str(parent.get("hostname") or "")
            if hostname and parent_host != hostname:
                continue
            if any(token in parent_feature for token in vlan_tokens):
                return index
        for index, entry in enumerate(scored_entries):
            parent_feature = str(entry["item"].get("feature") or "")
            if any(token in parent_feature for token in vlan_tokens):
                return index

    if rule_code:
        for index, entry in enumerate(scored_entries):
            parent_rule_code = str(
                entry["item"].get("rule_code") or entry["item"].get("outcome_code") or ""
            )
            if parent_rule_code == rule_code:
                return index

    if hostname:
        for index, entry in enumerate(scored_entries):
            if str(entry["item"].get("hostname") or "") == hostname:
                return index

    return None


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


def write_results(
    student_path,
    result,
    errors,
    minor=0,
    major=0,
    output_name=OUTPUT_FILE,
    additional_findings=None,
    grouped_errors=None,
):
    """
    Write readableResult.txt under a student's results folder.

    Both the current report-based path and the older summary-based CLI path use
    this final writer.
    """
    results_folder_path = os.path.join(student_path, "results")
    os.makedirs(results_folder_path, exist_ok=True)

    output_file = os.path.join(results_folder_path, output_name)
    additional_findings = additional_findings or []
    grouped_errors = grouped_errors or {}

    minor_errors = [e for e in errors if "[MINOR]" in e]
    major_errors = [e for e in errors if "[MAJOR]" in e]

    def _write_error_block(handle, err):
        handle.write(f"- {err}\n")
        for related in grouped_errors.get(err, []):
            handle.write(f"  Related unscored finding:\n")
            related_lines = related.splitlines()
            if related_lines:
                handle.write(f"    - {related_lines[0]}\n")
                for line in related_lines[1:]:
                    handle.write(f"      {line}\n")
            handle.write("\n")
        handle.write("\n")

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
                _write_error_block(f, err)
        else:
            f.write("None\n\n")

        f.write("Minor errors\n")
        if minor_errors:
            for err in minor_errors:
                _write_error_block(f, err)
        else:
            f.write("None\n")

        f.write("\nAdditional unscored findings\n")
        if additional_findings:
            f.write(
                "These were visible in the GUI but did not change the major/minor totals because "
                "they were duplicate, verification, or disabled-rule findings.\n\n"
            )
            for finding in additional_findings:
                f.write(f"- {finding}\n\n")
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
    scored_entries = []
    additional_findings = []
    for item in report.get("items") or []:
        if item.get("status") == "correct":
            continue
        severity = str(item.get("severity") or "minor").upper()
        device = item.get("hostname") or "Unknown device"
        if item.get("counts_toward_marking", True) and item.get("status") != "skipped":
            message = format_error(device, item, severity)
            errors.append(message)
            scored_entries.append({"item": item, "message": message})
        else:
            additional_findings.append(
                {"item": item, "message": format_unscored_finding(device, item)}
            )

    grouped_errors = {}
    unmatched_additional = []
    for finding in additional_findings:
        parent_index = _find_parent_error_index(finding["item"], scored_entries)
        if parent_index is None:
            unmatched_additional.append(finding["message"])
            continue
        parent_message = scored_entries[parent_index]["message"]
        grouped_errors.setdefault(parent_message, []).append(finding["message"])

    return write_results(
        student_path,
        result,
        errors,
        minor,
        major,
        additional_findings=unmatched_additional,
        grouped_errors=grouped_errors,
    )


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
