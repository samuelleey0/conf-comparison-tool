import os
from datetime import datetime
from os import makedirs
from pathlib import Path


def save_output_to_file(command: str, output: str, exam_name: str, student_id: str = None, session_id: str = None, base_dir="logs"):
    """
    Save Cisco device command output to a text file.
    Each command goes into its own file.
    Example path: ~/Documents/TNE20002_SkillExam_8-10am/Session1/102778907/show_running-config.txt
    """

    # Build directory path
    if base_dir is None:
        base_dir = str(Path.home() / "Documents") # Default to ~/Documents

    dir_path = os.path.join(base_dir, exam_name, session_id if session_id else "Session1", student_id if student_id else "UnknownID")
    os,makedirs(dir_path, exist_ok=True)

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

def build_base_path():
    """
    Ask user for exam/session/student and build path automatically.
    Ensures directory is only created after confirmation and handles user interruption.
    """
    try:
        while True:
            while True:
                exam_name = input("Enter Exam Name (UNITCODE_Purpose_Time: TNE20002_SkillExam_8-10am): ").strip()
                if exam_name:
                    break
                print("Exam Name cannot be empty. Please try again.")

            while True:
                session_id = input("Enter Session ID (e.g., Session1): ").strip()
                if session_id:
                    break
                print("Session ID cannot be empty. Please try again.")

            while True:
                student_id = input("Enter Student ID (e.g., 101XXXXXX): ").strip()
                if student_id:
                    break
                print("Student ID cannot be empty. Please try again.")

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
        print(f"[+] Logs will be saved in: {base_path}")

        proceed = input("Do you want to continue to establish connection? (y to continue / q to quit): ").strip().lower()
        if proceed == "q":
            print(f"[!] Exiting as per user request. Directory created {base_path}")
            return None

        return base_path
    except KeyboardInterrupt:
        print("\nInput cancelled by user. No directory created.")
        return None