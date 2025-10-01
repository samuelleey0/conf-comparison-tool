import os
from datetime import datetime
from os import makedirs
from pathlib import Path

def list_existing_directories():
    """
    List existing exam directories and let user select one.
    """
    docs_path = Path.home() / "Documents"

    if not docs_path.exists():
        print("[!] Documents folder not found.")
        return None

    # Look for exam directories (folders with pattern like TNE20002_*)
    exam_dirs = [d for d in docs_path.iterdir() if d.is_dir() and not d.name.startswith('.')]

    if not exam_dirs:
        print("[!] No existing directories found in Documents.")
        return None

    print("\n=== Existing Directories ===")
    for i, exam_dir in enumerate(exam_dirs, 1):
        # Look for session subdirectories
        session_dirs = [s for s in exam_dir.iterdir() if s.is_dir()]
        if session_dirs:
            for session_dir in session_dirs:
                student_dirs = [st for st in session_dir.iterdir() if st.is_dir()]
                if student_dirs:
                    for student_dir in student_dirs:
                        print(f"{i}. {exam_dir.name}/{session_dir.name}/{student_dir.name}")
                        break
                break
        else:
            print(f"{i}. {exam_dir.name} (no sessions found)")

    while True:
        try:
            choice = input(f"\nSelect directory (1-{len(exam_dirs)}) or 'b' to go back: ").strip()
            if choice.lower() == 'b':
                return None

            choice_num = int(choice)
            if 1 <= choice_num <= len(exam_dirs):
                selected_dir = exam_dirs[choice_num - 1]

                # Find the student directory path
                for session_dir in selected_dir.iterdir():
                    if session_dir.is_dir():
                        for student_dir in session_dir.iterdir():
                            if student_dir.is_dir():
                                return {
                                    "base_path": str(student_dir),
                                    "exam_name": selected_dir.name,
                                    "session_id": session_dir.name,
                                    "student_id": student_dir.name
                                }

                print("[!] No valid student directory found in selected exam.")
                return None
            else:
                print(f"Invalid choice. Please enter 1-{len(exam_dirs)} or 'b'.")
        except ValueError:
            print("Invalid input. Please enter a number or 'b'.")

def build_base_path():
    """
    Ask user to create new directory or select existing one.
    """
    print("\n=== Directory Setup ===")
    print("1. Create new directory structure")
    print("2. Select existing directory")

    while True:
        choice = input("Choose option (1 or 2): ").strip()
        if choice == "1":
            return create_new_directory()
        elif choice == "2":
            return select_existing_directory()
        else:
            print("Invalid choice. Please enter 1 or 2.")

def create_new_directory():
    """
    Original function to create new directory structure.
    """
    try:
        while True:
            exam_name = input("Enter Exam Name (UNITCODE_Purpose_Time: TNE20002_SkillExam_8-10am): ").strip()
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

        base_path = os.path.expanduser(f"~/Documents/{exam_name}/{session_id}/{student_id}")
        os.makedirs(base_path, exist_ok=True)
        print(f"[+] Directory created: {base_path}")

        proceed = input("Do you want to continue? (y to continue / q to quit): ").strip().lower()
        if proceed == "q":
            print(f"[!] Exiting as per user request. Directory created at {base_path}")
            return None

        return {
            "base_path": base_path,
            "exam_name": exam_name,
            "session_id": session_id,
            "student_id": student_id
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
        proceed = input("Do you want to continue? (y to continue / q to quit): ").strip().lower()
        if proceed == "q":
            print("[!] Exiting as per user request.")
            return None
        return path_info
    else:
        print("[!] No directory selected. Creating new one...")
        return create_new_directory()


def save_output_to_file(command: str, output: str, exam_name: str, student_id: str = None, session_id: str = None, hostname: str = None, base_dir="logs"):
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
            base_dir = str(Path.home() / "Documents") # Default to ~/Documents
        dir_path = os.path.join(base_dir, exam_name, session_id if session_id else "Session1", student_id if student_id else "UnknownID", hostname)

    os.makedirs(dir_path, exist_ok=True)

    # Clean command string for filename
    safe_command = command.replace(" ", "_").replace("/", "_")
    file_name = f"{safe_command}.txt"

    # Full file path
    file_path = os.path.join(dir_path, file_name)

    # Write output
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"[+] Output for '{command}' saved to {file_path}")
    return file_path
#
# def build_base_path():
#     """
#     Ask user for exam/session/student and build path automatically.
#     Ensures directory is only created after confirmation and handles user interruption.
#     """
#     try:
#         while True:
#             while True:
#                 exam_name = input("Enter Exam Name (UNITCODE_Purpose_Time: TNE20002_SkillExam_8-10am): ").strip()
#                 if exam_name:
#                     break
#                 print("Exam Name cannot be empty. Please try again.")
#
#             while True:
#                 session_id = input("Enter Session ID (e.g., Session1): ").strip()
#                 if session_id:
#                     break
#                 print("Session ID cannot be empty. Please try again.")
#
#             while True:
#                 student_id = input("Enter Student ID (e.g., 101XXXXXX): ").strip()
#                 if student_id:
#                     break
#                 print("Student ID cannot be empty. Please try again.")
#
#             print(f"\nPlease confirm your entries:")
#             print(f"  Exam Name: {exam_name}")
#             print(f"  Session ID: {session_id}")
#             print(f"  Student ID: {student_id}")
#             confirm = input("Is this correct? (y/n): ").strip().lower()
#             if confirm == "y":
#                 break
#             print("Try again.\n")
#
#         base_path = os.path.expanduser(
#             f"~/Documents/{exam_name}/{session_id}/{student_id}/"
#         )
#         os.makedirs(base_path, exist_ok=True)
#         print(f"[+] Logs will be saved in: {base_path}")
#
#         proceed = input("Do you want to continue to establish connection? (y to continue / q to quit): ").strip().lower()
#         if proceed == "q":
#             print(f"[!] Exiting as per user request. Directory created {base_path}")
#             return None
#
#         return {
#             "exam_name": exam_name,
#             "session_id": session_id,
#             "student_id": student_id,
#             "base_path": base_path
#         }
#     except KeyboardInterrupt:
#         print("\nInput cancelled by user. No directory created.")
#         return None