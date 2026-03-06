import os
from student_manager import compare_student_devices
from template_manager import setup_templates

# Get the directory where compare_main.py is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Folders
TEMPLATE_FOLDER = os.path.join(BASE_DIR, "templates")
STUDENT_FOLDER = os.path.join(BASE_DIR, "students")
RESULTS_FOLDER = os.path.join(BASE_DIR, "results")

# Make sure folders exist
os.makedirs(TEMPLATE_FOLDER, exist_ok=True)
os.makedirs(STUDENT_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)


def main():
    """Main function to run the comparison tool."""
    print("=== Multi-Device Show Run Comparison Tool ===")
    print("Expected student structure: students/<student_id>/<device>/<command files>")

    profile_name, templates = setup_templates(TEMPLATE_FOLDER)
    if not templates:
        print("No templates loaded. Exiting.")
        return

    while True:
        student_id = input("\nEnter student ID to grade (or 'q' to quit): ").strip()
        if student_id.lower() == "q":
            break
        if not student_id:
            print("Invalid student ID.")
            continue

        compare_student_devices(
            student_id,
            profile_name,
            templates,
            STUDENT_FOLDER,
            RESULTS_FOLDER,
        )

        another = input("Grade another student? (y/n): ").strip().lower()
        if another != "y":
            break

    print("\nSession complete.")


if __name__ == "__main__":
    main()
