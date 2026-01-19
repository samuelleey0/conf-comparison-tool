#!/usr/bin/env python3
"""
Utility functions for grading workflow - separated for scalability
"""
import os
from rubric_manager import RubricManager
from scheme_manager import SchemeManager
from grade_manager import Grader


def get_full_interface_name(short_form: str) -> str:
    """
    Convert short interface form to full form.
    Examples: g0/0 -> GigabitEthernet0/0, fa0/0 -> FastEthernet0/0
    """
    short_form = short_form.lower().strip()

    # Mapping of short forms to full forms
    mappings = {
        "g": "GigabitEthernet",
        "gi": "GigabitEthernet",
        "ge": "GigabitEthernet",
        "e": "Ethernet",
        "f": "FastEthernet",
        "fa": "FastEthernet",
        "s": "Serial",
        "se": "Serial",
        "lo": "Loopback",
    }

    # Extract the prefix and the port part (e.g., "g0/0" -> prefix="g", port="0/0")
    prefix = ""
    port = ""
    for i, char in enumerate(short_form):
        if char.isdigit():
            prefix = short_form[:i]
            port = short_form[i:]
            break

    if not prefix or not port:
        return short_form  # Return as-is if format doesn't match

    full_name = mappings.get(prefix, prefix)
    return full_name + port


def build_criterion_helper():
    """Build a criterion by asking users simple questions (no regex needed)"""
    print("\n  === BUILD CRITERION ===")
    print("  What do you want to check?")
    print("  1. Hostname")
    print("  2. IP Address on Interface")
    print("  3. Interface Configuration")
    print("  4. VLAN configuration (IDs and names)")
    print("  5. Banner (MOTD)")
    print("  6. Text Contains")
    print("  7. Custom (I'll enter my own)")

    choice = input("  Choice: ").strip()

    if choice == "1":
        # Hostname
        value = input("  Enter hostname to check (e.g., R1 or {{hostname}}): ").strip()
        pattern = f"hostname\\s+{value}"
        exists_pattern = "hostname"
        print(f"  ✓ Will check: 'hostname {value}'")
        return pattern, exists_pattern

    elif choice == "2":
        # IP Address on Interface
        interface = input("  Interface (e.g., g0/0, Gi0/0, Fa0/0): ").strip()
        full_interface = get_full_interface_name(interface)

        ip = input("  IP address (e.g., 192.168.1.1 or {{ip}}): ").strip()

        if ip.startswith("{{"):
            # For variables, match: interface <full_name> ... ip address <{{ip}}>
            pattern = f"interface\\s+{full_interface}[\\s\\S]*?ip\\s+address\\s+{ip}"
        else:
            # For static IPs, escape dots and match across lines
            escaped_ip = ip.replace(".", "\\.")
            pattern = (
                f"interface\\s+{full_interface}[\\s\\S]*?ip\\s+address\\s+{escaped_ip}"
            )

        exists_pattern = f"interface\\s+{full_interface}"
        print(f"  ✓ Will check: '{full_interface}' has IP '{ip}'")
        return pattern, exists_pattern

    elif choice == "3":
        # Interface Configuration
        interface = input("  Interface (e.g., g0/0, Fa0/0): ").strip()
        full_interface = get_full_interface_name(interface)

        config_line = input("  Configuration (e.g., 'description WAN Link'): ").strip()
        pattern = f"interface\\s+{full_interface}[\\s\\S]*?{config_line}"
        exists_pattern = f"interface\\s+{full_interface}"
        print(f"  ✓ Will check: '{full_interface}' has '{config_line}'")
        return pattern, exists_pattern

    elif choice == "4":
        # VLAN configuration - Reference a scheme variable
        var_name = input(
            "  Enter the scheme variable name containing VLANs (e.g., vlan_list): "
        ).strip()

        # We use a special prefix or structure so the Grader knows this is a multi-item check
        # We store it as a JSON-like string or a custom format the grader will parse
        pattern = f"VLAN_LOOP:{{{{{var_name}}}}}"
        exists_pattern = "vlan"

        print(
            f"  ✓ Will dynamically check all VLANs defined in scheme variable: '{var_name}'"
        )
        return pattern, exists_pattern

    elif choice == "5":
        # Banner MOTD
        banner_text = input(
            "  Banner text to check (press Enter for any banner): "
        ).strip()
        if banner_text:
            pattern = f"banner\\s+motd.*{banner_text}"
        else:
            pattern = "banner\\s+motd"
        exists_pattern = "banner\\s+motd"
        print(f"  ✓ Will check: banner motd exists")
        return pattern, exists_pattern

    elif choice == "6":
        # Simple text contains
        text = input("  Text to find (e.g., 'OSPF enabled'): ").strip()
        print(f"  ✓ Will check: contains '{text}'")
        return text, None  # No exists_pattern needed

    elif choice == "7":
        # Custom regex (for power users)
        print("  (Advanced) Enter your patterns:")
        pattern = input("    Main pattern (regex): ").strip()
        exists = input("    Exists pattern (optional, regex): ").strip()
        print(f"  ✓ Using custom pattern: {pattern}")
        return pattern, exists if exists else None

    else:
        print("  Invalid choice")
        return None, None


def create_scheme_programmatic():
    """Create a unified scheme with standard variables and complex VLAN lists"""
    sm = SchemeManager()

    print("\n=== CREATE NEW SCHEME ===")
    scheme_data = {
        "name": input("Scheme name (e.g., Set Alpha): ").strip(),
        "variables": {},
    }

    print("\nOptions:")
    print(" - Enter 'key=value' for standard variables (e.g., hostname=R1)")
    print(" - Enter 'vlan' to start the VLAN List Wizard")
    print(" - Press Enter on an empty line to finish")

    while True:
        line = input("\n[Scheme Input] > ").strip()

        if not line:
            break

        # Scenario A: User wants to add a VLAN List
        if line.lower() == "vlan":
            vlan_key = (
                input(
                    "  Enter the variable name for this list (default: vlans): "
                ).strip()
                or "vlans"
            )
            vlan_list = []

            print(f"  --- Adding VLANs to '{vlan_key}' ---")
            while True:
                v_id = input("    VLAN ID (or Enter to finish list): ").strip()
                if not v_id:
                    break
                v_name = input(f"    Name for VLAN {v_id}: ").strip()

                vlan_list.append({"id": v_id, "name": v_name})

            scheme_data["variables"][vlan_key] = vlan_list
            print(f"  ✓ Added {len(vlan_list)} VLANs to '{vlan_key}'")

        # Scenario B: User adds a standard variable
        elif "=" in line:
            key, val = line.split("=", 1)
            scheme_data["variables"][key.strip()] = val.strip()
            print(f"  ✓ Added {key.strip()} = {val.strip()}")

        else:
            print("  Invalid format. Use 'key=value' or 'vlan'.")

    scheme_id = sm.save_scheme(scheme_data)
    print(f"\n✓ Scheme created successfully! ID: {scheme_id}")
    return scheme_id


def create_rubric_programmatic():
    """Create a rubric without hardcoding"""
    rm = RubricManager()

    print("\n=== CREATE NEW RUBRIC ===")
    rubric_data = {
        "name": input("Rubric name: ").strip(),
        "description": input("Description: ").strip(),
        "criteria": [],
    }

    print("\nAdd grading criteria. Press Enter on empty name to finish:")
    while True:
        name = input("\n  Criterion name: ").strip()
        if not name:
            break

        use_builder = (
            input("  Use builder to create criterion? (y/n): ").strip().lower()
        )
        if use_builder == "y":
            pattern, exists_pattern = build_criterion_helper()
            if not pattern:
                continue
        else:
            pattern = input("  Pattern (regex): ").strip()
            exists_pattern = input("  Exists pattern (optional): ").strip()
        points = int(input("  Points: ").strip() or "0")

        criterion = {"name": name, "pattern": pattern, "points": points}
        if exists_pattern:
            criterion["exists_pattern"] = exists_pattern

        rubric_data["criteria"].append(criterion)

    rubric_id = rm.create_rubric(rubric_data)
    print(f"✓ Rubric created with ID: {rubric_id}\n")
    return rubric_id


def edit_scheme_programmatic():
    """Edit an existing scheme"""
    sm = SchemeManager()

    print("\n=== EDIT SCHEME ===")
    schemes = sm.get_all_schemes()

    if not schemes:
        print("No schemes found.")
        return

    for i, scheme in enumerate(schemes, 1):
        print(f"{i}. {scheme.get('name')} (ID: {scheme.get('id')})")

    choice = input("\nSelect scheme number to edit: ").strip()

    try:
        idx = int(choice) - 1
        scheme_id = schemes[idx]["id"]
        scheme = sm.get_scheme_by_id(scheme_id)
    except (ValueError, IndexError):
        print("Invalid selection")
        return

    print(f"\nEditing: {scheme.get('name')}")
    print("Press Enter to skip a field\n")

    new_name = input(f"  Name [{scheme.get('name')}]: ").strip()
    if new_name:
        scheme["name"] = new_name

    print(f"\n  Current variables: {scheme.get('variables', {})}")
    print("  Modify variables (key=value). Press Enter on empty line to finish:")

    while True:
        line = input("    > ").strip()
        if not line:
            break
        if "=" in line:
            key, val = line.split("=", 1)
            scheme["variables"][key.strip()] = val.strip()

    sm.save_scheme(scheme)
    print(f"✓ Scheme updated: {scheme_id}\n")


def edit_rubric_programmatic():
    """Edit an existing rubric"""
    rm = RubricManager()

    print("\n=== EDIT RUBRIC ===")
    rubrics = rm.get_rubrics()

    if not rubrics:
        print("No rubrics found.")
        return

    for i, rubric in enumerate(rubrics, 1):
        print(f"{i}. {rubric.get('name')} (ID: {rubric.get('id')})")

    choice = input("\nSelect rubric number to edit: ").strip()

    try:
        idx = int(choice) - 1
        rubric_id = rubrics[idx]["id"]
        rubric = rm.get_rubric_detail(rubric_id)
    except (ValueError, IndexError):
        print("Invalid selection")
        return

    print(f"\nEditing: {rubric.get('name')}")
    print("Press Enter to skip a field\n")

    new_name = input(f"  Name [{rubric.get('name')}]: ").strip()
    if new_name:
        rubric["name"] = new_name

    new_desc = input(f"  Description [{rubric.get('description', '')}]: ").strip()
    if new_desc:
        rubric["description"] = new_desc

    print(f"\n  Current criteria ({len(rubric.get('criteria', []))} total):")
    for i, crit in enumerate(rubric.get("criteria", []), 1):
        print(f"    {i}. {crit.get('name')} ({crit.get('points')} pts)")

    edit_criteria = input("\n  Edit criteria? (y/n): ").strip().lower()
    if edit_criteria == "y":
        print("\n  Criteria management:")
        print("  (1) Edit existing criterion")
        print("  (2) Add new criterion")
        print("  (3) Delete criterion")

        criteria_choice = input("  Choice: ").strip()

        if criteria_choice == "1":
            # Edit existing
            for i, crit in enumerate(rubric.get("criteria", []), 1):
                print(f"    {i}. {crit.get('name')}")

            crit_num = input("  Select criterion number to edit: ").strip()
            try:
                crit_idx = int(crit_num) - 1
                crit = rubric["criteria"][crit_idx]

                print(f"\n  Editing: {crit.get('name')}")
                new_crit_name = input(f"    Name [{crit.get('name')}]: ").strip()
                if new_crit_name:
                    crit["name"] = new_crit_name

                use_helper = input("    Use builder? (y/n): ").strip().lower()
                if use_helper == "y":
                    new_pattern, new_exists = build_criterion_helper()
                    if new_pattern:
                        crit["pattern"] = new_pattern
                        if new_exists:
                            crit["exists_pattern"] = new_exists
                else:
                    new_pattern = input(
                        f"    Pattern [{crit.get('pattern')}]: "
                    ).strip()
                    if new_pattern:
                        crit["pattern"] = new_pattern

                    new_exists = input(
                        f"    Exists pattern [{crit.get('exists_pattern', '')}]: "
                    ).strip()
                    if new_exists:
                        crit["exists_pattern"] = new_exists
                    elif "exists_pattern" in crit:
                        del crit["exists_pattern"]
                    del crit["exists_pattern"]

                new_points = input(f"    Points [{crit.get('points')}]: ").strip()
                if new_points:
                    crit["points"] = int(new_points)

                print(f"  ✓ Updated criterion: {crit.get('name')}")
            except (ValueError, IndexError):
                print("  Invalid selection")

        elif criteria_choice == "2":
            # Add new
            print("  Add new criteria. Press Enter on empty name to finish:")
            while True:
                name = input("\n    Criterion name: ").strip()
                if not name:
                    break

                use_helper = input("    Use builder? (y/n): ").strip().lower()
                if use_helper == "y":
                    pattern, exists_pattern = build_criterion_helper()
                    if not pattern:
                        continue
                else:
                    pattern = input("    Pattern (regex): ").strip()
                    exists_pattern = input("    Exists pattern (optional): ").strip()

                points = int(input("    Points: ").strip() or "0")

                criterion = {"name": name, "pattern": pattern, "points": points}
                if exists_pattern:
                    criterion["exists_pattern"] = exists_pattern

                rubric["criteria"].append(criterion)

        elif criteria_choice == "3":
            # Delete criterion
            for i, crit in enumerate(rubric.get("criteria", []), 1):
                print(f"    {i}. {crit.get('name')}")

            crit_num = input("  Select criterion number to delete: ").strip()
            try:
                crit_idx = int(crit_num) - 1
                deleted_crit = rubric["criteria"].pop(crit_idx)
                print(f"  ✓ Deleted criterion: {deleted_crit.get('name')}")
            except (ValueError, IndexError):
                print("  Invalid selection")

    rm.update_rubric(rubric_id, rubric)
    print(f"✓ Rubric updated: {rubric_id}\n")


def list_and_select_scheme():
    """List all schemes and let user select one"""
    sm = SchemeManager()
    schemes = sm.get_all_schemes()

    if not schemes:
        print("No schemes found. Create one first.")
        return None

    print("\n=== AVAILABLE SCHEMES ===")
    for i, scheme in enumerate(schemes, 1):
        print(f"{i}. {scheme.get('name')} (ID: {scheme.get('id')})")
        vars_preview = scheme.get("variables", {})
        print(f"   Variables: {list(vars_preview.keys())}")

    choice = input("\nSelect scheme number (or 'new' to create): ").strip()

    if choice.lower() == "new":
        return create_scheme_programmatic()

    try:
        idx = int(choice) - 1
        return schemes[idx]["id"]
    except (ValueError, IndexError):
        print("Invalid selection")
        return None


def list_and_select_rubric():
    """List all rubrics and let user select one"""
    rm = RubricManager()
    rubrics = rm.get_rubrics()

    if not rubrics:
        print("No rubrics found. Create one first.")
        return None

    print("\n=== AVAILABLE RUBRICS ===")
    for i, rubric in enumerate(rubrics, 1):
        print(f"{i}. {rubric.get('name')} (ID: {rubric.get('id')})")
        print(f"   {rubric.get('description', '')}")

    choice = input("\nSelect rubric number (or 'new' to create): ").strip()

    if choice.lower() == "new":
        return create_rubric_programmatic()

    try:
        idx = int(choice) - 1
        return rubrics[idx]["id"]
    except (ValueError, IndexError):
        print("Invalid selection")
        return None


def select_config_file():
    """Select a collected config file"""
    print("\n=== SELECT CONFIG FILE ===")
    config_path = input("Enter path to collected config file: ").strip()

    if not os.path.exists(config_path):
        print(f"Error: File not found: {config_path}")
        return None

    return config_path


def grade_config(config_path, scheme_id, rubric_id):
    """Grade a config file using selected scheme and rubric"""
    rm = RubricManager()
    sm = SchemeManager()
    grader = Grader()

    # Load config
    with open(config_path, "r") as f:
        config_text = f.read()

    # Load scheme and rubric
    scheme = sm.get_scheme_by_id(scheme_id)
    rubric = rm.get_rubric_detail(rubric_id)

    # Prepare rubric with scheme variables
    final_rubric = sm.prepare_rubric_for_grading(rubric, scheme)

    # Grade
    results = grader.grade(config_text, final_rubric)

    # Display results
    print("\n" + "=" * 70)
    print(f"GRADING REPORT")
    print(f"Config: {config_path}")
    print(f"Scheme: {scheme.get('name')} (ID: {scheme_id})")
    print(f"Rubric: {rubric.get('name')} (ID: {rubric_id})")
    print("=" * 70)

    total_earned = 0
    total_possible = 0

    print(f"\n{'Criterion':<35} {'Status':<12} {'Score':<10}")
    print("-" * 70)

    for result in results:
        print(
            f"{result['criterion']:<35} {result['status']:<12} {result['points']:<10}"
        )
        if "/" in result["points"]:
            earned, possible = result["points"].split("/")
            total_earned += int(earned)
            total_possible += int(possible)

    print("-" * 70)
    percentage = (total_earned / total_possible * 100) if total_possible > 0 else 0
    print(f"{'TOTAL':<35} {'':<12} {total_earned}/{total_possible} ({percentage:.1f}%)")
    print("=" * 70 + "\n")


def list_all_schemes():
    """List all schemes"""
    sm = SchemeManager()
    schemes = sm.get_all_schemes()
    print("\n=== ALL SCHEMES ===")
    for scheme in schemes:
        print(f"- {scheme.get('name')} (ID: {scheme.get('id')})")
        print(f"  Variables: {scheme.get('variables')}")


def list_all_rubrics():
    """List all rubrics"""
    rm = RubricManager()
    rubrics = rm.get_rubrics()
    print("\n=== ALL RUBRICS ===")
    for rubric in rubrics:
        print(f"- {rubric.get('name')} (ID: {rubric.get('id')})")
        print(f"  {rubric.get('description', '')}")


def delete_rubric_programmatic():
    """Delete an existing rubric"""
    rm = RubricManager()

    print("\n=== DELETE RUBRIC ===")
    rubrics = rm.get_rubrics()

    if not rubrics:
        print("No rubrics found.")
        return

    print("Select rubric to delete:")
    for i, rubric in enumerate(rubrics, 1):
        print(f"{i}. {rubric.get('name')} (ID: {rubric.get('id')})")

    choice = input("\nSelect rubric number to delete (or 'cancel'): ").strip()

    if choice.lower() == "cancel":
        print("Cancelled.")
        return

    try:
        idx = int(choice) - 1
        rubric_id = rubrics[idx]["id"]
        rubric_name = rubrics[idx].get("name")

        confirm = (
            input(f"\nAre you sure you want to delete '{rubric_name}'? (yes/no): ")
            .strip()
            .lower()
        )

        if confirm == "yes":
            rm.delete_rubric(rubric_id)
            print(f"✓ Rubric deleted: {rubric_name}\n")
        else:
            print("Cancelled.")
    except (ValueError, IndexError):
        print("Invalid selection")


def delete_scheme_programmatic():
    """Delete an existing scheme"""
    sm = SchemeManager()

    print("\n=== DELETE SCHEME ===")
    schemes = sm.get_all_schemes()

    if not schemes:
        print("No schemes found.")
        return

    print("Select scheme to delete:")
    for i, scheme in enumerate(schemes, 1):
        print(f"{i}. {scheme.get('name')} (ID: {scheme.get('id')})")

    choice = input("\nSelect scheme number to delete (or 'cancel'): ").strip()

    if choice.lower() == "cancel":
        print("Cancelled.")
        return

    try:
        idx = int(choice) - 1
        scheme_id = schemes[idx]["id"]
        scheme_name = schemes[idx].get("name")

        confirm = (
            input(f"\nAre you sure you want to delete '{scheme_name}'? (yes/no): ")
            .strip()
            .lower()
        )

        if confirm == "yes":
            sm.delete_scheme(scheme_id)
            print(f"✓ Scheme deleted: {scheme_name}\n")
        else:
            print("Cancelled.")
    except (ValueError, IndexError):
        print("Invalid selection")
