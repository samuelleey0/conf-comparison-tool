import os
import yaml
from comparison_engine.student_manager import compare_student_hostnames
from comparison_engine.template_manager import setup_templates

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


def grading_pipeline(target_path, templates_dir):
    """
    Automated grading pipeline invoked by the backend API.
    Loops through all students in target_path and grades against templates in templates_dir.
    """
    if not os.path.exists(target_path):
        return [], f"Target path {target_path} not found."

    # We load the template configurations into memory
    # Because `setup_templates` usually prompts, we will recreate its auto-loading logic for all templates
    summary_results = []

    templates = []
    # If the Templates folder has multiple template sub-directories, we iterate
    if not os.path.exists(templates_dir):
        return [], "Templates directory is missing."

    for t_name in os.listdir(templates_dir):
        t_path = os.path.join(templates_dir, t_name)
        if os.path.isdir(t_path):
            # Load all hostnames mapped to this template
            template_hostnames = {}
            for h_name in os.listdir(t_path):
                h_config = os.path.join(t_path, h_name, "config.json")
                if os.path.exists(h_config):
                    import json

                    try:
                        with open(h_config, "r") as f:
                            template_hostnames[h_name] = json.load(f)
                    except Exception as e:
                        pass
            if template_hostnames:
                templates.append((t_name, template_hostnames))

    if not templates:
        return [], "No configured template hosts found to grade against."

    for student_dir in os.listdir(target_path):
        student_id = student_dir
        student_path = os.path.join(target_path, student_dir)
        if not os.path.isdir(student_path):
            continue

        # We loop through all templates, find hostnames, run compare
        for t_name, t_hosts in templates:
            # Note: `compare_student_hostnames` currently writes a summary file.
            # We can run it directly and read results from RESULTS_FOLDER, or alter it to return dict.
            try:
                compare_student_hostnames(
                    student_id=student_id,
                    template_name=t_name,
                    template_data=t_hosts,
                    student_folder_base=target_path,  # Path where students sit
                    results_folder=RESULTS_FOLDER,
                    scheme_mode="strict",
                )
                summary_results.append(
                    {"student_id": student_id, "status": "Graded", "template": t_name}
                )
            except Exception as e:
                summary_results.append(
                    {
                        "student_id": student_id,
                        "status": f"Error: {str(e)}",
                        "template": t_name,
                    }
                )

    return summary_results, "Grading initialized fully."


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
            TEMPLATE_FOLDER,
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
