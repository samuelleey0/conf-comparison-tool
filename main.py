import time
import os
import shutil
import subprocess

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
    toggle_usb_adapter,
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
        host, username, password = remote_credentials()
        iface = input(
            "Enter USB Ethernet adapter interface (e.g. eth1); leave blank to skip toggle: "
        ).strip()
        retry_count = 0
        max_retries = 3

        # Keep the list of commands that still need collection
        remaining_commands = commands.copy()

        while retry_count <= max_retries:
            # attempt logical replug if interface provided
            if iface:
                replug_ok = False
                if os.geteuid() == 0:

                    # running as root: call function directly
                    try:
                        replug_ok = toggle_usb_adapter(iface)
                    except Exception as e:
                        print(f"[WARN] toggle_usb_adapter failed: {e}")
                        replug_ok = False
                else:

                    # not root: try helper script via sudo (must be installed at /usr/local/sbin/replug_usb_eth.py)
                    helper = "/usr/local/sbin/replug_usb_eth.py"
                    if shutil.which("sudo") and os.path.exists(helper):
                        try:
                            subprocess.run(["sudo", helper, iface], check=True)
                            replug_ok = True
                        except subprocess.CalledProcessError as e:
                            print(f"[WARN] replug helper failed (rc={e.returncode})")
                            replug_ok = False
                    else:
                        print(
                            "[WARN] Cannot replug adapter: helper missing or sudo not available."
                        )
                        replug_ok = False

                if not replug_ok:
                    print(
                        "[INFO] Proceeding without logical replug (adapter may already be fine)."
                    )
            client = remote_connect(host, username, password)
            if client is None:
                print("[+] Failed to connect via SSH. Exiting.")
                exit(1)
            try:
                print("[*] Entering enable mode...")
                output = enter_enable_mode_remote(client)
                print(output)
                print("[*] Disabling paging...")
                disable_paging_remote(client)

                # Get hostname first
                hostname = get_hostname_remote(client)
                print(f"[+] Detected hostname: {hostname}")

                for cmd in remaining_commands[:]:
                    try:
                        print(f"[*] Sending '{cmd}'...")
                        output = send_command_remote(client, cmd, timeout=30)
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
                        # clear ARP for this host before retrying (best-effort)
                        # try:
                        #     clear_arp_entry(host)
                        # except Exception:
                        #     pass
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
                # Close SSH session(s) safely
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
                # try:
                #     clear_arp_entry(host)
                # except Exception:
                #     pass
                print("[+] SSH connection closed.")

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
