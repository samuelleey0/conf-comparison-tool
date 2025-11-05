def choose_connection_type():
    print("Choose connection type:")
    print("1. Serial")
    print("2. Remote")
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
            or "/dev/cu.usbserial-10"
        )
    elif choice == "4":
        port = input("Enter your serial device path (e.g., /dev/ttyACM0): ").strip()
    else:
        print("Invalid choice. Using default: /dev/ttyUSB0")
        port = "/dev/ttyUSB0"
    return port


def remote_credentials():
    """
    Prompt for remote (telnet) connection info.

    Returns: (host, port, telnet_password, enable_password)
    - telnet_password: password for the telnet login (may be blank)
    - enable_password: privilege EXEC (enable) password (may be blank)
    """
    host = input("Enter device IP: ").strip()
    remote_password = input("Enter password: ").strip()
    enable_password = input(
        "Enable (exec) password (leave blank to reuse telnet password): "
    ).strip()
    if not enable_password:
        enable_password = remote_password  # reuse telnet password if not provided
    return host, remote_password, enable_password
