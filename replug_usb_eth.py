#!/usr/bin/env python3
import sys, os, time


def get_mac(iface):
    try:
        with open(f"/sys/class/net/{iface}/address", "r") as f:
            return f.read().strip().lower()
    except Exception:
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

    try:
        with open(unbind_path, "w") as f:
            f.write(device_name)
    except Exception as e:
        print(f"[ERROR] Failed to unbind device: {e}")
        return 9

    time.sleep(1.0)

    try:
        with open(bind_path, "w") as f:
            f.write(device_name)
    except Exception as e:
        print(f"[ERROR] Failed to bind device: {e}")
        return 10

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        for candidate in os.listdir("/sys/class/net"):
            try:
                with open(f"/sys/class/net/{candidate}/address", "r") as f:
                    cand_mac = f.read().strip().lower()
            except Exception:
                cand_mac = None
            if cand_mac == mac:
                print(f"[INFO] Adapter with MAC {mac} reappeared as {candidate}.")
                return 0
        time.sleep(wait_interval)

    print(f"[WARN] Timed out waiting for adapter with MAC {mac} to reappear.")
    return 11


if __name__ == "__main__":
    sys.exit(main(sys.argv))
