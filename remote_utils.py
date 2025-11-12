import time
import re
import logging
import paramiko
import socket
import os

logger = logging.getLogger("remote_utils")


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

    return client


def send_command_remote(client, command, expected_prompt="#", timeout=10):
    """
    Send command over an active SSHClient instance's shell channel and return output.
    Handles (config)# by sending 'end' and continuing to wait for prompt.
    """
    if not client:
        raise ValueError("SSH client is None")

    try:
        shell = client.invoke_shell()
    except Exception as e:
        logger.error(f"Failed to invoke shell: {e}")
        return ""

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
    """
    Attempt to enter enable (privileged EXEC) mode.
    Returns the prompt output when in privileged mode or raises on timeout.
    """
    if not client:
        raise ValueError("SSH client is None")

    try:
        shell = client.invoke_shell()
    except Exception as e:
        logger.error(f"Failed to invoke shell: {e}")
        raise

    shell.settimeout(timeout)
    buf = ""
    start = time.time()

    try:
        shell.send("enable\n")
    except Exception:
        try:
            shell.send("enable\r\n")
        except Exception:
            raise

    while time.time() - start < timeout:
        try:
            if shell.recv_ready():
                data = shell.recv(4096)
            else:
                time.sleep(0.2)
                continue
        except socket.timeout:
            continue
        except Exception:
            data = b""

        if not data:
            continue

        chunk = data.decode("utf-8", errors="ignore")
        buf += chunk

        # strip ANSI for reliable detection
        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buf)

        # check for privileged prompt patterns
        if re.search(r"(?:\S+#\s*$)|(?:\(config\)#\s*$)", clean, re.M):
            return clean.strip()

    raise TimeoutError("Timed out waiting for privileged prompt after 'enable'.")
