try:
    import telnetlib  # Standard library (Python <= 3.12)

    TELNET_MODE = "builtin"
except Exception:
    # Dynamically import telnetlib3 to avoid static analysis errors for unknown modules.
    # If telnetlib3 is not installed, telnetlib will be set to None and TELNET_MODE to None,
    # remote_connect will handle the absence and return (None, None).
    import importlib

    try:
        telnetlib = importlib.import_module("telnetlib3")
        TELNET_MODE = "asyncio"
    except Exception:
        telnetlib = None
        TELNET_MODE = None
import time
import re
import logging
import asyncio

logger = logging.getLogger("remote_utils")


class AsyncShellAdapter:
    """
    Thin synchronous adapter around telnetlib3 reader/writer so existing
    sync code can call write(...) and read_very_eager().

    Note: adapter uses asyncio.run for each operation (simple & safe).
    """

    def __init__(self, reader, writer):
        self._reader = reader
        self._writer = writer

    def write(self, data):
        # Accept bytes or str
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="ignore")
        else:
            text = str(data)

        async def _awrite():
            self._writer.write(text)
            try:
                await self._writer.drain()
            except Exception:
                # some telnetlib3 writers may not expose drain or it may be noop
                pass

        asyncio.run(_awrite())

    def read_very_eager(self):
        """
        Try to read available data quickly. Returns bytes (to match telnetlib API).
        """

        async def _aread():
            try:
                # read up to 4096 bytes with small timeout
                return await asyncio.wait_for(self._reader.read(4096), timeout=0.1)
            except asyncio.TimeoutError:
                return ""
            except Exception:
                return ""

        res = asyncio.run(_aread())
        if res is None:
            res = ""
        return res.encode("utf-8", errors="ignore")


def remote_connect(host, username="", password="", timeout=10):
    """
    Establish Telnet connection (sync for telnetlib, async for telnetlib3 fallback)
    """
    try:
        print(f"Connecting to {host} via Telnet ({TELNET_MODE})...")

        if TELNET_MODE == "builtin":
            tn = telnetlib.Telnet(host, timeout=timeout)
            try:
                # read initial banner / login prompt
                time.sleep(0.4)
                banner = tn.read_very_eager().decode("utf-8", errors="ignore")

                # common login prompt patterns
                if (
                    re.search(r"(?:username|login)[:\s]", banner, re.IGNORECASE)
                    and username
                ):
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

        elif TELNET_MODE == "asyncio":
            import asyncio

            async def async_connect():
                reader, writer = await telnetlib.open_connection(host, 23)
                # send credentials if needed
                if password:
                    writer.write(password + "\n")
                await writer.drain()
                return reader, writer

            # Run the async function in a blocking context
            reader, writer = asyncio.run(async_connect())
            return reader, writer

    except Exception as e:
        print(f"[!] Telnet connection failed: {e}")
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


def enter_enable_mode_remote(shell, enable_password=None, prompt="#", timeout=5):
    """
    Attempt to enter enable (privileged EXEC) mode.
    If device prompts for an enable password, send enable_password (if provided).
    Returns the prompt output when in privileged mode or raises on timeout.
    """
    if not shell:
        raise ValueError("Shell (telnet) is None")

    # send enable
    try:
        shell.write(b"enable\n")
    except Exception:
        try:
            shell.write(b"enable\r\n")
        except Exception:
            raise

    buf = ""
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(0.2)
        try:
            data = shell.read_very_eager()
        except Exception:
            data = b""
        if not data:
            continue

        chunk = data.decode("utf-8", errors="ignore")
        buf += chunk

        # If device asks for enable password
        if "Password:" in buf or "password:" in buf:
            if not enable_password:
                raise Exception("Device requested enable password but none provided.")
            # send provided enable password
            try:
                shell.write(enable_password.encode("utf-8") + b"\n")
            except Exception:
                shell.write(enable_password.encode("ascii", errors="ignore") + b"\n")
            buf = ""
            # continue to wait for prompt after password
            continue

        # strip ANSI for reliable detection
        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buf)

        # check for privileged prompt patterns
        if re.search(r"(?:\S+#\s*$)|(?:\(config\)#\s*$)", clean, re.M):
            return clean.strip()

    raise TimeoutError("Timed out waiting for privileged prompt after 'enable'.")
