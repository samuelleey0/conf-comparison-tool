from serial_utils import send_command

def choose_connection_type():
    print("Choose connection type:")
    print("1. Serial")
    print("2. SSH/Remote")
    return input("Enter choice (1-2): ").strip()


def choose_serial_port():
    print("Select your serial interface:")
    print("1. Ubuntu/Linux RS232 (/dev/ttyS0)")
    print("2. Ubuntu/Linux USB (/dev/ttyUSB0)")
    print("3. Windows (COM port, e.g. COM3)")
    print("4. macOS (USB-to-Serial, e.g. /dev/cu.usbserial-xxxx)")
    print("5. Custom (enter your own device path)")
    choice = input("Enter choice (1-5): ").strip()

    if choice == "1":
        # Linux RS232 default
        port = (
            input("Enter device path (default: /dev/ttyS0): ").strip()
            or "/dev/ttyS0"
        )
    elif choice == "2":
        # Linux USB default
        port = (
            input("Enter device path (default: /dev/ttyUSB0): ").strip()
            or "/dev/ttyUSB0"
        )
    elif choice == "3":
        # Windows default
        port = input("Enter device path (default: COM3): ").strip() or "COM3"
    elif choice == "4":
        # macOS default
        port = (
            input("Enter device path (default: /dev/cu.usbserial-10): ").strip()
            or "/dev/cu.usbserial-10"
        )
    elif choice == "5":
        port = input("Enter your serial device path (e.g., /dev/ttyACM0): ").strip()
    else:
        print("Invalid choice. Using default: /dev/ttyUSB0")
        port = "/dev/ttyUSB0"
    return port

def ssh_credentials():
    host = input("Enter device IP address: ").strip()
    username = input("Enter SSH username: ").strip()
    password = input("Enter SSH password: ").strip()
    return host, username, password