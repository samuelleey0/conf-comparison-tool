import serial
import time
import re
import threading

from netmiko.netmiko_globals import MAX_BUFFER

READ_TIMEOUT = 8  # seconds
MAX_BUFFER = 4096  # 4KB

# def wake_device(ser, expected_prompts=[">", "#"], attempts=5, delay=1):
#     """
#     Wake the Cisco device by sending carriage returns and Ctrl-C until a prompt is detected.
#     Prevents timeout if the device was idle too long.
#     """
#     buffer = b""
#     ser.timeout = 1
#     for i in range(attempts):
#         # Send carriage return
#         ser.write(b"\r")
#         ser.flush()
#         time.sleep(delay)
#
#         # Try Ctrl-C as backup
#         if i == 2:
#             ser.write(b"\x03")  # Ctrl-C
#             ser.flush()
#             time.sleep(0.5)
#         elif i == 4:
#             ser.write(b"\x1a")  # Ctrl-Z (back to exec mode)
#             ser.flush()
#             time.sleep(0.5)
#
#         # Read response
#         data = ser.read(1024)
#         if data:
#             buffer += data
#             decoded = buffer.decode(errors="ignore")
#             print(f"[DEBUG] Wake attempt {i+1}, received: {decoded}")
#
#             # Check for prompt
#             for prompt in expected_prompts:
#                 if prompt in decoded:
#                     print(f"[+] Device woke up, found prompt: {prompt}")
#                     return True
#
#     print("[!] Failed to wake device, no prompt found.")
#     return False


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

        # Clear any existing session first
        clear_session(ser)

        # Wake device before waiting for prompt
        # if not wake_device(ser):
        #     print("[!] Warning: Device may still be idle or stuck.")

        output = wait_for_prompt(ser, [">", "#"], timeout=timeout, wake=True)
        print(f"[+] Connected. Device prompt: {output.strip().splitlines()[-1]}")
        return ser
    except serial.SerialException as e:
        print(f"[!] Error communicating...: {e}")
        return None


def wait_for_prompt(ser, expected_prompts, timeout=15, wake=True):
    """
    Wait for specific prompts from the Cisco device.
    Optionally send a wake-up carriage return first.
    """
    buffer = b""
    start_time = time.time()
    ser.timeout = 1  # Set a short timeout for read operations
    print(f"[DEBUG] Serial settings: port={ser.port}, baudrate={ser.baudrate}, timeout={ser.timeout}")

    if wake and not buffer:
        ser.reset_input_buffer() # clear junk buffer
        time.sleep(2) # wait for device to stabilize
        ser.write(b"\r") # wake terminal
        ser.flush()
        print("[DEBUG] Sent wake-up carriage return to device.")

    print(f"[DEBUG] Waiting for prompts: {expected_prompts} (timeout: {timeout}s)")

    # Stop at here

    while time.time() - start_time < timeout:
        if ser.in_waiting > 0:
            print(f"[DEBUG] ser.in_waiting={ser.in_waiting}")
            data = ser.read(1024)
            print(f"[DEBUG] ser.read(1024) returned {len(data)} bytes")
            if data:
                buffer += data
                # Check buffer size
                print(f"[DEBUG] Current buffer size: {len(buffer)} bytes")

                if len(buffer) > MAX_BUFFER:
                    buffer = buffer[-MAX_BUFFER:] # keep only last MAX_BUFFER bytes
                    print(f"[DEBUG] Buffer exceeded {MAX_BUFFER} bytes, truncating.")

                decoded = buffer.decode(errors='ignore')
                # Debugging: see what's coming in
                print(f"[DEBUG] Received data: {data.decode(errors='ignore')}")

                for prompt in expected_prompts:
                    if re.search(rf"{re.escape(prompt)}\s*$", decoded, re.MULTILINE):
                        print(f"[DEBUG] Found prompt: {prompt}")
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

def get_hostname(ser, timeout=5):
    """
    Extract hostname from device using CLI prompt.
    Returns a string like 'R1' or 'SW1'.
    """
    output = send_command(ser, "show running-config | include hostname", timeout=timeout)

    for line in output.splitlines():
        if line.strip().startswith("hostname"):
            return line.split()[1]  # e.g. 'R1'
    return "CiscoDevice"


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


def logout(ser, timeout=2):
    """
    Log out from the device.
    """
    try:
        # Send Ctrl+C first to interrupt any running command
        ser.write(b"\x03")
        ser.flush()
        time.sleep(0.1)

        # If in enable (#) mode, exit to user (>) mode first
        ser.write(b"exit\n")
        ser.flush()
        time.sleep(0.2)

        # Logout from user mode
        for logout_cmd in [b"logout\r\n", b"exit\r\n", b"quit\r\n"]:
            ser.write(logout_cmd)
            ser.flush()
            time.sleep(0.2)

        # Send break to force disconnect
        ser.send_break(duration=0.15)
        time.sleep(0.1)

        # Send additional exits to ensure cleanup
        for _ in range(2):
            ser.write(b"\x03\r\n") # Ctrl-C + Enter
            ser.flush()
            time.sleep(0.1)

        # Clear any remaining buffer
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print("[DEBUG] Thorough logout sequence completed.")
    except Exception as e:
        print(f"[DEBUG] Error during logout: {e}")

def clear_session(ser):
    """
    Clear any existing session by sending multiple newlines.
    """
    try:
        # Clear input/output buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # Send break signal
        ser.send_break(duration=0.25)
        time.sleep(1)

        # Send multiple carriage returns and exits
        for _ in range(3):
            ser.write(b"\x03") # Send Ctrl-C
            ser.flush()
            time.sleep(0.2)

        ser.write(b"\x1a") # Send Ctrl-Z
        ser.flush()
        time.sleep(0.5)

        ser.write(b"\x1b") # Send ESC
        ser.flush()
        time.sleep(0.3)

        # Try to exit from any mode
        for exit_cmd in [ b"exit\r\n", b"quit\r\n"]:
            ser.write(exit_cmd)
            ser.flush()
            time.sleep(0.5)

        # Final buffer clear
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print("[DEBUG] Aggressive Session cleared.")
    except Exception as e:
        print(f"[DEBUG] Error during session clear: {e}")


def logout_close_connection(ser):
    """
    Close the serial connection.
    """
    if ser and ser.is_open:
        try:
            # First, attempt to logout cleanly
            logout(ser)

            # Send break signal to force session termination
            ser.send_break(duration=0.2)
            time.sleep(0.2)

            # Control hardware lines to signal disconnect
            ser.dtr = False # Data Terminal Ready
            ser.rts = False # Request To Send
            time.sleep(0.2)
            ser.dtr = True
            ser.rts = True

            # Clear all buffers
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Close the connection
            ser.close()
            print("[+] Serial connection closed with proper cleanup.")
        except Exception as e:
            print(f"[!] Error closing serial connection: {e}")
            # Force close if error occurs
            try:
                ser.close()
                print("[+] Serial connection force-closed.")
            except:
                pass
    else:
        print("[!] No open serial connection to close.")
