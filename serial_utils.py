import serial
import time

READ_TIMEOUT = 8  # seconds


def connect_to_serial(port: str, baudrate: int = 9600, timeout=READ_TIMEOUT):
    """
    Establish a serial connection to Cisco device.
    """
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            parity="N",
            stopbits=1,
            bytesize=8,
            timeout=timeout,
        )
        if ser.is_open:
            print(f"[+] Communication established to {ser.name}")
            return ser
    except serial.SerialException as e:
        print(f"[!] Error communicating...: {e}")
        return None


def send_command(ser, command, delay=1):
    """
    Send a command to the Cisco device and read response.
    """
    ser.write((command + "\r\n").encode())
    time.sleep(delay)
    return ser.read(ser.inWaiting()).decode(errors="ignore")


def enter_enable_mode(ser, enable_password=None, delay=1):
    """
    Enter privileged EXEC mode.
    """
    output = send_command(ser, "enable", delay)
    if "Password" in output and enable_password:
        ser.write((enable_password + "\r\n").encode())
        time.sleep(delay)
        output = ser.read(ser.inWaiting()).decode(errors="ignore")
    return output


def enter_config_mode(ser, delay=1):
    """
    Enter global configuration mode.
    """
    return send_command(ser, "configure terminal", delay)


def logout(ser, delay=1):
    """
    Exit session gracefully.
    """
    send_command(ser, "exit", delay)
    print("[+] Logged out from device")


def close_connection(ser):
    """
    Close the serial connection.
    """
    if ser and ser.is_open:
        ser.close()
        print("[+] Serial connection closed.")
    else:
        print("[!] No open serial connection to close.")
