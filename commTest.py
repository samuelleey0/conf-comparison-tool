import serial
import time

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
time.sleep(8)


def send_command(cmd, wait=10):
    """Send a command and return output"""
    ser.write(cmd.encode("utf-8") + b"\n")
    time.sleep(wait)
    output = ser.read(5000).decode("utf-8", errors="ignore")
    return output


# Wake up CLI
ser.write(b"\n")
time.sleep(5)

# Enter enable mode
print("Entering enable mode...")
out = send_command("enable")
print(out)


# Now run show running-config
print("Sending 'show running-config'...")
out = send_command("show running-config", wait=5)
print("Router output:\n", out)

ser.close()
