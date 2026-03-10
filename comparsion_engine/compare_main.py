import os
import yaml
from student_manager import compare_student_hostnames
from template_manager import setup_templates

# Get the directory where compare_main.py is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Folders
TEMPLATE_FOLDER = os.path.join(BASE_DIR, "templates")
STUDENT_FOLDER = os.path.join(BASE_DIR, "students")
RESULTS_FOLDER = os.path.join(BASE_DIR, "results")
SCHEME_FOLDER = os.path.join(os.path.dirname(BASE_DIR), "schemes")

# Make sure folders exist
os.makedirs(TEMPLATE_FOLDER, exist_ok=True)
os.makedirs(STUDENT_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)


def list_schemes(scheme_folder):
    schemes = []
    if not os.path.isdir(scheme_folder):
        return schemes

    for entry in sorted(os.listdir(scheme_folder)):
        if not entry.endswith(".yaml"):
            continue
        path = os.path.join(scheme_folder, entry)
        try:
            with open(path, "r") as handle:
                data = yaml.safe_load(handle) or {}
                data.setdefault("id", os.path.splitext(entry)[0])
                schemes.append(data)
        except Exception:
            continue

    return schemes


def choose_scheme(schemes, prompt_text):
    if not schemes:
        print("No schemes found. Falling back to strict mode.")
        return None

    print(f"\n{prompt_text}")
    for idx, scheme in enumerate(schemes, 1):
        print(f"{idx}. {scheme.get('name', 'Unnamed')} (ID: {scheme.get('id')})")

    selection = input("Select scheme number: ").strip()
    try:
        index = int(selection) - 1
        if 0 <= index < len(schemes):
            return schemes[index]
    except ValueError:
        pass

    print("Invalid selection. No scheme selected.")
    return None


def main():
    """Main function to run the comparison tool."""
    print("=== Multi-Hostname Show Run Comparison Tool ===")
    print("Expected structure:")
    print("  Templates: templates/<template_name>/<hostname>/logs/")
    print("  Students:  students/<student_id>/<hostname>/")

    template_name, templates = setup_templates(TEMPLATE_FOLDER)
    if not templates:
        print("No templates loaded. Exiting.")
        return

    print("\nGrading mode:")
    print("1. Strict (exact values must match)")
    print("2. Scheme-aware (normalize VLAN IDs by scheme roles)")
    mode_choice = input("Select mode [1/2]: ").strip() or "1"
    scheme_mode = "scheme-aware" if mode_choice == "2" else "strict"

    template_scheme = None
    schemes = []
    if scheme_mode == "scheme-aware":
        schemes = list_schemes(SCHEME_FOLDER)
        template_scheme = choose_scheme(
            schemes,
            "Select TEMPLATE scheme (the scheme used to build teacher template):",
        )
        if template_scheme is None:
            print("Template scheme not selected. Switching to strict mode.")
            scheme_mode = "strict"

    while True:
        student_id = input("\nEnter student ID to grade (or 'q' to quit): ").strip()
        if student_id.lower() == "q":
            break
        if not student_id:
            print("Invalid student ID.")
            continue

        student_scheme = None
        if scheme_mode == "scheme-aware":
            student_scheme = choose_scheme(
                schemes,
                f"Select STUDENT scheme for {student_id}:",
            )
            if student_scheme is None:
                print(
                    "Student scheme not selected. Using strict mode for this student."
                )
                active_mode = "strict"
            else:
                active_mode = "scheme-aware"
        else:
            active_mode = "strict"

        compare_student_hostnames(
            student_id,
            template_name,
            templates,
            STUDENT_FOLDER,
            RESULTS_FOLDER,
            scheme_mode=active_mode,
            template_scheme=template_scheme,
            student_scheme=student_scheme,
        )

        another = input("Grade another student? (y/n): ").strip().lower()
        if another != "y":
            break

    print("\nSession complete.")


if __name__ == "__main__":
    main()
