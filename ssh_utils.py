import re
import time
from typing import Optional, Tuple

import paramiko


_PROMPT_REGEX = re.compile(r"(?:\S+#\s*$)|(?:\S+>\s*$)|(?:\(config\)#\s*$)", re.M)
_ANSI_REGEX = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def connect_ssh(
    host: str,
    username: str,
    password: str,
    port: int = 22,
    timeout: int = 10,
) -> Tuple[Optional[paramiko.SSHClient], Optional[paramiko.Channel]]:
    """
    Establish an SSH session and return (client, shell channel).
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"[SSH] Connecting to {host}:{port} as {username} ...")
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        shell = client.invoke_shell()
        shell.settimeout(timeout)
        return client, shell
    except Exception as exc:
        print(f"[SSH] Connection failed: {exc}")
        try:
            client.close()
        except Exception:
            pass
        return None, None


def _read_channel(shell: paramiko.Channel) -> str:
    """
    Drain any ready data from the channel.
    """
    output = ""
    while shell.recv_ready():
        data = shell.recv(65535)
        if not data:
            break
        output += data.decode("utf-8", errors="ignore")
    return output


def _normalize_buffer(buffer: str) -> str:
    return _ANSI_REGEX.sub("", buffer)


def send_command_ssh(
    shell: paramiko.Channel,
    command: str,
    expected_prompt: Optional[str] = "#",
    timeout: int = 10,
) -> str:
    """
    Send a command over an interactive SSH shell and return the collected output.
    Stops when the expected prompt (or a generic privileged/user prompt) is seen.
    """
    if shell is None:
        raise ValueError("SSH shell is None")

    to_send = command if command.endswith("\n") else command + "\n"
    shell.send(to_send)

    buffer = ""
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(0.2)
        if shell.recv_ready():
            buffer += _read_channel(shell)
            clean = _normalize_buffer(buffer)
            if expected_prompt and expected_prompt in clean:
                return clean.strip()
            if _PROMPT_REGEX.search(clean):
                return clean.strip()
    return _normalize_buffer(buffer).strip()


def enter_enable_mode_ssh(
    shell: paramiko.Channel,
    enable_password: Optional[str] = None,
    prompt: str = "#",
    timeout: int = 10,
) -> str:
    """
    Attempt to enter privileged EXEC mode by sending `enable`.
    Sends enable_password if the device prompts for it.
    """
    if shell is None:
        raise ValueError("SSH shell is None")

    shell.send("enable\n")
    buffer = ""
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(0.2)
        if not shell.recv_ready():
            continue
        buffer += _read_channel(shell)
        clean = _normalize_buffer(buffer)

        if "Password:" in clean or "password:" in clean:
            if enable_password is None:
                raise Exception("Enable password requested but none provided.")
            shell.send(enable_password + "\n")
            buffer = ""
            continue

        if prompt in clean or _PROMPT_REGEX.search(clean):
            return clean.strip()
    raise TimeoutError("Timed out entering enable mode over SSH.")


def disable_paging_ssh(shell: paramiko.Channel, prompt: str = "#", timeout: int = 5):
    return send_command_ssh(shell, "terminal length 0", expected_prompt=prompt, timeout=timeout)


def get_hostname_ssh(shell: paramiko.Channel, timeout: int = 10) -> str:
    output = send_command_ssh(
        shell, "show running-config | include hostname", timeout=timeout
    )
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("hostname"):
            parts = stripped.split()
            if len(parts) >= 2:
                return parts[1]
    return "CiscoDevice"
