import os
import json

SUMMARY_FILE = "summary.json"
MAJOR_KEYWORDS = ["ACL", "NAT", "ROUTING", "USER", "PPP"]


def is_major_error(outcome_code):
    if not outcome_code:
        return False
    return any(k in outcome_code for k in MAJOR_KEYWORDS)


def format_error(device, item, severity):
    feature = item.get("feature", "Unknown")
    expected = item.get("expected")
    actual = item.get("actual")
    outcome = item.get("outcome_code", "UNKNOWN")

    message = f"[{severity}] {device} - {feature}"

    if expected is not None and actual is not None:
        message += f"\n  → Expected: {expected}\n  → Actual: {actual}"
    elif expected is not None:
        message += f"\n  → Expected: {expected}"
    elif actual is not None:
        message += f"\n  → Actual: {actual}"

    message += f"\n  → Code: {outcome}"

    return message


def process_summary(summary_path):
    with open(summary_path, "r") as f:
        data = json.load(f)

    total_minor = 0
    total_major = 0
    errors = []

    results = data.get("results", {})

    for device, checks in results.items():
        for item in checks:
            status = item.get("status")

            if status == "correct":
                continue

            outcome = item.get("outcome_code", "")

            if is_major_error(outcome):
                severity = "MAJOR"
                total_major += 1
            else:
                severity = "MINOR"
                total_minor += 1

            error_msg = format_error(device, item, severity)
            errors.append(error_msg)

    return total_minor, total_major, errors


def determine_result(minor, major):
    return "FAIL" if (major >= 1 or minor >= 5) else "PASS"


def write_results(student_path, result, errors):
    results_folder_path = os.path.join(student_path, "results")
    os.makedirs(results_folder_path, exist_ok=True)

    output_file = os.path.join(results_folder_path, "results.txt")

    minor_errors = [e for e in errors if "[MINOR]" in e]
    major_errors = [e for e in errors if "[MAJOR]" in e]

    with open(output_file, "w", encoding="utf-8") as f:

        if result == "PASS":
            f.write("Congratulations. You have passed the exam!\n")

        else:
            f.write("You have committed a series number of errors that have caused you to fail the examination\n\n")

            f.write("List of Minor errors you have occured:\n")
            if minor_errors:
                for err in minor_errors:
                    f.write(f"- {err}\n\n")
            else:
                f.write("None\n\n")

            f.write("List of Major errors you have occured:\n")
            if major_errors:
                for err in major_errors:
                    f.write(f"- {err}\n\n")
            else:
                f.write("None\n")


def choose_session(base_path):
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

        write_results(student_path, result, errors)

        print(f"[RESULT] {student_id}: {result} (Minor={minor}, Major={major})\n")


def main():
   
    base_path = os.path.join(os.path.expanduser("~"), "Documents", "B408", "MarkTee")

    if not os.path.exists(base_path):
        print("[ERROR] Base directory not found.")
        return

    session_path = choose_session(base_path)

    if session_path:
        process_students(session_path)


if __name__ == "__main__":
    main()