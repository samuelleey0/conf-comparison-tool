import serial
import time
import re

READ_TIMEOUT = 8  # seconds


def connect_to_serial(port: str, baudrate: int = 9600, timeout=READ_TIMEOUT):
    """
    Establish a serial connection to Cisco device.
    """
    print(f"[DEBUG] Attempting to open serial port: {port} at {baudrate} baud")
    try:
        ser = serial.Serial(
            port=port,  # Console cable device (e.g., /dev/ttyUSB0)
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1,
        )
        print("[DEBUG] Serial port opened successfully.")
        # Wake up CLI
        ser.write(b"\n")
        print("[DEBUG] Sent wake-up newline to device.")
        time.sleep(3)  # Give router some time after opening
        ser.reset_input_buffer()
        print("[DEBUG] Input buffer reset. Waiting for prompt...")
        output = wait_for_prompt(ser, [">", "#"], timeout=timeout)
        print(f"[+] Connected. Device prompt: {output.strip().splitlines()[-1]}")
        return ser
    except serial.SerialException as e:
        print(f"[!] Error communicating...: {e}")
        return None


def wait_for_prompt(ser, expected_prompts, timeout=15):
    """
    Wait for specific prompts from the Cisco device.
    """
    buffer = b""
    start_time = time.time()
    ser.timeout = 0.1  # Set a short timeout for read operations
    print(f"[DEBUG] Serial settings: port={ser.port}, baudrate={ser.baudrate}, timeout={ser.timeout}")
    print(f"[DEBUG] Waiting for prompts: {expected_prompts} (timeout: {timeout}s)")

    while time.time() - start_time < timeout:
        data = ser.read(1024)  # Read up to 1024 bytes
        print(f"[DEBUG] ser.read(1024) returned {len(data)} bytes")
        if data:
            buffer += data
            # Debugging: see what's coming in
            print(f"[DEBUG] Received data: {data.decode(errors='ignore')}")
            for prompt in expected_prompts:
                if prompt.encode() in buffer:
                    print(f"[DEBUG] Found prompt: {prompt}")
                    print(f"[DEBUG] Full buffer:\n{buffer.decode(errors='ignore')}")
                    return buffer.decode(errors="ignore")
        else:
            time.sleep(0.05)  # Avoid busy waiting
    print(f"[DEBUG] Final buffer before timeout:\n{buffer.decode(errors='ignore')}")
    raise TimeoutError(
        f"Did not receive expected prompt(s) {expected_prompts} in {timeout} seconds."
    )


def send_command(ser, command, expected_prompt=b"#", timeout=20):
    """
    Send a command to the Cisco device and read response.
    """
    ser.write(command.encode("utf-8") + b"\n")
    ser.flush()
    buffer = b""
    start_time = time.time()
    ser.timeout = 0.1 # Faster polling

    while time.time() - start_time < timeout:
        data = ser.read(1024)
        if not data:
            continue
        buffer += data
        if expected_prompt.encode() in buffer:
            break

    output = buffer.decode("utf-8", errors="ignore")
    # Clean junk (--More--, ANSI codes, etc.)
    output = re.sub(r"--More--", "", output)
    output = re.sub(r"\x1b\[.*?[@-~]", "", output)
    return output.strip()


def disable_paging(ser, prompt=b"#", timeout=5):
    """
    Disable paging on Cisco device to get full output.
    """
    send_command(ser, "terminal length 0", expected_prompt=prompt, timeout=timeout)


def enter_enable_mode(ser, timeout=5):
    """
    Enter privileged EXEC mode (> to #).
    """
    output = send_command(ser, "enable", expected_prompt="#", timeout=timeout)
    return output


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
