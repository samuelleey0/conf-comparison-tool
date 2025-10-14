# main_cli_test.py
import os
import time
from command_manager import command_menu
from file_utils import save_output_to_file, build_base_path


def main():
    print("=== TEST MODE (No Cisco Device Required) ===")
    # === Ask user which commands to run ===
    commands = command_menu()
    if not commands:
        print("No commands selected. Exiting.")
        return

    # === Get path info dynamically ===
    path_info = build_base_path()
    if path_info is None:
        print("No path info available. Exiting.")
        return

    exam_name = path_info["exam_name"]
    session_id = path_info["session_id"]
    student_id = path_info["student_id"]
    base_path = path_info["base_path"]

    # Simulated hostname (instead of real device)
    hostname = "TEST_ROUTER"

    print(f"\n[*] Running test mode with hostname: {hostname}")
    for cmd in commands:
        print(f"[*] Sending '{cmd}'...")
        time.sleep(0.5)  # simulate delay
        output = f"Simulated output for command: {cmd}\n<...fake data...>"
        print(f"Router output for '{cmd}':\n{output}\n{'-'*50}")

        # Save simulated output just like real one
        save_output_to_file(
            cmd,
            output,
            exam_name,
            session_id,
            student_id,
            hostname,
            base_dir=base_path,
        )

    print("\n✅ Test complete — all selected commands processed.")
    print("Output saved in your output directory structure.")


if __name__ == "__main__":
    main()
