#!/usr/bin/env python3
"""
Main entry point for grading workflow
Orchestrates user interactions and delegates to utility functions
"""
from grading_utils import (
    create_scheme_programmatic,
    create_rubric_programmatic,
    edit_scheme_programmatic,
    edit_rubric_programmatic,
    delete_scheme_programmatic,
    delete_rubric_programmatic,
    list_and_select_scheme,
    list_and_select_rubric,
    select_config_file,
    grade_config,
    list_all_schemes,
    list_all_rubrics,
)


def display_menu():
    """Display main menu options"""
    print("\nWhat would you like to do?")
    print("1. Create new scheme")
    print("2. Create new rubric")
    print("3. Edit existing scheme")
    print("4. Edit existing rubric")
    print("5. Delete existing scheme")
    print("6. Delete existing rubric")
    print("7. Grade a config file")
    print("8. List all schemes")
    print("9. List all rubrics")
    print("10. Exit")


def handle_menu_choice(choice):
    """Handle user menu selection"""
    if choice == "1":
        create_scheme_programmatic()

    elif choice == "2":
        create_rubric_programmatic()

    elif choice == "3":
        edit_scheme_programmatic()

    elif choice == "4":
        edit_rubric_programmatic()

    elif choice == "5":
        delete_scheme_programmatic()

    elif choice == "6":
        delete_rubric_programmatic()

    elif choice == "7":
        config_path = select_config_file()
        if not config_path:
            return True

        scheme_id = list_and_select_scheme()
        if not scheme_id:
            return True

        rubric_id = list_and_select_rubric()
        if not rubric_id:
            return True

        grade_config(config_path, scheme_id, rubric_id)

    elif choice == "8":
        list_all_schemes()

    elif choice == "9":
        list_all_rubrics()

    elif choice == "10":
        return False  # Signal to exit

    else:
        print("Invalid choice. Try again.")

    return True  # Continue running


def main():
    """Main workflow orchestration"""
    print("=" * 70)
    print("DYNAMIC SCHEME & RUBRIC GRADING SYSTEM")
    print("=" * 70)

    while True:
        display_menu()
        choice = input("\nChoice: ").strip()

        if not handle_menu_choice(choice):
            print("Goodbye!")
            break


if __name__ == "__main__":
    main()
