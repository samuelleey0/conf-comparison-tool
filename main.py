import time
from asyncio import timeout

from serial_utils import (
    connect_to_serial,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    get_hostname,
)
from remote_utils import (
    connect_ssh,
    disable_paging_ssh,
    enter_enable_mode_ssh,
    send_command_ssh,
    get_hostname_ssh,
)
from ui_utils import choose_connection_type, choose_serial_port, ssh_credentials

from file_utils import save_output_to_file, build_base_path


def main():
    # List of commands to run
    commands = ["show running-config", "show ip interface brief"]

    # === Get path info dynamically ===
    path_info = build_base_path()
    if path_info is None:
        return

    exam_name = path_info["exam_name"]
    session_id = path_info["session_id"]
    student_id = path_info["student_id"]
    base_path = path_info["base_path"]

    conn_type = choose_connection_type()

    if conn_type == "1":
        port = choose_serial_port()
        ser = connect_to_serial(port)
        if ser is None:
            print("[+] Failed to connect to device. Exiting.")
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

            for cmd in commands:
                print(f"[*] Sending '{cmd}'...")
                output = send_command(ser, cmd, timeout=30)
                save_output_to_file(
                    cmd,
                    output,
                    exam_name,
                    session_id,
                    student_id,
                    hostname,
                    base_dir=base_path,
                )
                print(f"Router output for '{cmd}':\n{output}\n{'-'*50}")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            logout_close_connection(ser)
    elif conn_type == "2":
        host, username, password = ssh_credentials()
        client, shell = connect_ssh(host, username, password)
        if client is None or shell is None:
            print("[+] Failed to connect via SSH. Exiting.")
            exit(1)
        try:
            print("[*] Entering enable mode...")
            output = enter_enable_mode_ssh(shell)
            print(output)
            print("[*] Disabling paging...")
            disable_paging_ssh(shell)

            # Get hostname first
            hostname = get_hostname_ssh(shell)
            print(f"[+] Detected hostname: {hostname}")

            for cmd in commands:
                print(f"[*] Sending '{cmd}'...")
                output = send_command_ssh(shell, cmd, timeout=30)
                save_output_to_file(
                    cmd,
                    output,
                    exam_name,
                    session_id,
                    student_id,
                    hostname,
                    base_dir=base_path,
                )
                print(f"Router output for '{cmd}':\n{output}\n{'-'*50}")
            print("Router output:\n", output)

        except Exception as e:
            print(f"Error: {e}")
        finally:
            shell.close()
            client.close()
            print("[+] SSH connection closed.")
    else:
        print("Invalid choice.")


if __name__ == "__main__":
    main()
