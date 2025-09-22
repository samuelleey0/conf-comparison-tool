from serial_utils import (
    connect_to_serial,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout,
    close_connection,
)


def choose_serial_port():
    print("Select your serial interface:")
    print("1. USB-to-Serial (USB-A or USB-C)")
    print("2. Built-in Serial Port (DB9/RS232)")
    print("3. Custom (enter your own device path)")
    choice = input("Enter choice (1-3): ").strip()

    if choice == "1":
        # Common USB-to-Serial device names
        port = (
            input("Enter device path (default: /dev/ttyUSB0): ").strip()
            or "/dev/ttyUSB0"
        )
    elif choice == "2":
        # Common direct serial port device names
        port = (
            input("Enter device path (default: /dev/ttyS0): ").strip() or "/dev/ttyS0"
        )
    elif choice == "3":
        port = input("Enter your serial device path (e.g., /dev/ttyACM0): ").strip()
    else:
        print("Invalid choice. Using default: /dev/ttyUSB0")
        port = "/dev/ttyUSB0"
    return port


def main():
    # (Linux: /dev/ttyUSB0, Mac: /dev/cu.usbserial, Windows: COM3)
    port = choose_serial_port()

    # Connect to the serial port
    ser = connect_to_serial(port)
    if not ser:
        print("[-] Failed to connect to the serial port.")
        return

    try:
        # Enter enable mode
        print("[*] Entering enable mode...")
        output = enter_enable_mode(ser)
        print(output)

        print("[*] Disabling paging...")
        disable_paging(ser)

        print("[*] Sending 'show running-config'...")
        output = send_command(ser, "show running-config", prompt=b"#", timeout=60)
        print("Router output:\n", output)

        logout(ser)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        close_connection(ser)


if __name__ == "__main__":
    main()
