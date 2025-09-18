from serial_utils import (
    connect_to_serial,
    send_command,
    enter_enable_mode,
    enter_config_mode,
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
    baudrate = 9600

    # Connect to the serial port
    ser = connect_to_serial(port, baudrate)
    if not ser:
        print("[-] Failed to connect to the serial port.")
        return

    # Enter enable mode
    print("[*] Entering enable mode...")
    output = enter_enable_mode(ser)
    print(output)

    # Enter config mode
    print("[*] Entering config mode...")
    output = enter_config_mode(ser)
    print(output)

    # Example command
    response = send_command(ser, "show run")
    print(response)

    logout(ser)
    close_connection(ser)


if __name__ == "__main__":
    main()
