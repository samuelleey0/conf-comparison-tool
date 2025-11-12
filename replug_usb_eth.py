import sys
import os
import time
import subprocess


def get_mac(iface):
    try:
        with open(f"/sys/class/net/{iface}/address", "r") as f:
            return f.read().strip().lower()
    except Exception:
        return None


def _safe_read(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _wait_for_mac(mac, wait_timeout, wait_interval):
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        for candidate in os.listdir("/sys/class/net"):
            cand_mac = _safe_read(f"/sys/class/net/{candidate}/address")
            if cand_mac and cand_mac.lower() == mac:
                return candidate
        time.sleep(wait_interval)
    return None


def main(argv):
    if len(argv) < 2:
        print("Usage: replug_usb_eth.py <iface> [wait_seconds]")
        return 2
    iface = argv[1]
    wait_timeout = float(argv[2]) if len(argv) > 2 else 15.0
    wait_interval = 0.5

    if os.geteuid() != 0:
        print("[ERROR] This helper must run as root (sudo).")
        return 3

    if not os.path.exists(f"/sys/class/net/{iface}"):
        print(f"[ERROR] Interface {iface} not found.")
        return 4

    mac = get_mac(iface)
    if not mac:
        print(f"[ERROR] Unable to read MAC for {iface}.")
        return 5

    try:
        dev_path = os.path.realpath(f"/sys/class/net/{iface}/device")
    except Exception as e:
        print(f"[ERROR] Could not resolve sysfs device for {iface}: {e}")
        return 6

    driver_link = os.path.join(dev_path, "driver")
    if not os.path.islink(driver_link):
        print(f"[ERROR] No driver symlink found for {iface} at {driver_link}.")
        return 7

    driver_dir = os.path.realpath(driver_link)
    device_name = os.path.basename(dev_path)
    unbind_path = os.path.join(driver_dir, "unbind")
    bind_path = os.path.join(driver_dir, "bind")

    if not (os.path.exists(unbind_path) and os.path.exists(bind_path)):
        print(f"[ERROR] bind/unbind files not found under driver {driver_dir}.")
        return 8

    # Attempt unbind
    try:
        with open(unbind_path, "w") as f:
            f.write(device_name)
    except Exception as e:
        print(f"[ERROR] Failed to unbind device: {e}")
        return 9

    # brief pause to allow kernel to tear down
    time.sleep(1.0)

    # First attempt: try direct re-bind (may fail if sysfs entry removed)
    bind_failed = False
    try:
        with open(bind_path, "w") as f:
            f.write(device_name)
    except Exception as e:
        print(f"[WARN] Direct bind attempt failed: {e}")
        bind_failed = True

    # If direct bind worked, wait for MAC to reappear
    if not bind_failed:
        candidate = _wait_for_mac(mac, wait_timeout, wait_interval)
        if candidate:
            print(f"[INFO] Adapter with MAC {mac} reappeared as {candidate}.")
            return 0
        # if bind succeeded but MAC not seen, continue to other recovery steps

    # Recovery path if direct bind failed or no interface appeared:
    driver_name = os.path.basename(driver_dir)
    print(
        f"[INFO] Attempting recovery for driver '{driver_name}' (module reload + udev trigger)."
    )

    # Try ip link down/up on device path if available (gentle)
    try:
        # try bring link down/up by original iface name (best-effort)
        subprocess.run(
            ["ip", "link", "set", iface, "down"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)
        subprocess.run(
            ["ip", "link", "set", iface, "up"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    # Try module reload (remove then add), best-effort
    try:
        subprocess.run(
            ["modprobe", "-r", driver_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        subprocess.run(
            ["modprobe", driver_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[WARN] Module reload attempt encountered error: {e}")

    # Trigger udev to re-create devices
    try:
        subprocess.run(
            ["udevadm", "trigger"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["udevadm", "settle"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    # Wait for MAC to reappear after recovery steps
    candidate = _wait_for_mac(mac, wait_timeout, wait_interval)
    if candidate:
        print(f"[INFO] Adapter with MAC {mac} reappeared as {candidate}.")
        return 0

    # Final fallback: attempt direct bind again if sysfs entry reappeared
    if os.path.exists(bind_path):
        try:
            with open(bind_path, "w") as f:
                f.write(device_name)
            candidate = _wait_for_mac(mac, 5.0, wait_interval)
            if candidate:
                print(
                    f"[INFO] Adapter with MAC {mac} reappeared as {candidate} after fallback bind."
                )
                return 0
        except Exception as e:
            print(f"[WARN] Final fallback bind failed: {e}")

    print(f"[WARN] Timed out waiting for adapter with MAC {mac} to reappear.")
    return 11


if __name__ == "__main__":
    sys.exit(main(sys.argv))
