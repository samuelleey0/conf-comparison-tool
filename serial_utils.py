from asyncio import Timeout

import serial
import time
import re
import os
import threading
import logging
from netmiko.netmiko_globals import MAX_BUFFER
import errno

READ_TIMEOUT = 8  # seconds
MAX_BUFFER = 4096  # 4KB

DEBUG = True
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


def _wait_for_port_free(port_path, timeout=3.0, poll=0.2):
    """
    Try to open the device node non‑blocking to check it's not stuck by the kernel/another process.
    Return True when we can open&close it, False on timeout.
    """
    deadline = time.time() + timeout
    flags = os.O_RDWR | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_NONBLOCK", 0)
    while time.time() < deadline:
        try:
            fd = os.open(port_path, flags)
            os.close(fd)
            return True
        except OSError as e:
            # If device node missing, bail fast (higher-level code will retry)
            if e.errno in (errno.ENOENT, errno.ENXIO):
                return False
            # otherwise wait and retry (device busy / driver racing)
            time.sleep(poll)
    return False


def connect_to_serial(
    port: str,
    baudrate: int = 9600,
    timeout=READ_TIMEOUT,
    retry_interval: int = 3,
    max_retries: int = 5,
    status_cb=None,
):
    """
    Establish a serial connection to a Cisco device.
    Handles cases where the serial cable is unplugged or the device is unresponsive.
    - retry_interval: Time (in seconds) to wait between retries.
    - max_retries: Maximum number of retries for the entire connection process.
    Returns an open Serial object or None.
    """

    def emit(message):
        print(message, flush=True)
        if status_cb:
            try:
                status_cb(message)
            except Exception:
                pass

    emit(f"[INFO] Attempting to open serial port: {port} at {baudrate} baud")
    retries = 0
    last_exc = None

    while retries < max_retries:
        try:
            # Check if the serial device exists (Linux/macOS: /dev/ttyUSB0, Windows: COMx)
            if not os.path.exists(port) and not port.startswith("COM"):
                emit(f"[WARNING] Serial device {port} not found. Check connection.")
                retries += 1
                time.sleep(retry_interval)
                continue

            if not os.path.exists(port) and not port.startswith("COM"):
                emit(f"[WARNING] Serial device {port} not found. Check connection.")
                retries += 1
                time.sleep(retry_interval)
                continue

            _wait_for_port_free(port, timeout=3.0, poll=0.2)

            # Attempt to open the serial port
            ser = None
            open_exec = None
            for open_try in range(6):
                try:
                    ser = serial.Serial(
                        port=port,
                        baudrate=baudrate,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=0.5,
                        dsrdtr=False,
                        rtscts=False,
                    )
                    open_exec = None
                    break
                except serial.SerialException as e:
                    open_exec = e
                    dbg(f"Serial open attempt {open_try + 1} failed: {e}")
                    time.sleep(1.0)
                    try:
                        if ser is not None and getattr(ser, "is_open", False):
                            ser.close()
                    except Exception:
                        pass

            if open_exec is not None and ser is None:
                emit(f"[ERROR] Failed to open serial port: {open_exec}")
                last_exc = open_exec
                retries += 1
                time.sleep(retry_interval)
                continue
            try:
                ser.dtr = True  # Ensure DTR is set to True to avoid connection issues
                ser.rts = True  # Ensure RTS is set to True to avoid connection issues
            except Exception as e:
                dbg(f"[Connection] Failed to set DTR/RTS: {e}")

            time.sleep(0.4)
            emit(f"[INFO] Serial port opened successfully (attempt {retries + 1}).")
            time.sleep(0.05)
            _reset_buffers(ser)

            # Attempt to detect the prompt
            try:
                output = wait_for_prompt(ser, [">", "#"], timeout=timeout, wake=True)
                emit(
                    f"[INFO] Connected. Device prompt: {output.strip().splitlines()[-1]}"
                )
                return ser
            except TimeoutError as e:
                dbg(f"Prompt not found on attempt {retries + 1}: {e}")
                logout_close_connection(ser)
                time.sleep(0.5)
                retries += 1
                time.sleep(retry_interval)
                continue

        except serial.SerialException as e:
            emit(f"[ERROR] Error opening serial port on attempt {retries + 1}: {e}")
            last_exc = e
            retries += 1
            time.sleep(retry_interval)
            continue

    emit(
        f"[ERROR] All {max_retries} attempts to open serial port or detect prompt failed on {port}."
    )
    emit("[HINT] Check if the device is plugged in and try again.")
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
                        dbg("Sent RETURN after 'Press RETURN to get started'")
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


def send_command(ser, command, expected_prompt=">", timeout=20):
    """
    Send a command to the Cisco device and read the response.
    Assumes the prompt is '>', but if '(config)#' is detected, sends 'end'
    to exit configuration mode and proceeds to the next command.
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
        decoded = buffer.decode("utf-8", errors="ignore")

        if "(config)#" in decoded:
            print("[INFO] Device entered configuration mode. Exiting to EXEC mode...")
            ser.write(b"end\n")
            ser.flush()
            time.sleep(0.2)
            buffer = b""
            continue
        if expected_prompt in decoded or "#" in decoded:
            decoded = re.sub(r"--More--", "", decoded)
            decoded = re.sub(r"\x1b\[.*?[@-~]", "", decoded)
            return decoded.strip()
    raise TimeoutError(
        f" [ERROR] Command '{command}' timed out after {timeout} seconds."
    )


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
    Enter privileged EXEC mode (> to # or (config)# to #).
    """
    output = send_command(ser, "enable", expected_prompt="#", timeout=timeout)

    if "#" not in output:
        raise Exception("[ERROR] Failed to enter privileged EXEC mode.")

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

        dbg("Thorough logout sequence completed.")
    except Exception as e:
        dbg(f"Error during logout: {e}")


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

        dbg("Aggressive Session cleared.")
    except Exception as e:
        dbg(f"Error during session clear: {e}")


def logout_close_connection(ser):
    """
    Close the serial connection.
    """
    if ser and ser.is_open:
        try:
            # First, attempt to logout cleanly
            logout(ser)

            clear_session(ser)

            time.sleep(0.2)

            # Close the connection
            try:
                ser.close()
            except Exception as e:
                pass
            time.sleep(0.15)
            print("[INFO] Serial connection closed with proper cleanup.")
        except Exception as e:
            print(f"[ERROR] Error during logout/close: {e}")
    else:
        print("[!] No open serial connection to close.")
