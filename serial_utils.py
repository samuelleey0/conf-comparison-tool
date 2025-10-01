import serial
import time
import re
import threading

from netmiko.netmiko_globals import MAX_BUFFER

READ_TIMEOUT = 8  # seconds
MAX_BUFFER = 4096  # 4KB

def send_keepalive(ser, stop_event, interval=30):
    """
    Send periodic keepalive signals to prevent device sleep/timeout.
    Runs in a separate thread.
    """
    while not stop_event.is_set():
        try:
            if ser.is_open:
                # Send a harmless command that doesn't affect device state
                ser.write(b"\r")  # Just a carriage return
                ser.flush()
                print("[DEBUG] Keepalive signal sent")
            stop_event.wait(interval)  # Wait for interval or stop signal
        except Exception as e:
            print(f"[DEBUG] Keepalive error: {e}")
            break


def connect_to_serial_with_keepalive(port: str, baudrate: int = 9600, timeout=READ_TIMEOUT):
    """
    Establish a serial connection to Cisco device with keepalive function.
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
            # Prevent OS-level sleep
            write_timeout=None,
            inter_byte_timeout=None,
        )

        # Set hardware flow control to prevent sleep
        ser.dtr = True
        ser.rts = True
        print("[DEBUG] Serial port opened successfully.")

        # Clear any existing session first
        clear_session(ser)

        # Wake device before waiting for prompt
        wake_up_device(ser)

        output = wait_for_prompt(ser, [">", "#"], timeout=timeout, wake=True)
        print(f"[+] Connected. Device prompt: {output.strip().splitlines()[-1]}")
        return ser
    except serial.SerialException as e:
        print(f"[!] Error communicating...: {e}")
        return None

def wake_up_device(ser, attempts=5):
    """
    Aggressively wake up the device from sleep mode.
    """
    print("[DEBUG] Waking up device...")

    for attempt in range(attempts):
        try:
            # Clear buffers
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Send multiple wake signals
            wake_signals = [
                b"\r\n",  # Carriage return + line feed
                b" ",  # Space character
                b"\x03",  # Ctrl+C
                b"\r",  # Just carriage return
            ]

            for signal in wake_signals:
                ser.write(signal)
                ser.flush()
                time.sleep(0.5)

                # Check if device responded
                if ser.in_waiting > 0:
                    response = ser.read(ser.in_waiting)
                    print(f"[DEBUG] Device responded: {response.decode(errors='ignore')}")
                    return True

            print(f"[DEBUG] Wake attempt {attempt + 1}/{attempts}")
            time.sleep(1)

        except Exception as e:
            print(f"[DEBUG] Wake attempt {attempt + 1} failed: {e}")

    print("[DEBUG] Device wake-up completed")
    return False


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


def send_command_with_keepalive(ser, command, expected_prompt="#", timeout=20, keepalive_interval=10):
    """
    Send command with built-in keepalive for long-running commands.
    """
    print(f"[DEBUG] Sending command: {command}")

    # Start keepalive thread for long commands
    stop_keepalive = threading.Event()
    keepalive_thread = None

    if timeout > keepalive_interval:
        keepalive_thread = threading.Thread(
            target=send_keepalive,
            args=(ser, stop_keepalive, keepalive_interval)
        )
        keepalive_thread.daemon = True
        keepalive_thread.start()

    try:
        # Send the command
        if isinstance(command, str):
            ser.write(command.encode("utf-8") + b"\r\n")
        else:
            ser.write(command + b"\r\n")
        ser.flush()

        buffer = b""
        start_time = time.time()
        ser.timeout = 0.5  # Faster polling for responsiveness

        while time.time() - start_time < timeout:
            data = ser.read(1024)
            if data:
                buffer += data
                print(f"[DEBUG] Received {len(data)} bytes")

                # Check for prompt
                decoded = buffer.decode("utf-8", errors="ignore")
                if re.search(rf"{re.escape(expected_prompt)}\s*$", decoded, re.MULTILINE):
                    break

                # Handle --More-- prompts
                if "--More--" in decoded:
                    ser.write(b" ")  # Send space to continue
                    ser.flush()
            else:
                # Send periodic keepalive during long waits
                time.sleep(0.1)

        # Stop keepalive
        if keepalive_thread:
            stop_keepalive.set()
            keepalive_thread.join(timeout=1)

        output = buffer.decode("utf-8", errors="ignore")
        # Clean output
        output = re.sub(r"--More--", "", output)
        output = re.sub(r"\x1b\[.*?[@-~]", "", output)  # Remove ANSI codes
        output = re.sub(r"\r\n", "\n", output)  # Normalize line endings

        return output.strip()

    except Exception as e:
        if keepalive_thread:
            stop_keepalive.set()
        raise e

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
