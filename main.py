from serial_utils import (
    connect_to_serial,
    send_command,
    enter_enable_mode,
    enter_config_mode,
    logout,
    close_connection,
)


def main():
    # (Linux: /dev/ttyUSB0, Mac: /dev/cu.usbserial, Windows: COM3)
    port = "/dev/ttyUSB0"
    baudrate = 9600

    # Connect to the serial port
    ser = connect_to_serial(port, baudrate)
    if not ser:
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
