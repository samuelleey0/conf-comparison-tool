from asyncio import Timeout

import serial
import time
import re
import os
import threading
import logging
from netmiko.netmiko_globals import MAX_BUFFER

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


def connect_to_serial(
    port: str,
    baudrate: int = 9600,
    timeout=READ_TIMEOUT,
    cable_retry_interval: int = 3,
    connection_retry_interval: int = 1,
    max_cable_retries: int = 10,
    max_connection_retries: int = 5,
):
    """
    Establish a serial connection to a Cisco device.
    Handles cases where the serial cable is unplugged and reconnected.
    - cable_retry_interval: Time (in seconds) to wait between cable detection retries.
    - connection_retry_interval: Time (in seconds) to wait between connection retries.
    - max_cable_retries: Maximum number of retries for detecting the serial cable.
    - max_connection_retries: Maximum number of retries for the connection process.
    Returns an open Serial object or None.
    """
    print(f"[INFO] Attempting to open serial port: {port} at {baudrate} baud")
    cable_retries = 0
    connection_retries = 0
    last_exc = None

    while cable_retries < max_cable_retries:
        try:
            # Check if the serial device exists (Linux/macOS: /dev/ttyUSB0, Windows: COMx)
            if not os.path.exists(port) and not port.startswith("COM"):
                print(f"[WARNING] Serial device {port} not found. Check connection.")
                cable_retries += 1
                time.sleep(cable_retry_interval)  # Wait before retrying
                continue

            ser = serial.Serial(
                port=port,  # Console cable device (e.g., /dev/ttyUSB0)
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5,
            )
            print(f"[INFO] Serial port opened successfully (attempt {cable_retries}).")
            time.sleep(0.05)
            _reset_buffers(ser)

            while connection_retries < max_connection_retries:
                try:
                    output = wait_for_prompt(
                        ser, [">", "#"], timeout=timeout, wake=True
                    )
                    print(
                        f"[INFO] Connected. Device prompt: {output.strip().splitlines()[-1]}"
                    )
                    return ser
                except TimeoutError as e:
                    dbg(
                        f"Prompt not found on connection retry {connection_retries}: {e}"
                    )
                    connection_retries += 1
                    time.sleep(connection_retry_interval)
                    continue

            print(f"[ERROR] Failed to connect after {max_connection_retries} attempts.")
            logout_close_connection(ser)
            cable_retries += 1
            connection_retries = 0
            time.sleep(cable_retry_interval)

        except serial.SerialException as e:
            print(
                f"[ERROR] Error opening serial port on cable retry {cable_retries}: {e}"
            )
            last_exc = e
            cable_retries += 1
            time.sleep(cable_retry_interval)
            continue

    print(
        f"[ERROR] All {max_cable_retries} attempts to open serial port or detect prompt failed on {port}."
    )
    print("[HINT] Check if the device is plugged in and try again.")
    if last_exc and DEBUG:
        dbg(f"Last exception: {last_exc}")
    return None


def reconnect_serial(
    port, baudrate, timeout=READ_TIMEOUT, retry_interval=3, max_retries=10
):
    """
    Reconnect to the serial device if the connection is lost.
    """
    print("[WARNING] Serial connection lost. Attempting to reconnect...")
    return connect_to_serial(port, baudrate, timeout, retry_interval, max_retries)


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

            # Send break signal to force session termination
            # try:
            #     ser.send_break(duration=0.2)
            #     time.sleep(0.2)
            # except Exception as e:
            #     pass

            # Control hardware lines to signal disconnect
            # try:
            #     ser.dtr = False  # Data Terminal Ready
            #     ser.rts = False  # Request To Send
            #     time.sleep(0.2)
            #     ser.dtr = True
            #     ser.rts = True
            # except Exception as e:
            #     pass
            # _reset_buffers(ser)

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
