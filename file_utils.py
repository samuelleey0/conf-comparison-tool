import os
from datetime import datetime
from os import makedirs
from pathlib import Path
import csv


def load_students_from_file():
    """
    Load student IDs from a CSV or text file.
    CSV format: student_id,name (optional)
    Text format: one student ID per line
    """
    print("\n=== Load Students from File ===")
    file_path = input("Enter path to CSV/TXT file (or drag & drop): ").strip()

    # Remove quotes if file was dragged and dropped
    file_path = file_path.strip("\"'")

    if not os.path.exists(file_path):
        print(f"[!] File not found: {file_path}")
        return None

    students = []

    try:
        # Detect file type and load accordingly
        if file_path.lower().endswith(".csv"):
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0].strip():  # Skip empty rows
                        student_id = row[0].strip()
                        name = row[1].strip() if len(row) > 1 else ""
                        students.append({"id": student_id, "name": name})
        else:
            # Treat as text file
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    student_id = line.strip()
                    if student_id:  # Skip empty lines
                        students.append({"id": student_id, "name": ""})

        if not students:
            print("[!] No student IDs found in file.")
            return None

        print(f"[+] Loaded {len(students)} student IDs:")
        for i, student in enumerate(students[:5], 1):  # Show first 5
            display_name = f" ({student['name']})" if student["name"] else ""
            print(f"  {i}. {student['id']}{display_name}")

        if len(students) > 5:
            print(f"  ... and {len(students) - 5} more")

        return students

    except Exception as e:
        print(f"[!] Error reading file: {e}")
        return None


def create_bulk_directories():
    """
    Create directories for multiple students from file input.
    """
    try:
        # Get exam and session info
        while True:
            exam_name = input("Enter Exam Name (UNITCODE_Purpose_Time): ").strip()
            if exam_name:
                break
            print("Exam Name cannot be empty. Please try again.")

        while True:
            session_id = input("Enter Session ID (e.g., Session1): ").strip()
            if session_id:
                break
            print("Session ID cannot be empty. Please try again.")

        # Load students from file
        students = load_students_from_file()
        if not students:
            return None

        print(f"\nConfirm bulk directory creation:")
        print(f"  Exam Name: {exam_name}")
        print(f"  Session ID: {session_id}")
        print(f"  Students: {len(students)} students")

        confirm = input("Create directories for all students? (y/n): ").strip().lower()
        if confirm != "y":
            return None

        # Create directories for all students
        created_paths = []
        base_docs_path = os.path.expanduser("~/Documents")

        for student in students:
            student_path = os.path.join(
                base_docs_path, exam_name, session_id, student["id"]
            )
            os.makedirs(student_path, exist_ok=True)
            created_paths.append(
                {
                    "base_path": student_path,
                    "exam_name": exam_name,
                    "session_id": session_id,
                    "student_id": student["id"],
                    "display": f"{exam_name}/{session_id}/{student['id']}",
                }
            )

        print(f"[+] Created {len(created_paths)} directories")

        # Let user select which student to work with
        print("\n=== Select Student to Work With ===")
        for i, path_info in enumerate(created_paths, 1):
            print(f"{i}. {path_info['student_id']}")

        while True:
            try:
                choice = input(f"\nSelect student (1-{len(created_paths)}): ").strip()
                choice_num = int(choice)
                if 1 <= choice_num <= len(created_paths):
                    selected = created_paths[choice_num - 1]
                    print(f"[+] Selected: {selected['display']}")
                    return selected
                else:
                    print(f"Invalid choice. Please enter 1-{len(created_paths)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return None


def list_existing_directories():
    """
    List existing exam directories and let user select one.
    """
    docs_path = Path.home() / "Documents"

    if not docs_path.exists():
        print("[!] Documents folder not found.")
        return None

    # Look for all student directories
    student_paths = []
    exam_dirs = [
        d for d in docs_path.iterdir() if d.is_dir() and not d.name.startswith(".")
    ]

    for exam_dir in exam_dirs:
        session_dirs = [s for s in exam_dir.iterdir() if s.is_dir()]
        for session_dir in session_dirs:
            student_dirs = [st for st in session_dir.iterdir() if st.is_dir()]
            for student_dir in student_dirs:
                student_paths.append(
                    {
                        "path": str(student_dir),
                        "exam_name": exam_dir.name,
                        "session_id": session_dir.name,
                        "student_id": student_dir.name,
                        "display": f"{exam_dir.name}/{session_dir.name}/{student_dir.name}",
                    }
                )

    if not student_paths:
        print("[!] No existing student directories found in Documents.")
        return None

    print("\n=== Existing Directories ===")
    for i, path_info in enumerate(student_paths, start=1):
        print(f"{i}. {path_info['display']}")

    while True:
        try:
            choice = input(
                f"\nSelect directory (1-{len(student_paths)}) or 'b' to go back: "
            ).strip()
            if choice.lower() == "b":
                return None

            choice_num = int(choice)
            if 1 <= choice_num <= len(student_paths):
                selected = student_paths[choice_num - 1]
                return {
                    "base_path": selected["path"],
                    "exam_name": selected["exam_name"],
                    "session_id": selected["session_id"],
                    "student_id": selected["student_id"],
                }
            else:
                print(f"Invalid choice. Please enter 1-{len(student_paths)} or 'b'.")
        except ValueError:
            print("Invalid input. Please enter a number or 'b'.")


def build_base_path():
    """
    Ask user to create new directory or select existing one.
    """
    print("\n=== Directory Setup ===")
    print("1. Create new directory structure")
    print("2. Select existing directory")
    print("3. Create bulk directories from student file (CSV/TXT)")

    while True:
        choice = input("Choose option (1, 2, or 3): ").strip()
        if choice == "1":
            return create_new_directory()
        elif choice == "2":
            return select_existing_directory()
        elif choice == "3":
            return create_bulk_directories()
        else:
            print("Invalid choice. Please enter 1 or 2.")


def create_new_directory():
    """
    Original function to create new directory structure.
    """
    try:
        while True:
            exam_name = input(
                "Enter Exam Name (UNITCODE_Purpose_Time: TNE20002_SkillExam_8-10am): "
            ).strip()
            if not exam_name:
                print("Exam Name cannot be empty. Please try again.")
                continue

            session_id = input("Enter Session ID (e.g., Session1): ").strip()
            if not session_id:
                print("Session ID cannot be empty. Please try again.")
                continue

            student_id = input("Enter Student ID (e.g., 101XXXXXX): ").strip()
            if not student_id:
                print("Student ID cannot be empty. Please try again.")
                continue

            print(f"\nPlease confirm your entries:")
            print(f"  Exam Name: {exam_name}")
            print(f"  Session ID: {session_id}")
            print(f"  Student ID: {student_id}")
            confirm = input("Is this correct? (y/n): ").strip().lower()
            if confirm == "y":
                break
            print("Try again.\n")

        base_path = os.path.expanduser(
            f"~/Documents/{exam_name}/{session_id}/{student_id}"
        )
        os.makedirs(base_path, exist_ok=True)
        print(f"[+] Directory created: {base_path}")

        proceed = (
            input("Do you want to continue? (y to continue / q to quit): ")
            .strip()
            .lower()
        )
        if proceed == "q":
            print(f"[!] Exiting as per user request. Directory created at {base_path}")
            return None

        return {
            "base_path": base_path,
            "exam_name": exam_name,
            "session_id": session_id,
            "student_id": student_id,
        }
    except KeyboardInterrupt:
        print("\nInput cancelled by user. No directory created.")
        return None


def select_existing_directory():
    """
    Select from existing directories.
    """
    path_info = list_existing_directories()
    if path_info:
        print(f"[+] Selected directory: {path_info['base_path']}")
        proceed = (
            input("Do you want to continue? (y to continue / q to quit): ")
            .strip()
            .lower()
        )
        if proceed == "q":
            print("[!] Exiting as per user request.")
            return None
        return path_info
    else:
        print("[!] No directory selected. Creating new one...")
        return create_new_directory()


def save_output_to_file(
    command: str,
    output: str,
    exam_name: str,
    student_id: str = None,
    session_id: str = None,
    hostname: str = None,
    base_dir="logs",
    extension=".txt",
):
    """
    Save Cisco device command output to a text file.
    Each command goes into its own file.
    Example path: ~/Documents/TNE20002_SkillExam_8-10am/Session1/102778907/show_running-config.txt
    """

    if not hostname:
        raise ValueError("Hostname must be provided to save output files.")

    # If base_dir is already a complete path, just append the hostname
    if base_dir and os.path.isabs(base_dir):
        dir_path = os.path.join(base_dir, hostname)
    else:
        # Build directory path
        if base_dir is None:
            base_dir = str(Path.home() / "Documents")  # Default to ~/Documents
        dir_path = os.path.join(
            base_dir,
            exam_name,
            session_id if session_id else "Session1",
            student_id if student_id else "UnknownID",
            hostname,
        )

    os.makedirs(dir_path, exist_ok=True)

    # Clean command string for filename
    safe_command = command.replace(" ", "_").replace("/", "_")
    
    # Ensure extension starts with dot
    if not extension.startswith("."):
        extension = f".{extension}"
        
    file_name = f"{safe_command}{extension}"

    # Full file path
    file_path = os.path.join(dir_path, file_name)

    # Write output
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"[+] Output for '{command}' saved to {file_path}")
    return file_path


def del_partial_logs(base_path, exam_name, session_id, student_id, hostname):
    """
    Delete all log files for the current session if the collection is incomplete.
    """
    log_dir = os.path.join(base_path, exam_name, session_id, student_id, hostname)
    if os.path.exists(log_dir):
        print(f"[INFO] Deleting partial logs in {log_dir}...")
        for root, dirs, files in os.walk(log_dir, topdown=False):
            for file in files:
                os.remove(os.path.join(root, file))
            for dir in dirs:
                os.rmdir(os.path.join(root, dir))
        os.rmdir(log_dir)
        print("[INFO] Partial logs deleted.")
    else:
        print("[INFO] No partial logs found to delete.")
