import time
from serial_utils import connect_to_serial


def reload_cisco_device(port: str, baudrate: int = 9600, status_cb=None):
    logs = []

    def emit(message):
        print(message, flush=True)
        logs.append(message)
        if status_cb:
            try:
                status_cb(message)
            except Exception:
                pass

    emit(f"[INFO] Connecting to device on {port}...")
    ser = connect_to_serial(port, baudrate=baudrate, status_cb=status_cb)

    if not ser:
        emit(f"[ERROR] Could not connect to device on {port}.")
        return {"success": False, "logs": logs, "message": f"Could not connect to device on {port}."}

    try:
        # --- STEP 1: ENSURE EXEC MODE ---
        emit("[INFO] Ensuring EXEC mode...")
        ser.write(b"end\n")
        ser.flush()
        time.sleep(1)
        ser.read(ser.in_waiting or 1024)

        # --- STEP 2: ENABLE ---
        emit("[INFO] Entering enable mode...")
        ser.write(b"enable\n")
        ser.flush()
        time.sleep(1)
        ser.read(ser.in_waiting or 1024)

        # --- STEP 3: DELETE VLAN.DAT ---
        emit("[INFO] Deleting vlan.dat...")
        ser.write(b"delete flash:vlan.dat\n")
        ser.flush()

        response = ""
        timeout = time.time() + 10

        while time.time() < timeout:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting).decode(errors="ignore")

                if "delete filename" in response.lower():
                    emit("[INFO] Confirming filename...")
                    ser.write(b"\n")
                    ser.flush()

                elif "confirm" in response.lower():
                    emit("[INFO] Confirming deletion...")
                    ser.write(b"\n")
                    ser.flush()
                    break

            time.sleep(0.1)

        emit("[DEBUG] VLAN deleted")
        time.sleep(3)

        # --- STEP 4: RELOAD ONLY ---
        emit("[INFO] Sending reload command...")
        ser.write(b"reload\n")
        ser.flush()

        response = ""
        timeout = time.time() + 15

        while time.time() < timeout:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting).decode(errors="ignore")

                # DO NOT SAVE CONFIG
                if "save" in response.lower():
                    emit("[INFO] Responding 'no' (do not save)...")
                    ser.write(b"no\n")
                    ser.flush()
                    response = ""

                # CONFIRM RELOAD
                elif "confirm" in response.lower():
                    emit("[INFO] Confirming reload...")
                    ser.write(b"\n")
                    ser.flush()
                    break

            time.sleep(0.1)

        emit("[INFO] Device is rebooting...")

        time.sleep(5)
        return {"success": True, "logs": logs, "message": "Reset command sent successfully."}

    except Exception as e:
        emit(f"[ERROR] Failed: {e}")
        return {"success": False, "logs": logs, "message": str(e)}

    finally:
        try:
            ser.close()
        except:
            pass
        emit("[INFO] Serial port closed safely.")
