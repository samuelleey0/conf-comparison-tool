from asyncio import Timeout

import serial
import time
import re
import threading
import logging
from netmiko.netmiko_globals import MAX_BUFFER

READ_TIMEOUT = 8  # seconds
MAX_BUFFER = 4096  # 4KB

DEBUG = False
logger = logging.getLogger("serial_utils")


def dbg(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")


def _reset_buffers(ser):
    """Reset input/output buffers with fallbacks for older pyserial versions."""
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception:
        try:
            ser.flushInput()
            ser.flushOutput()
        except Exception:
            pass


def connect_to_serial(
    port: str, baudrate: int = 9600, timeout=READ_TIMEOUT, attempts: int = 3
):
    """
    Establish a serial connection to a Cisco device.
    Will retry open+wake up to `attempts` times if no prompt is seen.
    Returns an open Serial object or None.
    """
    dbg(f"Attempting to open serial port: {port} at {baudrate} baud")
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            ser = serial.Serial(
                port=port,  # Console cable device (e.g., /dev/ttyUSB0)
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5,
            )
            dbg(f"Serial port opened successfully (attempt {attempt}).")
            time.sleep(0.05)
            _reset_buffers(ser)

            try:
                output = wait_for_prompt(ser, [">", "#"], timeout=timeout, wake=True)
                dbg(f"Connected. Device prompt: {output.strip().splitlines()[-1]}")
                return ser
            except TimeoutError as e:
                dbg(f"Prompt not found on attempt {attempt}: {e}")
                last_exc = e
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(0.5)
                continue
        except serial.SerialException as e:
            dbg(f"Error opening serial port on attempt {attempt}: {e}")
            last_exc = e
            time.sleep(0.5)
            continue

    dbg(
        f"All {attempts} attempts to open serial port or detect prompt failed on {port}."
    )
    if last_exc and DEBUG:
        dbg(f"Last exception: {last_exc}")
    return None


def wait_for_prompt(ser, expected_prompts, timeout=15, wake=True):
    """
    Read from serial until one of expected_prompts is seen or timeout.
    - expected_prompts: list of strings to detect (e.g., [">", "#", "(config)#"])
    - timeout: total seconds to wait
    - wake: whether to attempt a short wake sequence (CRs) while reading
    Raises TimeoutError on failure.
    """
    start = time.time()
    buffer = b""
    ser.timeout = 0.4
    wake_sent = 0
    last_read_time = time.time()

    # ensure buffers are clear before starting
    _reset_buffers(ser)

    # Send a first wake CR immediately
    if wake:
        try:
            ser.write(b"\r\n")
            ser.flush()
            dbg("Initial wake sent")
            wake_sent = 1
        except Exception as e:
            dbg(f"Error sending initial wake: {e}")

    while time.time() - start < timeout:
        try:
            # read available data (non-blocking up to per-read timeout)
            data = ser.read(1024)
        except Exception:
            data = b""

        if data:
            buffer += data
            last_read_time = time.time()
            # keep buffer bounded
            if len(buffer) > MAX_BUFFER:
                buffer = buffer[-MAX_BUFFER:]
            decoded = buffer.decode(errors="ignore")
            # check each expected prompt at line end
            for prompt in expected_prompts:
                if re.search(rf"{re.escape(prompt)}\s*$", decoded, re.MULTILINE):
                    return decoded
            # handle possible activation text
            if b"Press RETURN to get started" in buffer:
                try:
                    ser.write(b"\r\n")
                    ser.flush()
                except Exception:
                    pass
        else:
            # no data read this cycle: if wake enabled, send additional CRs spaced out
            elapsed = time.time() - start
            # send up to 3 wake CRs distributed within first part of timeout
            if wake and wake_sent < 3 and elapsed >= wake_sent * (timeout / 6.0):
                try:
                    ser.write(b"\r\n")
                    ser.flush()
                    dbg(f"Sent wake CR (#{wake_sent + 1}) after {elapsed:.2f}s")
                    wake_sent += 1
                except Exception as e:
                    dbg(f"Error sending wake CR: {e}")
            time.sleep(0.08)

    # timed out
    dbg(f"wait_for_prompt timed out, buffer:\n{buffer.decode(errors='ignore')}")
    raise TimeoutError(
        f"Did not receive expected prompt(s) {expected_prompts} in {timeout} seconds."
    )

    # def wait_for_prompt(ser, expected_prompts, timeout=15, wake=True):
    """
    Wait for specific prompts from the Cisco device.
    Optionally send a wake-up carriage return first.
    """
    buffer = b""
    start_time = time.time()
    ser.timeout = 1  # Set a short timeout for read operations
    wake_sent = 0
    last_read_time = time.time()

    _reset_buffers(ser)

    prompt_found = False
    print(
        f"[DEBUG] Serial settings: port={ser.port}, baudrate={ser.baudrate}, timeout={ser.timeout}"
    )

    if wake and not buffer:
        ser.reset_input_buffer()  # clear junk buffer
        time.sleep(2)  # wait for device to stabilize
        max_attempts = 5
        for attempt in range(max_attempts):
            ser.write(b"\r")  # wake terminal
            ser.flush()
            print(
                f"[DEBUG] Sent wake-up carriage return to device (attempt {attempt+1}/{max_attempts})."
            )
            time.sleep(2)  # wait for response
            wake_buffer = b""
            while ser.in_waiting > 0:
                wake_buffer += ser.read(1024)
            decoded = wake_buffer.decode(errors="ignore")
            print(f"[DEBUG] Wake attempt {attempt+1} captured:\n{decoded}")
            if any(
                re.search(rf"{re.escape(prompt)}\s*$", decoded, re.MULTILINE)
                for prompt in expected_prompts
            ):
                print(
                    f"[DEBUG] Device awake, found prompt: {decoded.strip().splitlines()[-1]}"
                )
                buffer += wake_buffer
                prompt_found = True
                break
        if not prompt_found:
            print(
                "[DEBUG] No prompt detected after wake attempts, proceeding to wait for prompt."
            )
        print("[DEBUG] Sent wake-up carriage return to device.")

    dbg(f"[DEBUG] Waiting for prompts: {expected_prompts} (timeout: {timeout}s)")
    prompt_found = False
    dbg(
        f"Serial settings: port={ser.port}, baudrate={ser.baudrate}, timeout={ser.timeout}"
    )

    if wake:
        try:
            try:
                ser.reset_input_buffer()  # clear junk buffer
            except Exception:
                try:
                    ser.flushInput()
                except Exception:
                    pass
            time.sleep(0.1)  # wait for device to stabilize
            wake_attempts = 3
            for attempt in range(wake_attempts):
                ser.write(b"\r")  # wake terminal
                ser.flush()
                dbg(
                    f"Sent wake-up carriage return to device (attempt {attempt+1}/{wake_attempts})."
                )
                time.sleep(0.25)  # wait for response
                if ser.in_waiting:
                    wake_buffer = ser.read(ser.in_waiting or 1)
                    decoded = wake_buffer.decode(errors="ignore")
                    dbg(f"Wake attempt {attempt+1} captured:\n{decoded}")
                    if any(
                        re.search(rf"{re.escape(prompt)}\s*$", decoded, re.MULTILINE)
                        for prompt in expected_prompts
                    ):
                        dbg(
                            f"Device awake, found prompt: {decoded.strip().splitlines()[-1]}"
                        )
                        buffer += wake_buffer
                        prompt_found = True
                        break
        except Exception as e:
            dbg(f"Error during wake attempts: {e}")

    dbg(f"Waiting for prompts: {expected_prompts} (timeout: {timeout}s)")

    if buffer:
        decoded = buffer.decode(errors="ignore")
        for prompt in expected_prompts:
            if re.search(rf"{re.escape(prompt)}\s*$", decoded, re.MULTILINE):
                print(f"[DEBUG] Found prompt: {prompt}")

                # Send logout until 'Press RETURN to get started' is seen
                for _ in range(5):
                    ser.write(b"logout\n")
                    ser.flush()
                    time.sleep(0.5)
                    logout_buffer = b""
                    while ser.in_waiting > 0:
                        logout_buffer += ser.read(1024)
                    if b"Press RETURN to get started" in logout_buffer:
                        print(
                            "[DEBUG] Detected 'Press RETURN to get started' after logout."
                        )
                        ser.write(b"\r")  # wake terminal again
                        ser.flush()
                        time.sleep(1)

                        # Read any prompt after activation
                        activation_buffer = b""
                        while ser.in_waiting > 0:
                            activation_buffer += ser.read(1024)
                        buffer += activation_buffer
                        return buffer.decode(errors="ignore")
                    raise TimeoutError(
                        "Did not receive 'Press RETURN to get started' after logout attempts."
                    )

                for _ in range(3):
                    ser.write(b"logout\n")
                    ser.flush()
                    time.sleep(0.2)
                    logout_buffer = b""
                    if ser.in_waiting:
                        logout_buffer += ser.read(ser.in_waiting or 1)
                    if b"Press RETURN to get started" in logout_buffer:
                        dbg(
                            "[DEBUG] Detected 'Press RETURN to get started' after logout."
                        )
                        ser.write(b"\r\n")
                        ser.flush()
                        time.sleep(0.2)
                        activation_buffer = b""
                        while ser.in_waiting > 0:
                            activation_buffer += ser.read(ser.in_waiting or 1)
                            buffer += activation_buffer
                        return buffer.decode(errors="ignore")
                dbg("Logout attempts did not produce activation; continue to wait.")

    print(f"[DEBUG] Waiting for prompts: {expected_prompts} (timeout: {timeout}s)")
    while time.time() - start_time < timeout:
        if ser.in_waiting > 0:
            print(f"[DEBUG] ser.in_waiting={ser.in_waiting}")
            data = ser.read(1024)
            print(f"[DEBUG] ser.read(1024) returned {len(data)} bytes")
            if data:
                buffer += data
                print(f"[DEBUG] Current buffer size: {len(buffer)} bytes")
                decoded = buffer.decode(errors="ignore")
                for prompt in expected_prompts:
                    if re.search(rf"{re.escape(prompt)}\s*$", decoded, re.MULTILINE):
                        print(f"[DEBUG] Found prompt: {prompt}")
                        for _ in range(5):
                            ser.write(b"logout\n")
                            ser.flush()
                            time.sleep(0.5)
                            logout_buffer = b""
                            while ser.in_waiting > 0:
                                logout_buffer += ser.read(1024)
                            if b"Press RETURN to get started" in logout_buffer:
                                print(
                                    "[DEBUG] Detected 'Press RETURN to get started' after logout."
                                )
                                ser.write(b"\r")
                                ser.flush()
                                time.sleep(1)
                                activation_buffer = b""
                                while ser.in_waiting > 0:
                                    activation_buffer += ser.read(1024)
                                buffer += activation_buffer
                                return buffer.decode(errors="ignore")
                        raise TimeoutError(
                            "Did not receive 'Press RETURN to get started' after logout attempts."
                        )
                # Only truncate buffer after checking for prompts
                if len(buffer) > MAX_BUFFER:
                    buffer = buffer[-MAX_BUFFER:]  # keep only last MAX_BUFFER bytes
                    print(f"[DEBUG] Buffer exceeded {MAX_BUFFER} bytes, truncating.")

                print(f"[DEBUG] Received data: {data.decode(errors='ignore')}")
        else:
            time.sleep(0.1)  # Avoid busy waiting
    while time.time() - start_time < timeout:
        try:
            if ser.in_waiting > 0:
                dbg(f"ser.in_waiting={ser.in_waiting}")
                data = ser.read(ser.in_waiting or 1)
                dbg(f"ser.read() returned {len(data)} bytes")
                if data:
                    buffer += data
                    if len(buffer) > MAX_BUFFER:
                        buffer = buffer[-MAX_BUFFER:]
                        dbg(f"Buffer exceeded {MAX_BUFFER} bytes, truncating.")
                    decoded = buffer.decode(errors="ignore")
                    for prompt in expected_prompts:
                        if re.search(
                            rf"{re.escape(prompt)}\s*$", decoded, re.MULTILINE
                        ):
                            dbg(f"Found prompt: {prompt}")
                            return buffer.decode(errors="ignore")
            else:
                time.sleep(0.05)
        except Exception as e:
            dbg(f"Read loop error: {e}")
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
    ser.timeout = 0.1  # Faster polling

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
    output = send_command(
        ser, "show running-config | include hostname", timeout=timeout
    )

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
            try:
                ser.write(logout_cmd)
                ser.flush()
                time.sleep(0.2)
            except Exception as e:
                pass
        _reset_buffers(ser)

        # # Send break to force disconnect
        # ser.send_break(duration=0.15)
        # time.sleep(0.1)

        # # Send additional exits to ensure cleanup
        # for _ in range(2):
        #     ser.write(b"\x03\r\n")  # Ctrl-C + Enter
        #     ser.flush()
        #     time.sleep(0.1)

        # # Clear any remaining buffer
        # ser.reset_input_buffer()
        # ser.reset_output_buffer()

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
            ser.write(b"\x03")  # Send Ctrl-C
            ser.flush()
            time.sleep(0.2)

        ser.write(b"\x1a")  # Send Ctrl-Z
        ser.flush()
        time.sleep(0.5)

        ser.write(b"\x1b")  # Send ESC
        ser.flush()
        time.sleep(0.3)

        # Try to exit from any mode
        for exit_cmd in [b"exit\r\n", b"quit\r\n"]:
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
            try:
                ser.send_break(duration=0.2)
                time.sleep(0.2)
            except Exception as e:
                pass

            # Control hardware lines to signal disconnect
            try:
                ser.dtr = False  # Data Terminal Ready
                ser.rts = False  # Request To Send
                time.sleep(0.2)
                ser.dtr = True
                ser.rts = True
            except Exception as e:
                pass
            _reset_buffers(ser)

            # Close the connection
            try:
                ser.close()
            except Exception as e:
                pass
            time.sleep(0.15)
            dbg("Serial connection closed with proper cleanup.")
        except Exception as e:
            dbg(f"Error during logout/close: {e}")
    else:
        print("[!] No open serial connection to close.")
