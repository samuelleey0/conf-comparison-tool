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
        for _ in range(3):
            ser.write(b"\r")
            print("[DEBUG] Sent wake-up newline to device.")
            time.sleep(1.5)
        # ser.timeout = 2
        # response = ser.read(1024)
        # print(f"[DEBUG] Output from device after wake-up:{response.decode(errors='ignore')}")
        # if not response:
        #     print("[!] No response from device. Check connections and settings.")
        #     close_connection(ser)
        #     return None
        time.sleep(3)  # Give router some time after opening
        print("[DEBUG] Device is up. Waiting for prompt...")
        print(f"[DEBUG] Bytes waiting in buffer before wait_for_prompt: {ser.in_waiting}")
        print("[DEBUG] Testing raw read for 5 seconds...")
        start = time.time()
        while time.time() - start < 5:
            data = ser.read(1024)
            if data:
                print("[DEBUG][RAW]", data)
            else:
                time.sleep(0.1)
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
    ser.timeout = 1  # Set a short timeout for read operations
    print(f"[DEBUG] Serial settings: port={ser.port}, baudrate={ser.baudrate}, timeout={ser.timeout}")
    print(f"[DEBUG] Waiting for prompts: {expected_prompts} (timeout: {timeout}s)")

    prompt_pattern = re.compile(r"^.*[>#]\s*$", re.MULTILINE)

    while time.time() - start_time < timeout:
        print(f"[DEBUG] ser.in_waiting={ser.in_waiting}")
        data = ser.read(1024)  # Read up to 1024 bytes
        print(f"[DEBUG] ser.read(1024) returned {len(data)} bytes")
        if data:
            buffer += data
            decoded = buffer.decode(errors='ignore')
            # Debugging: see what's coming in
            print(f"[DEBUG] Received data: {data.decode(errors='ignore')}")
            for prompt in expected_prompts:
                if prompt in decoded:
                    print(f"[DEBUG] Found prompt: {prompt}")
                    print(f"[DEBUG] Full buffer:\n{decoded}")
                    return decoded
        else:
            time.sleep(0.1)  # Avoid busy waiting
    print(f"[DEBUG] Final buffer before timeout:\n{buffer.decode(errors='ignore')}")
    raise TimeoutError(
        f"Did not receive expected prompt(s) {expected_prompts} in {timeout} seconds."
    )


def send_command(ser, command, expected_prompt="#", timeout=20):
    """
    Send a command to the Cisco device and read response.
    """
    if isinstance(command, str):
        ser.write(command.encode("utf-8") + b"\n")
    else:
        ser.write(command + b"\n")
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


def disable_paging(ser, prompt="#", timeout=5):
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
    output = send_command(ser, "exit", expected_prompt=">", timeout=timeout)
    print(output)
    return output


def close_connection(ser):
    """
    Close the serial connection.
    """
    if ser and ser.is_open:
        ser.close()
        print("[+] Serial connection closed.")
    else:
        print("[!] No open serial connection to close.")
