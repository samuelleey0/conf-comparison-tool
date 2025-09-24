import paramiko
import time


def connect_ssh(host, username, password, port=22, timeout=10):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"Connecting to {host}:{port} as {username}...")
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False
        )
    except Exception as e:
        print(f"[!] SSH connection failed: {e}")
        return None, None
    shell = client.invoke_shell()
    return client, shell


def send_command_ssh(shell, command, expected_prompt="#", timeout=10):
    shell.send(command + "\n")
    buffer = ""
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(0.5)
        if shell.recv_ready():
            data = shell.recv(10000).decode("utf-8", errors="ignore")
            buffer += data
            if expected_prompt in buffer:
                break
        else:
            time.sleep(0.1)
    return buffer.strip()


def disable_paging_ssh(shell, prompt="#", timeout=5):
    return send_command_ssh(
        shell, "terminal length 0", expected_prompt=prompt, timeout=timeout
    )


def enter_enable_mode_ssh(shell, prompt="#", timeout=5):
    output = send_command_ssh(shell, "enable", expected_prompt=prompt, timeout=timeout)
    return output
