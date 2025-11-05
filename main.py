import time

from serial_utils import (
    connect_to_serial,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    get_hostname,
)
from remote_utils import (
    remote_connect,
    disable_paging_remote,
    enter_enable_mode_remote,
    send_command_remote,
    get_hostname_remote,
)
from ui_utils import choose_connection_type, choose_serial_port, remote_credentials

from file_utils import save_output_to_file, build_base_path, del_partial_logs

from command_manager import command_menu


def main():

    # Command checklist to run
    commands = command_menu()
    if not commands:
        print("[!] No commands selected. Exiting.")
        return

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
        retry_count = 0
        max_retries = 3

        while retry_count <= max_retries:
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

                remaining_commands = commands.copy()

                for cmd in remaining_commands[:]:
                    try:
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
                        print(f"[*] Router output for '{cmd}':\n{output}\n{'-'*50}")
                        remaining_commands.remove(cmd)
                    except Exception as e:
                        print(f"[ERROR] Failed to execute command '{cmd}': {e}")
                        print("[INFO] Attempting to reconnect...")
                        break

                if not remaining_commands:
                    print("[INFO] All commands executed successfully.")
                    break
                else:
                    print(
                        f"[WARNING] {len(remaining_commands)} commands remaining. Retrying..."
                    )
                    retry_count += 1
                    print(f"[INFO] Retrying... ({retry_count}/{max_retries})")

            except Exception as e:
                print(f"Error: {e}")
            finally:
                logout_close_connection(ser)
        if retry_count > max_retries and remaining_commands:
            print("[ERROR] Maximum retries reached. Some commands were not executed:")
            del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
            print(
                "[INFO] Partial logs deleted. Please re-run the process to collect all logs."
            )

            retry_count = 0
            commands = remaining_commands

    elif conn_type == "2":
        host, port, username, password = remote_credentials()
        retry_count = 0
        max_retries = 3

        # Keep the list of commands that still need collection
        remaining_commands = commands.copy()

        while retry_count <= max_retries:
            client, shell = remote_connect(host, username, password, port=port)
            if client is None or shell is None:
                print("[+] Failed to connect via Telnet. Exiting.")
                exit(1)
            try:
                print("[*] Entering enable mode...")
                output = enter_enable_mode_remote(shell)
                print(output)
                print("[*] Disabling paging...")
                disable_paging_remote(shell)

                # Get hostname first
                hostname = get_hostname_remote(shell)
                print(f"[+] Detected hostname: {hostname}")

                for cmd in remaining_commands[:]:
                    try:
                        print(f"[*] Sending '{cmd}'...")
                        output = send_command_remote(shell, cmd, timeout=30)
                        save_output_to_file(
                            cmd,
                            output,
                            exam_name,
                            session_id,
                            student_id,
                            hostname,
                            base_dir=base_path,
                        )
                        print(f"[*] Router output for '{cmd}':\n{output}\n{'-'*50}")
                        remaining_commands.remove(cmd)
                    except Exception as e:
                        print(f"[ERROR] Failed to execute command '{cmd}': {e}")
                        print("[INFO] Attempting to reconnect...")
                        break

                if not remaining_commands:
                    print("[INFO] All commands executed successfully.")
                    break
                else:
                    print(
                        f"[WARNING] {len(remaining_commands)} commands remaining. Retrying..."
                    )
                    retry_count += 1
                    print(f"[INFO] Retrying... ({retry_count}/{max_retries})")

            except Exception as e:
                print(f"Error: {e}")
            finally:
                # Close telnet session(s) safely
                try:
                    if shell:
                        shell.close()
                except Exception:
                    pass
                try:
                    if client and client is not shell:
                        client.close()
                except Exception:
                    pass
                print("[+] Telnet connection closed.")

        if retry_count > max_retries and remaining_commands:
            print("[ERROR] Maximum retries reached. Some commands were not executed:")
            del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
            print(
                "[INFO] Partial logs deleted. Will retry collection for remaining commands..."
            )

            # reset retry count and re-run collection for remaining commands only
            retry_count = 0
            commands = remaining_commands
            remaining_commands = commands.copy()
            # loop will naturally re-enter and retry remaining_commands

    else:
        print("Invalid choice.")


if __name__ == "__main__":
    main()
