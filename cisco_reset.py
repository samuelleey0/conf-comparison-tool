import time
from serial_utils import connect_to_serial

def reload_cisco_device(port: str, baudrate: int = 9600):
    print(f"[INFO] Connecting to device on {port}...")
    ser = connect_to_serial(port, baudrate=baudrate)

    if not ser:
        print(f"[ERROR] Could not connect to device on {port}.")
        return

    try:
        # --- STEP 1: ENSURE EXEC MODE ---
        print("[INFO] Ensuring EXEC mode...")
        ser.write(b"end\n")
        ser.flush()
        time.sleep(1)
        ser.read(ser.in_waiting or 1024)

        # --- STEP 2: ENABLE ---
        print("[INFO] Entering enable mode...")
        ser.write(b"enable\n")
        ser.flush()
        time.sleep(1)
        ser.read(ser.in_waiting or 1024)

        # --- STEP 3: DELETE VLAN.DAT ---
        print("[INFO] Deleting vlan.dat...")
        ser.write(b"delete flash:vlan.dat\n")
        ser.flush()

        response = ""
        timeout = time.time() + 10

        while time.time() < timeout:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting).decode(errors="ignore")

                if "delete filename" in response.lower():
                    print("[INFO] Confirming filename...")
                    ser.write(b"\n")
                    ser.flush()

                elif "confirm" in response.lower():
                    print("[INFO] Confirming deletion...")
                    ser.write(b"\n")
                    ser.flush()
                    break

            time.sleep(0.1)

        print("[DEBUG] VLAN deleted")
        time.sleep(3)

        # --- STEP 4: RELOAD ONLY ---
        print("[INFO] Sending reload command...")
        ser.write(b"reload\n")
        ser.flush()

        response = ""
        timeout = time.time() + 15

        while time.time() < timeout:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting).decode(errors="ignore")

                # DO NOT SAVE CONFIG
                if "save" in response.lower():
                    print("[INFO] Responding 'no' (do not save)...")
                    ser.write(b"no\n")
                    ser.flush()
                    response = ""

                # CONFIRM RELOAD
                elif "confirm" in response.lower():
                    print("[INFO] Confirming reload...")
                    ser.write(b"\n")
                    ser.flush()
                    break

            time.sleep(0.1)

        print("[INFO] Device is rebooting...")

        time.sleep(5)

    except Exception as e:
        print(f"[ERROR] Failed: {e}")

    finally:
        try:
            ser.close()
        except:
            pass
        print("[INFO] Serial port closed safely.")