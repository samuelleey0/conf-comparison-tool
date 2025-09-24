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
import time


def choose_connection_type():
    print("Choose connection type:")
    print("1. Serial")
    print("2. SSH/Remote")
    return input("Enter choice (1-2): ").strip()


def choose_serial_port():
    print("Select your serial interface:")
    print("1. Ubuntu/Linux (USB-to-Serial, e.g. /dev/ttyUSB0)")
    print("2. Windows (COM port, e.g. COM3)")
    print("3. macOS (USB-to-Serial, e.g. /dev/tty.usbserial-xxxx)")
    print("4. Custom (enter your own device path)")
    choice = input("Enter choice (1-4): ").strip()

    if choice == "1":
        # Linux default
        port = (
            input("Enter device path (default: /dev/ttyUSB0): ").strip()
            or "/dev/ttyUSB0"
        )
    elif choice == "2":
        # Windows default
        port = input("Enter device path (default: COM3): ").strip() or "COM3"
    elif choice == "3":
        # macOS default
        port = (
            input("Enter device path (default: /dev/tty.usbserial-0001): ").strip()
            or "/dev/tty.usbserial-0001"
        )
    elif choice == "4":
        port = input("Enter your serial device path (e.g., /dev/ttyACM0): ").strip()
    else:
        print("Invalid choice. Using default: /dev/ttyUSB0")
        port = "/dev/ttyUSB0"
    return port


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
        host = input("Enter device IP address: ").strip()
        username = input("Enter SSH username: ").strip()
        password = input("Enter SSH password: ").strip()
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
