import time
from serial_utils import connect_to_serial


def _read_until(ser, triggers, timeout=15, log_cb=None):
    """
    Read serial output until one of the trigger phrases is found (case-insensitive)
    or until timeout.  Returns (accumulated_text, matched_trigger_or_None).
    """
    response = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            chunk = ser.read(n).decode(errors="ignore")
            response += chunk
            if log_cb:
                log_cb(f"[SERIAL] {chunk.strip()}")
            lower = response.lower()
            for trigger in triggers:
                if trigger.lower() in lower:
                    return response, trigger
        time.sleep(0.1)
    return response, None


def reload_cisco_device(
    port: str,
    baudrate: int = 9600,
    status_cb=None,
    erase_startup_config: bool = False,
    delete_vlan_database: bool = True,
):
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
        return {
            "success": False,
            "logs": logs,
            "message": f"Could not connect to device on {port}.",
        }

    try:
        # --- STEP 1: ENSURE EXEC MODE ---
        emit("[INFO] Ensuring EXEC mode...")
        ser.write(b"\x03")  # Ctrl-C to interrupt anything
        ser.flush()
        time.sleep(0.3)

        # Detect current prompt and exit config modes if needed
        max_end_attempts = 5
        for attempt in range(max_end_attempts):
            ser.write(b"end\n")
            ser.flush()
            time.sleep(0.5)

            # Read any response to check prompt
            resp, trigger = _read_until(ser, [">", "#"], timeout=3)
            emit(f"[DEBUG] Prompt check attempt {attempt + 1}: {repr(resp)}")

            # Simple check: if response contains > or #, we're good
            if ">" in resp or "#" in resp:
                emit("[INFO] Reached user/enable prompt.")
                break
            elif attempt < max_end_attempts - 1:
                emit(f"[INFO] Still in config mode, sending end again...")
        else:
            emit(
                f"[WARNING] Could not exit config mode after {max_end_attempts} attempts, proceeding anyway."
            )

        ser.read(ser.in_waiting or 1024)  # drain buffer
        time.sleep(0.5)

        # --- STEP 2: ENABLE ---
        emit("[INFO] Entering enable mode...")
        ser.write(b"enable\n")
        ser.flush()
        time.sleep(1)
        ser.read(ser.in_waiting or 1024)  # drain buffer

        # --- STEP 3: WRITE ERASE (erase startup-config) --- [OPTIONAL]
        if erase_startup_config:
            emit("[INFO] Erasing startup configuration...")
            ser.write(b"write erase\n")
            ser.flush()

            resp, trigger = _read_until(ser, ["confirm", "[ok]"], timeout=10)
            if trigger:
                emit("[INFO] Confirming write erase...")
                ser.write(b"\n")
                ser.flush()
                time.sleep(3)
                ser.read(ser.in_waiting or 1024)  # drain
            else:
                emit(
                    "[WARNING] No confirmation prompt for write erase. Continuing anyway."
                )
        else:
            emit("[INFO] Skipping startup config erasure (preserving startup-config).")

        # Ensure buffer is clean before vlan operation
        ser.read(ser.in_waiting or 1024)  # drain any residual data
        time.sleep(0.5)

        # --- STEP 4: DELETE VLAN.DAT --- [SWITCH ONLY]
        if delete_vlan_database:
            emit("[INFO] Deleting vlan.dat...")
            ser.write(b"delete flash:vlan.dat\n")
            ser.flush()

            # First prompt: "Delete filename [vlan.dat]?"
            resp, trigger = _read_until(
                ser,
                ["delete filename", "confirm", "[ok]", "no such file", "not found"],
                timeout=10,
            )
            emit(f"[DEBUG] Device response: {repr(resp)}")
            emit(f"[DEBUG] Matched trigger: {trigger}")

            if trigger and (
                "no such file" in trigger.lower() or "not found" in trigger.lower()
            ):
                emit("[INFO] vlan.dat does not exist. Skipping.")
            elif trigger:
                if "delete filename" in trigger.lower():
                    emit("[INFO] Confirming filename...")
                    ser.write(b"\n")
                    ser.flush()
                    # Wait for second prompt: "Delete flash:/vlan.dat? [confirm]"
                    resp2, trigger2 = _read_until(ser, ["confirm", "[ok]"], timeout=10)
                    emit(f"[DEBUG] Second response: {repr(resp2)}")
                    emit(f"[DEBUG] Second trigger: {trigger2}")

                    if trigger2:
                        emit("[INFO] Confirming deletion...")
                        ser.write(b"\n")
                        ser.flush()
                        time.sleep(2)
                elif "confirm" in trigger.lower():
                    emit("[INFO] Confirming deletion...")
                    ser.write(b"\n")
                    ser.flush()
                    time.sleep(2)
            else:
                if not resp.strip():
                    emit(
                        "[INFO] No immediate response for vlan.dat delete; probing device prompt..."
                    )
                    ser.write(b"\n")
                    ser.flush()
                    resp_probe, trigger_probe = _read_until(
                        ser,
                        [
                            "delete filename",
                            "confirm",
                            "[ok]",
                            "no such file",
                            "not found",
                            "#",
                            ">",
                        ],
                        timeout=3,
                    )
                    emit(f"[DEBUG] Probe response: {repr(resp_probe)}")
                    emit(f"[DEBUG] Probe trigger: {trigger_probe}")

                    if trigger_probe and trigger_probe.lower() in (
                        "delete filename",
                        "confirm",
                    ):
                        if trigger_probe.lower() == "delete filename":
                            emit("[INFO] Confirming filename...")
                            ser.write(b"\n")
                            ser.flush()
                            resp2, trigger2 = _read_until(
                                ser, ["confirm", "[ok]"], timeout=10
                            )
                            emit(f"[DEBUG] Second response: {repr(resp2)}")
                            emit(f"[DEBUG] Second trigger: {trigger2}")
                            if trigger2:
                                emit("[INFO] Confirming deletion...")
                                ser.write(b"\n")
                                ser.flush()
                                time.sleep(2)
                        else:
                            emit("[INFO] Confirming deletion...")
                            ser.write(b"\n")
                            ser.flush()
                            time.sleep(2)
                    else:
                        emit(
                            "[INFO] No interactive delete prompt detected; continuing to reload."
                        )
                else:
                    emit(
                        f"[WARNING] No prompt detected for vlan.dat deletion. Device response was: {repr(resp)}"
                    )
                    emit("[WARNING] Continuing anyway...")

            ser.read(ser.in_waiting or 1024)  # drain buffer
            time.sleep(1)
        else:
            emit("[INFO] Skipping vlan.dat deletion for router reset.")

        # --- STEP 5: RELOAD ---
        emit("[INFO] Sending reload command...")
        ser.write(b"reload\n")
        ser.flush()

        if delete_vlan_database:
            # Preserve switch behavior: standard save/confirm handling.
            resp, trigger = _read_until(
                ser, ["save?", "yes/no", "modified", "confirm", "proceed"], timeout=15
            )

            if trigger and trigger.lower() in ("save?", "yes/no", "modified"):
                emit("[INFO] Responding 'no' to save prompt...")
                ser.write(b"no\n")
                ser.flush()
                resp2, trigger2 = _read_until(ser, ["confirm", "proceed"], timeout=10)
                if trigger2:
                    emit("[INFO] Confirming reload...")
                    ser.write(b"\n")
                    ser.flush()
                else:
                    emit(
                        "[INFO] No confirm prompt after 'no'. Device may be reloading."
                    )
            elif trigger and trigger.lower() in ("confirm", "proceed"):
                emit("[INFO] Confirming reload...")
                ser.write(b"\n")
                ser.flush()
            else:
                emit(
                    "[WARNING] No reload prompt detected. Sending Enter just in case..."
                )
                ser.write(b"\n")
                ser.flush()
        else:
            # Routers can prompt multiple times during reload, e.g.:
            #   1) Save? [yes/no]:
            #   2) Do you wish to proceed with reload anyway[confirm]
            #   3) Proceed with reload? [confirm]
            #   4) "Keep it blank" style prompt requiring Enter
            reload_triggers = [
                "save?",
                "yes/no",
                "modified",
                "do you wish to proceed with reload anyway",
                "proceed with reload",
                "proceed",
                "[confirm]",
                "confirm",
                "boot variable",
                "keep it blank",
            ]

            save_answered = False
            enter_presses = 0
            max_enter_presses = 4

            for _ in range(8):
                resp, trigger = _read_until(ser, reload_triggers, timeout=8)
                if not trigger:
                    break

                trigger_lower = trigger.lower()
                emit(f"[DEBUG] Reload prompt matched: {trigger_lower}")

                if not save_answered and trigger_lower in (
                    "save?",
                    "yes/no",
                    "modified",
                ):
                    emit("[INFO] Responding 'no' to save prompt...")
                    ser.write(b"no\n")
                    ser.flush()
                    save_answered = True
                    continue

                if trigger_lower in (
                    "do you wish to proceed with reload anyway",
                    "proceed with reload",
                    "proceed",
                    "[confirm]",
                    "confirm",
                    "boot variable",
                    "keep it blank",
                ):
                    emit("[INFO] Sending Enter to continue reload sequence...")
                    ser.write(b"\n")
                    ser.flush()
                    enter_presses += 1
                    if enter_presses >= max_enter_presses:
                        break

            if enter_presses == 0 and not save_answered:
                emit(
                    "[WARNING] No reload prompt detected. Sending Enter just in case..."
                )
                ser.write(b"\n")
                ser.flush()

        emit("[INFO] Device is rebooting. Waiting for reload to begin...")
        time.sleep(10)  # Give the device time to start the reload

        return {
            "success": True,
            "logs": logs,
            "message": "Device reset and reload initiated successfully.",
        }

    except Exception as e:
        emit(f"[ERROR] Failed: {e}")
        return {"success": False, "logs": logs, "message": str(e)}

    finally:
        try:
            ser.close()
        except Exception:
            pass
        emit("[INFO] Serial port closed safely.")
