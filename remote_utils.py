import telnetlib
import time
import re
import logging

logger = logging.getLogger("remote_utils")


def remote_connect(host, username="", password="", port=22, timeout=10):
    """
    Establish a Telnet connection and return (client_like, shell_like).
    Keeps the original function name to avoid changing callers.
    """
    try:
        print(f"Connecting to {host}:{port} via Telnet...")
        tn = telnetlib.Telnet(host, port, timeout)
    except Exception as e:
        print(f"[!] Telnet connection failed: {e}")
        return None, None

    try:
        # read initial banner / login prompt
        time.sleep(0.4)
        banner = tn.read_very_eager().decode("utf-8", errors="ignore")

        # common login prompt patterns
        if re.search(r"(?:username|login)[:\s]", banner, re.IGNORECASE) and username:
            tn.write(username.encode("ascii") + b"\n")
            time.sleep(0.2)
            # then expect password
            tn.read_until(b"Password:", timeout=2)
            tn.write(password.encode("ascii") + b"\n")
        elif "Password:" in banner and password:
            # some devices present only password prompt
            tn.write(password.encode("ascii") + b"\n")
        else:
            # no auth prompts detected; proceed
            pass

        # wait until we get a device prompt (>, # or (config)#)
        start = time.time()
        buf = ""
        while time.time() - start < timeout:
            time.sleep(0.2)
            try:
                data = tn.read_very_eager()
            except Exception:
                data = b""
            if data:
                buf += data.decode("utf-8", errors="ignore")
                if re.search(
                    r"(?:\S+#\s*$)|(?:\S+>\s*$)|(?:\(config\)#\s*$)", buf, re.M
                ):
                    break

        print(f"[INFO] Connected via Telnet. Device prompt detected.")
        # return both values so existing callers that expect (client, shell) work
        return tn, tn
    except Exception as e:
        print(f"[!] Telnet post-login handling failed: {e}")
        try:
            tn.close()
        except Exception:
            pass
        return None, None


def send_command_remote(shell, command, expected_prompt="#", timeout=10):
    """
    Send command over a telnetlib.Telnet instance and return output.
    Handles (config)# by sending 'end' and continuing to wait for prompt.
    """
    if not shell:
        raise ValueError("Shell (telnet) is None")

    if not command.endswith("\n"):
        to_send = command + "\n"
    else:
        to_send = command

    try:
        shell.write(to_send.encode("ascii"))
    except Exception:
        # some telnetlib implementations expect bytes; tolerate errors
        shell.write(to_send.encode("utf-8", errors="ignore"))

    buffer = ""
    start_time = time.time()

    while time.time() - start_time < timeout:
        time.sleep(0.2)
        try:
            data = shell.read_very_eager()
        except Exception:
            data = b""
        if data:
            chunk = data.decode("utf-8", errors="ignore")
            buffer += chunk

            # if device is in config mode, send 'end' and continue
            if "(config)#" in buffer:
                logger.info(
                    "Detected (config)# prompt -- sending 'end' to exit config mode"
                )
                try:
                    shell.write(b"end\n")
                except Exception:
                    pass
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

    # timeout: return whatever we have (strip) to avoid losing partial output
    return buffer.strip()


def get_hostname_remote(shell, timeout=10):
    """
    Extract hostname dynamically from device via Telnet.
    """
    output = send_command_remote(
        shell, "show running-config | include hostname", timeout=timeout
    )

    for line in output.splitlines():
        if line.strip().startswith("hostname"):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    return "CiscoDevice"


def disable_paging_remote(shell, prompt="#", timeout=5):
    return send_command_remote(
        shell, "terminal length 0", expected_prompt=prompt, timeout=timeout
    )


def enter_enable_mode_remote(shell, prompt="#", timeout=5):
    """
    Attempt to enter enable (privileged EXEC) mode. If device prompts for a password,
    this function does not supply one (keeps parity with original behaviour).
    """
    return send_command_remote(shell, "enable", expected_prompt=prompt, timeout=timeout)
