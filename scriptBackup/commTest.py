import serial
import time
import re

# Open serial connection (update port if needed, e.g. /dev/ttyUSB1)
ser = serial.Serial(
    port="/dev/ttyUSB0",  # Console cable device
    baudrate=9600,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=1,
)

# Give router some time after opening
time.sleep(2)


def send_command(cmd, prompt=b"#", timeout=10):
    """Send a command and return output"""
    ser.write(cmd.encode("utf-8") + b"\n")
    ser.flush()
    buffer = b""
    start_time = time.time()

    while time.time() - start_time < timeout:
        data = ser.read(1024)
        if not data:
            continue
        buffer += data
        if prompt in buffer:
            break

    output = buffer.decode("utf-8", errors="ignore")

    # Clean junk (--More--, ^ chars, etc.)
    output = re.sub(r"--More--", "", output)
    output = re.sub(r"\x1b\[.*?[@-~]", "", output)  # remove ANSI codes
    return output.strip()


# Wake up CLI
ser.write(b"\n")
time.sleep(3)

# Enter enable mode
print("Entering enable mode...")
out = send_command("enable", timeout=5)
print(out)

# Disable paging
print("Disabling paging...")
send_command("terminal length 0", timeout=3)

# Now run show running-config
print("Sending 'show running-config'...")
out = send_command("show running-config", timeout=15)
print("Router output:\n", out)

ser.close()
