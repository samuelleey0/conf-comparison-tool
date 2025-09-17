import serial
import time


def connect_to_serial(port: str, baudrate: int = 9600, timeout: int = 1):
    """
    Establish a serial connection to Cisco device.
    """
    try:
        ser = serial.Serial(port, baudrate, timeout=timeout)
        time.sleep(2)  # Wait for the connection to establish and stable
        if ser.is_open:
            print(f"[+] Communication established to {ser.name}")
            return ser
    except serial.SerialException as e:
        print(f"[!] Error communicating...: {e}")
        return None


def send_command(ser, command: str):
    """
    Send a command to the Cisco device and read response.
    """
    if ser is None:
        print("[!] No active serial connection.")
        return None

    try:
        ser.write((command + "\n").encode())  # Send command
        time.sleep(1)  # Wait for the command to be processed
        output = ser.read_all().decode(errors="ignore")
        return output
    except Exception as e:
        print(f"[!] Failed sending command...: {e}")
        return None


def close_connection(ser):
    """
    Close the serial connection.
    """
    if ser and ser.is_open:
        ser.close()
        print("[+] Serial connection closed.")
    else:
        print("[!] No open serial connection to close.")
