import time
import re
import logging
import paramiko
import socket
import os
import subprocess

logger = logging.getLogger("remote_utils")


def clear_arp_entry(
    ip: str, interface: str | None = None, allow_flush_all: bool = False
) -> bool:
    """
    Try to remove a single ARP/neighbor entry for `ip`.
    Attempts, in order:
        1) ip neigh del <ip> dev <iface> (if iface known)
        2) detect iface via `ip neigh show <ip>` and delete
        3) ip neigh del <ip> (some kernels accept this)
        4) arp -d <ip> (if arp command available)
        5) ip neigh flush to <ip>
    If all fail and allow_flush_all is True, will attempt `ip neigh flush all` as a last resort.
    Returns True on success, False otherwise.
    """

    def run(cmd, use_sudo=False):
        full = (["sudo"] + cmd) if use_sudo else cmd
        try:
            res = subprocess.run(full, capture_output=True, text=True)
            return res.returncode == 0, res
        except FileNotFoundError as e:
            return False, e
        except Exception as e:
            return False, e

    # 1) If iface supplied, try targeted delete
    if interface:
        ok, res = run(["ip", "neigh", "del", ip, "dev", interface])
        if ok:
            print(f"[INFO] Cleared ARP entry {ip} on {interface}")
            return True

    # 2) Try to detect interface from `ip neigh show <ip>`
    ok, res = run(["ip", "neigh", "show", ip])
    if ok and isinstance(res, subprocess.CompletedProcess):
        out = (res.stdout or "").strip()
        m = re.search(r"\bdev\s+(\S+)\b", out)
        if m:
            iface = m.group(1)
            ok2, res2 = run(["ip", "neigh", "del", ip, "dev", iface])
            if ok2:
                print(f"[INFO] Cleared ARP entry {ip} on {iface}")
                return True

    # 3) Try a generic ip neigh del <ip>
    ok, res = run(["ip", "neigh", "del", ip])
    if ok:
        print(f"[INFO] Cleared ARP entry {ip} (no iface)")
        return True

    # 4) Try arp -d <ip> (net-tools)
    ok, res = run(["arp", "-d", ip])
    if ok:
        print(f"[INFO] Cleared ARP entry {ip} using arp -d")
        return True

    # 5) Try ip neigh flush to <ip>
    ok, res = run(["ip", "neigh", "flush", "to", ip])
    if ok:
        print(f"[INFO] Flushed neighbor entry for {ip} using ip neigh flush to")
        return True

    # 6) Try sudo fallbacks for the above (permission issues)
    for cmd in (
        ["ip", "neigh", "del", ip],
        ["arp", "-d", ip],
        ["ip", "neigh", "flush", "to", ip],
    ):
        ok, res = run(cmd, use_sudo=True)
        if ok:
            print(f"[INFO] Cleared ARP entry {ip} (via sudo): {' '.join(cmd)}")
            return True

    # 7) Last resort: optional flush all (dangerous) only if explicitly allowed
    if allow_flush_all:
        ok, res = run(["ip", "neigh", "flush", "all"], use_sudo=True)
        if ok:
            print("[WARN] Flushed all ARP/neighbor entries (allow_flush_all=True).")
            return True

    # report failure
    stderr = ""
    if isinstance(res, subprocess.CompletedProcess):
        stderr = (res.stderr or "").strip()
    print(f"[WARN] Unable to clear ARP entry for {ip}. Last result: {stderr}")
    return False


def remote_connect(host, username="", password="", port="22", timeout=10):
    """
    Establish SSH connection using Paramiko.
    Automatically accepts unknown host keys.
    Handles conflicting host keys and Diffie-Hellman key exchange mismatches.
    Returns an active Paramiko SSHClient instance with an invoked shell channel.
    """
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    known_hosts_path = os.path.expanduser("~/.ssh/known_hosts")

    def remove_host_key(hostname):
        if not os.path.exists(known_hosts_path):
            return
        try:
            with open(known_hosts_path, "r") as f:
                lines = f.readlines()
            with open(known_hosts_path, "w") as f:
                for line in lines:
                    if not line.startswith(hostname):
                        f.write(line)
            logger.info(
                f"Removed conflicting host key entry for {hostname} from known_hosts."
            )
        except Exception as e:
            logger.warning(f"Failed to remove host key for {hostname}: {e}")

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
    except paramiko.ssh_exception.SSHException as exc:

        # Handle Diffie-Hellman group exchange mismatch by appending compatible algorithms
        if "kex alg" in str(exc).lower() or "diffie-hellman" in str(exc).lower():
            logger.info(
                "Detected DH key exchange issue, adjusting algorithms and retrying."
            )
            try:
                transport = paramiko.Transport((host, 22))
                # Append compatible kex algorithms
                compatible_kex = [
                    "diffie-hellman-group1-sha1",
                    "diffie-hellman-group14-sha1",
                    "diffie-hellman-group-exchange-sha1",
                    "diffie-hellman-group-exchange-sha256",
                ]
                transport.get_security_options().key_exchanges += compatible_kex
                transport.start_client(timeout=timeout)
                transport.auth_password(username=username, password=password)
                client._transport = transport
                # Open shell once and store it on client
                try:
                    client._shell = client.invoke_shell()
                    logger.info(
                        "Shell channel opened and stored on client (DH fallback)."
                    )
                except Exception as e:
                    logger.warning(f"Failed to invoke shell after DH fallback: {e}")
                    client._shell = None
                return client
            except Exception as e2:
                logger.error(f"Failed to connect after adjusting kex algorithms: {e2}")
                return None
        else:
            logger.error(f"SSHException during connect: {exc}")
            return None
    except paramiko.ssh_exception.NoValidConnectionsError as exc:
        logger.error(f"Unable to connect to {host}: {exc}")
        return None
    except paramiko.ssh_exception.BadHostKeyException as exc:
        # Remove conflicting host key and retry once
        logger.warning(f"Bad host key for {host}, removing and retrying: {exc}")
        remove_host_key(host)
        try:
            client.connect(
                hostname=host,
                username=username,
                password=password,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except Exception as e:
            logger.error(f"Failed to connect after removing bad host key: {e}")
            return None
    except Exception as e:
        logger.error(f"Unexpected error during SSH connect: {e}")
        return None

    # Open shell once and store it on client
    try:
        client._shell = client.invoke_shell()
        logger.info("Shell channel opened and stored on client.")
    except Exception as e:
        logger.warning(f"Failed to invoke shell: {e}")
        client._shell = None

    return client


def send_command_remote(client, command, expected_prompt="#", timeout=10):
    """
    Send command over an active SSHClient instance's shell channel and return output.
    Handles (config)# by sending 'end' and continuing to wait for prompt.
    Reuses the shell channel opened at connection time.
    """
    if not client:
        raise ValueError("SSH client is None")

    shell = getattr(client, "_shell", None)
    if shell is None:
        logger.info("No existing shell channel found, invoking new shell.")
        try:
            shell = client.invoke_shell()
            client._shell = shell
        except Exception as e:
            logger.error(f"Failed to invoke shell: {e}")
            return ""
    else:
        logger.info("Reusing existing shell channel for command.")

    shell.settimeout(timeout)
    buffer = ""
    try:
        # Clear any initial data
        time.sleep(0.5)
        while shell.recv_ready():
            shell.recv(4096)

        shell.send(command if command.endswith("\n") else command + "\n")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if shell.recv_ready():
                recv = shell.recv(4096).decode("utf-8", errors="ignore")
                buffer += recv

                # if device is in config mode, send 'end' and continue
                if "(config)#" in buffer:
                    logger.info(
                        "Detected (config)# prompt -- sending 'end' to exit config mode"
                    )
                    shell.send("end\n")
                    time.sleep(0.2)
                    buffer = ""
                    continue

                # clean ANSI escapes for reliable prompt detection
                clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buffer)

                # check for expected prompt or common privileged prompt '#'
                if expected_prompt and expected_prompt in clean:
                    return clean.strip()
                if "#" in clean or ">" in clean:
                    return clean.strip()
            else:
                time.sleep(0.2)
    except socket.timeout:
        pass
    except Exception as e:
        logger.error(f"Error during send_command_remote: {e}")

    # timeout: return whatever we have (strip) to avoid losing partial output
    return buffer.strip()


def get_hostname_remote(client, timeout=10):
    """
    Extract hostname dynamically from device via SSH.
    """
    output = send_command_remote(
        client, "show running-config | include hostname", timeout=timeout
    )

    for line in output.splitlines():
        if line.strip().startswith("hostname"):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    return "CiscoDevice"


def disable_paging_remote(client, prompt="#", timeout=5):
    return send_command_remote(
        client, "terminal length 0", expected_prompt=prompt, timeout=timeout
    )


def enter_enable_mode_remote(client, prompt="#", timeout=5):
    if not client:
        raise ValueError("SSH client is None")

    shell = getattr(client, "_shell", None)
    if shell is None:
        shell = client.invoke_shell()
        client._shell = shell

    shell.settimeout(timeout)
    time.sleep(0.5)
    initial_output = ""
    while shell.recv_ready():
        initial_output += shell.recv(4096).decode(errors="ignore")

    # Check current mode
    if "#" in initial_output:
        logger.info("Already in privileged EXEC mode, skipping 'enable'.")
        return initial_output.strip()

    # Otherwise send enable
    logger.info("Not in privileged mode, sending 'enable'.")
    shell.send("enable\n")

    buffer = ""
    start = time.time()
    while time.time() - start < timeout:
        if shell.recv_ready():
            buffer += shell.recv(4096).decode(errors="ignore")
            if "#" in buffer:
                return buffer.strip()
        time.sleep(0.2)
    raise TimeoutError("Timed out waiting for privileged prompt after 'enable'.")
