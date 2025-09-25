
import time

from serial_utils import (
    connect_to_serial,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout,
    close_connection,
)
from remote_utils import (
    connect_ssh,
    disable_paging_ssh,
    enter_enable_mode_ssh,
    send_command_ssh,
)
from ui_utils import (
    choose_connection_type,
    choose_serial_port,
    ssh_credentials
)



def main():
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
            print("[*] Sending 'show running-config'...")
            output = send_command(ser, "show running-config", timeout=30)
            print("Router output:\n", output)
            logout(ser)
        except Exception as e:
            print(f"Error: {e}")
        finally:
            close_connection(ser)
    elif conn_type == "2":
        host, username, password = ssh_credentials()
        client, shell = connect_ssh(host, username, password)
        try:
            print("[*] Entering enable mode...")
            output = enter_enable_mode_ssh(shell)
            print(output)
            print("[*] Disabling paging...")
            disable_paging_ssh(shell)
            print("[*] Sending 'show running-config'...")
            output = send_command_ssh(
                shell, "show running-config", expected_prompt="#", timeout=60
            )
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
