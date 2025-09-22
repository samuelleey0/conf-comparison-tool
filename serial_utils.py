import serial
import time

READ_TIMEOUT = 8  # seconds


def connect_to_serial(port: str, baudrate: int = 9600, timeout=READ_TIMEOUT):
    """
    Establish a serial connection to Cisco device.
    """
    try:
        ser = serial.Serial(
            port="/dev/ttyUSB0",  # your console cable device
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1,
        )
        output = wait_for_prompt(ser, [">", "#"], timeout=timeout)
        print(f"[+] Connected. Device prompt: {output.strip().splitlines()[-1]}")
        return ser
    except serial.SerialException as e:
        print(f"[!] Error communicating...: {e}")
        return None


def wait_for_prompt(ser, expected_prompts, timeout=10):
    """
    Wait for specific prompts from the Cisco device.
    """
    buffer = b""
    start_time = time.time()
    ser.timeout = 0.5  # Set a short timeout for read operations
    while time.time() - start_time < timeout:
        data = ser.read(1024)  # Read up to 1024 bytes
        if data:
            buffer += data
            for prompt in expected_prompts:
                if prompt in expected_prompts:
                    if prompt.encode() in buffer:
                        return buffer.decode(errors="ignore")
        else:
            time.sleep(0.1)  # Avoid busy waiting
    raise TimeoutError(
        f"Did not receive expected prompt(s) {expected_prompts} in {timeout} seconds."
    )


def send_command(ser, command, expected_prompt=">", timeout=5, delay=0.2):
    """
    Send a command to the Cisco device and read response.
    """
    ser.write((command + "\r\n").encode())
    ser.flush()
    time.sleep(delay)
    return wait_for_prompt(ser, [expected_prompt], timeout=timeout)


def enter_enable_mode(ser, enable_password=None, timeout=5):
    """
    Enter privileged EXEC mode (> to #).
    """
    output = send_command(ser, "enable", expected_prompt="#", timeout=timeout)
    return output


def enter_config_mode(ser, timeout=5):
    """
    Enter global configuration mode (# to (config)#).
    """
    return send_command(
        ser, "configure terminal", expected_prompt="(config)#", timeout=timeout
    )


def logout(ser, timeout=5):
    """
    Log out from the device.
    """
    return send_command(ser, "exit", expected_prompt=">", timeout=timeout)


def close_connection(ser):
    """
    Close the serial connection.
    """
    if ser and ser.is_open:
        ser.close()
        print("[+] Serial connection closed.")
    else:
        print("[!] No open serial connection to close.")
