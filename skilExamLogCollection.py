from serial.serialjava import device

from serial_utils import (
    connect_to_serial,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    get_hostname,
)
from ui_utils import choose_serial_port
from file_utils import save_output_to_file, build_base_path

def choose_device_type():
    """
    Let user choose between switch or router with predefined commands.
    """
    print("\n=== Device Type Selection ===")
    print("1. Switch")
    print("2. Router")

    while True:
        choice = input("Enter choice (1-2): ").strip()
        if choice == "1":
            return "switch", get_switch_commands()
        elif choice == "2":
            return "router", get_router_commands()
        else:
            print("Invalid choice. Please enter 1 or 2.")

def get_switch_commands():
    """
    Return a list of commands for switch devices.
    """
    return [
        "show running-config",
        "show vlan brief",
        "show spanning-tree",
        "show ip interface brief",
    ]

def get_router_commands():
    """
    Return a list of commands for router devices.
    """
    return [
        "show running-config",
        "show ip interface brief",
        "show ip route",
    ]

def main():
    print("=== Skill Exam Cisco Log Collection System ===")

    # Get device type and commands
    device_type, commands = choose_device_type()
    print(f"[+] Selected device type: {device_type.capitalize()}")
    print(f"[+] Commands to be executed: {len(commands)} commands")

    # Get path info dynamically
    path_info = build_base_path()
    if path_info is None:
        return

    exam_name = path_info["exam_name"]
    session_id = path_info["session_id"]
    student_id = path_info["student_id"]
    base_path = path_info["base_path"]

    # Serial connection only
    port = choose_serial_port()
    ser = connect_to_serial(port)
    if ser is None:
        print("[!] Failed to connect to device. Exiting.")
        exit(1)
    try:
        print("[*] Entering enable mode...")
        output = enter_enable_mode(ser)
        print(output)
        print("[*] Disabling paging...")
        disable_paging(ser)

        # Get hostname first
        hostname = get_hostname(ser)
        print(f"[+] Detected hostname: {hostname}")
        print(f"[+] Device type: {device_type.upper()}")

        for i, cmd in enumerate(commands, 1):
            print(f"[*] Executing command {i}/{len(commands)}: '{cmd}'...")
            output = send_command(ser, cmd, timeout=30)
            save_output_to_file(cmd, output, exam_name, session_id, student_id, hostname, base_dir=base_path)
            print(f"[+] Command '{cmd}' executed and saved.\n")
            print(f"Router output for '{cmd}':\n{output}\n{'-'*50}")

    except Exception as e:
        print(f"[!] Error: {e}")
    finally:
        logout_close_connection(ser)
        print("[+] Serial connection closed.")

    print(f"\n [+] All commands completed for {device_type.upper()}: '{hostname}'.")
    print(f"[+] Logs saved in: {base_path}/{hostname}/")

if __name__ == "__main__":
    main()