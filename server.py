# server.py
from flask import Flask, jsonify, request, Response, stream_with_context
import threading
from queue import SimpleQueue
import os
import json
import traceback
from pathlib import Path

# Reuse your helpers
from file_utils import save_output_to_file, del_partial_logs
from serial_utils import (
    connect_to_serial,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    get_hostname,
)
from ssh_utils import (
    connect_ssh,
    disable_paging_ssh,
    enter_enable_mode_ssh,
    send_command_ssh,
    get_hostname_ssh,
)
from command_manager import load_commands, save_commands

app = Flask(__name__)

connection_lock = threading.Lock()
connection_cache = {
    "type": None,
    "serial": {"ser": None, "port": None, "hostname": None},
    "ssh": {
        "client": None,
        "shell": None,
        "host": None,
        "username": None,
        "hostname": None,
    },
}


def _release_serial_connection_locked():
    ser = connection_cache["serial"]["ser"]
    if ser:
        try:
            logout_close_connection(ser)
        except Exception:
            pass
    connection_cache["serial"] = {"ser": None, "port": None, "hostname": None}
    if connection_cache["type"] == "serial":
        connection_cache["type"] = None


def _release_ssh_connection_locked():
    shell = connection_cache["ssh"]["shell"]
    client = connection_cache["ssh"]["client"]
    if shell:
        try:
            shell.close()
        except Exception:
            pass
    if client:
        try:
            client.close()
        except Exception:
            pass
    connection_cache["ssh"] = {
        "client": None,
        "shell": None,
        "host": None,
        "username": None,
        "hostname": None,
    }
    if connection_cache["type"] == "ssh":
        connection_cache["type"] = None


def _set_serial_connection_locked(ser, port, hostname):
    _release_serial_connection_locked()
    _release_ssh_connection_locked()
    connection_cache["type"] = "serial"
    connection_cache["serial"] = {
        "ser": ser,
        "port": port,
        "hostname": hostname,
    }


def _set_ssh_connection_locked(client, shell, host, username, hostname):
    _release_serial_connection_locked()
    _release_ssh_connection_locked()
    connection_cache["type"] = "ssh"
    connection_cache["ssh"] = {
        "client": client,
        "shell": shell,
        "host": host,
        "username": username,
        "hostname": hostname,
    }


def _update_cached_serial_hostname(hostname):
    with connection_lock:
        if connection_cache["type"] == "serial" and connection_cache["serial"]["ser"]:
            connection_cache["serial"]["hostname"] = hostname


def _update_cached_ssh_hostname(hostname):
    with connection_lock:
        if connection_cache["type"] == "ssh" and connection_cache["ssh"]["client"]:
            connection_cache["ssh"]["hostname"] = hostname


def _is_ssh_transport_active(info):
    client = info.get("client")
    shell = info.get("shell")
    transport = client.get_transport() if client else None
    return (
        client
        and shell
        and transport
        and transport.is_active()
        and not getattr(shell, "closed", False)
    )


def _acquire_ssh_connection(host, username, password):
    return connect_ssh(host, username, password)


def stream_json_line(obj):
    return json.dumps(obj) + "\n"


def _expand_path(path):
    """Expand ~ in user supplied paths."""
    return os.path.expanduser(path) if path else None


# -------------------------------------------------
# ✅ Directory Endpoints
# -------------------------------------------------
def _validate_directory_payload(data):
    exam_name = data.get("examName")
    session_id = data.get("sessionId")
    student_id = data.get("studentId")

    if not all([exam_name, session_id, student_id]):
        return (
            None,
            jsonify(
                {"status": "error", "message": "Missing examName/sessionId/studentId"}
            ),
            400,
        )
    return (exam_name, session_id, student_id), None, None


@app.route("/api/create_directory", methods=["POST"])
def api_create_directory():
    """
    Create the standard directory hierarchy for a student.
    """
    data = request.get_json() or {}
    validated, error_resp, status = _validate_directory_payload(data)
    if error_resp:
        return error_resp, status

    exam_name, session_id, student_id = validated
    base_path = os.path.expanduser(
        os.path.join("~/Documents", exam_name, session_id, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    return jsonify(
        {
            "status": "ok",
            "message": f"Directory ready: {base_path}",
            "path": base_path,
            "exam_name": exam_name,
            "session_id": session_id,
            "student_id": student_id,
        }
    )


@app.route("/api/select_directory", methods=["POST"])
def api_select_directory():
    """
    Reuse an existing directory path provided by the user.
    """
    data = request.get_json() or {}
    existing_path = _expand_path(data.get("existingPath"))
    if not existing_path:
        return (
            jsonify(
                {"status": "error", "message": "Missing existingPath for selection"}
            ),
            400,
        )

    if os.path.exists(existing_path):
        parts = Path(existing_path).parts
        if len(parts) >= 3:
            exam_name, session_id, student_id = parts[-3], parts[-2], parts[-1]
        else:
            exam_name = data.get("examName")
            session_id = data.get("sessionId")
            student_id = data.get("studentId")
        return jsonify(
            {
                "status": "ok",
                "message": f"Using existing directory: {existing_path}",
                "path": existing_path,
                "exam_name": exam_name,
                "session_id": session_id,
                "student_id": student_id,
            }
        )

    return (
        jsonify({"status": "error", "message": f"Path not found: {existing_path}"}),
        404,
    )


def _list_existing_directories():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for exam_dir in docs_path.iterdir():
        if not exam_dir.is_dir() or exam_dir.name.startswith("."):
            continue
        for session_dir in exam_dir.iterdir():
            if not session_dir.is_dir():
                continue
            for student_dir in session_dir.iterdir():
                if not student_dir.is_dir():
                    continue
                results.append(
                    {
                        "path": str(student_dir),
                        "exam_name": exam_dir.name,
                        "session_id": session_dir.name,
                        "student_id": student_dir.name,
                        "display": f"{exam_dir.name}/{session_dir.name}/{student_dir.name}",
                    }
                )
    return sorted(results, key=lambda x: x["display"])


@app.route("/api/directories", methods=["GET"])
def api_list_directories():
    directories = _list_existing_directories()
    return jsonify({"status": "ok", "directories": directories})


@app.route("/api/directories/bulk", methods=["POST"])
def api_bulk_directories():
    data = request.get_json() or {}
    exam_name = data.get("examName")
    session_id = data.get("sessionId")
    students = data.get("students") or []

    if not exam_name or not session_id or not students:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing examName/sessionId/students for bulk creation.",
                }
            ),
            400,
        )

    created = []
    base_docs_path = Path.home() / "Documents"

    for student in students:
        student_id = (student.get("id") or "").strip()
        if not student_id:
            continue
        student_dir = base_docs_path / exam_name / session_id / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        created.append(
            {
                "path": str(student_dir),
                "exam_name": exam_name,
                "session_id": session_id,
                "student_id": student_id,
                "display": f"{exam_name}/{session_id}/{student_id}",
            }
        )

    return jsonify({"status": "ok", "created": created})


# -------------------------------------------------
# ✅ Connection Test Endpoint
# -------------------------------------------------
@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Stream connection progress to the client for serial/SSH tests."""
    data = request.get_json() or {}
    conn = data.get("connection")

    def stream_error(message, trace=None):
        print(f"[API][connect] ERROR: {message}", flush=True)
        payload = {"type": "error", "msg": message}
        if trace:
            payload["trace"] = trace
        payload["done"] = True
        return stream_json_line(payload)

    def serial_generator():
        port = data.get("serial", {}).get("port", "/dev/ttyUSB0")

        with connection_lock:
            cached_info = connection_cache["serial"]
            cached_ser = cached_info["ser"]
            if (
                connection_cache["type"] == "serial"
                and cached_ser
                and cached_ser.is_open
                and cached_info.get("port") == port
            ):
                hostname = cached_info.get("hostname") or "device"
                print(
                    f"[API][connect][serial] Reusing cached session ({port})",
                    flush=True,
                )
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Reusing existing serial session on {port}",
                    }
                )
                yield stream_json_line(
                    {
                        "type": "success",
                        "msg": f"Connected to {hostname}",
                        "hostname": hostname,
                        "port": port,
                        "persistent": True,
                    }
                )
                yield stream_json_line(
                    {
                        "type": "done",
                        "success": True,
                        "hostname": hostname,
                        "port": port,
                    }
                )
                return

        yield stream_json_line(
            {
                "type": "progress",
                "msg": f"Connecting over serial: {port}",
            }
        )

        queue = SimpleQueue()

        def status_cb(message):
            queue.put(("progress", message))

        def worker():
            ser = None
            cached = False
            try:
                ser = connect_to_serial(
                    port,
                    retry_interval=1,
                    max_retries=5,
                    status_cb=status_cb,
                )
                if not ser:
                    queue.put(("error", f"Failed to open serial port {port}"))
                    return

                queue.put(("progress", "Ensuring privileged access..."))
                enter_enable_mode(ser)
                queue.put(("progress", "Disabling paging..."))
                disable_paging(ser)
                try:
                    hostname = get_hostname(ser) or "device"
                except Exception:
                    hostname = "device"

                with connection_lock:
                    _set_serial_connection_locked(ser, port, hostname)
                    cached = True

                queue.put(("success", {"hostname": hostname, "port": port}))
            except Exception as exc:
                if ser and not cached:
                    try:
                        logout_close_connection(ser)
                    except Exception:
                        pass
                queue.put(("exception", (str(exc), traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()

        while True:
            event, payload = queue.get()
            if event == "progress":
                print(f"[API][connect][serial] {payload}", flush=True)
                yield stream_json_line({"type": "progress", "msg": payload})
            elif event == "success":
                hostname = payload.get("hostname", "device")
                port_value = payload.get("port")
                print(
                    f"[API][connect][serial] Connected to {hostname} (port={port_value})",
                    flush=True,
                )
                yield stream_json_line(
                    {
                        "type": "success",
                        "msg": f"Connected to {hostname}",
                        "hostname": hostname,
                        "port": port_value,
                        "persistent": True,
                    }
                )
                yield stream_json_line(
                    {
                        "type": "done",
                        "success": True,
                        "hostname": hostname,
                        "port": port_value,
                    }
                )
                return
            elif event == "error":
                print(f"[API][connect][serial] ERROR: {payload}", flush=True)
                yield stream_json_line({"type": "error", "msg": payload})
                yield stream_json_line({"type": "done", "success": False})
                return
            elif event == "exception":
                msg, tb = payload
                print(f"[API][connect][serial] EXCEPTION: {msg}", flush=True)
                yield stream_json_line({"type": "error", "msg": msg, "trace": tb})
                yield stream_json_line({"type": "done", "success": False})
                return

    def ssh_generator():
        ssh = data.get("ssh", {})
        host, user, pwd = ssh.get("host"), ssh.get("username"), ssh.get("password")
        if not all([host, user, pwd]):
            print("[API][connect][ssh] Missing credentials.", flush=True)
            yield stream_json_line(
                {
                    "type": "error",
                    "msg": "Missing SSH credentials (host, username, password).",
                }
            )
            yield stream_json_line({"type": "done", "success": False})
            return

        print(f"[API][connect][ssh] Connecting to {host} ...", flush=True)
        yield stream_json_line(
            {"type": "progress", "msg": f"Connecting to {host} via SSH..."}
        )
        cached = False
        client = None
        shell = None
        try:
            client, shell = remote_connect(host, user, pwd)
            if not client or not shell:
                print(f"[API][connect][ssh] Connection to {host} failed.", flush=True)
                yield stream_json_line(
                    {"type": "error", "msg": "SSH connection failed."}
                )
                yield stream_json_line({"type": "done", "success": False})
                return

            print("[API][connect][ssh] Entering enable mode...", flush=True)
            yield stream_json_line(
                {"type": "progress", "msg": "Entering enable mode..."}
            )
            enter_enable_mode_ssh(shell)
            print("[API][connect][ssh] Disabling paging...", flush=True)
            yield stream_json_line({"type": "progress", "msg": "Disabling paging..."})
            disable_paging_ssh(shell)
            try:
                hostname = get_hostname_ssh(shell) or host
                print(f"[API][connect][ssh] Detected hostname: {hostname}", flush=True)
                yield stream_json_line(
                    {"type": "progress", "msg": f"Detected hostname: {hostname}"}
                )
            except Exception:
                hostname = host
                print("[API][connect][ssh] Hostname detection failed.", flush=True)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Connected but hostname detection failed.",
                    }
                )

            with connection_lock:
                _set_ssh_connection_locked(client, shell, host, user, hostname)
                cached = True

            print(f"[API][connect][ssh] Connected to {hostname}", flush=True)
            yield stream_json_line(
                {
                    "type": "success",
                    "msg": f"Connected to {hostname}",
                    "hostname": hostname,
                    "host": host,
                    "persistent": True,
                }
            )
            yield stream_json_line(
                {"type": "done", "success": True, "hostname": hostname, "host": host}
            )
        except Exception as exc:
            print(f"[API][connect][ssh] EXCEPTION: {exc}", flush=True)
            if not cached:
                if shell:
                    try:
                        shell.close()
                    except Exception:
                        pass
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass
            yield stream_json_line(
                {"type": "error", "msg": str(exc), "trace": traceback.format_exc()}
            )
            yield stream_json_line({"type": "done", "success": False})

    if conn == "serial":
        generator = serial_generator()
    elif conn == "ssh":
        generator = ssh_generator()
    else:
        return Response(
            stream_error("Invalid connection type"), mimetype="text/plain", status=400
        )

    return Response(stream_with_context(generator), mimetype="text/plain")


# -------------------------------------------------
# ✅ Get Commands
# -------------------------------------------------
@app.route("/api/commands", methods=["GET"])
def api_get_commands():
    try:
        commands = load_commands()
        return jsonify({"status": "ok", "commands": commands})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/commands", methods=["POST"])
def api_add_command():
    data = request.get_json() or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"status": "error", "message": "Command text is required."}), 400

    commands = load_commands()
    if command in commands:
        return jsonify({"status": "error", "message": "Command already exists."}), 400

    commands.append(command)
    save_commands(commands)
    return jsonify({"status": "ok", "commands": commands})


@app.route("/api/commands", methods=["DELETE"])
def api_delete_command():
    data = request.get_json() or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"status": "error", "message": "Command text is required."}), 400

    commands = load_commands()
    if command not in commands:
        return jsonify({"status": "error", "message": "Command not found."}), 404

    commands = [c for c in commands if c != command]
    save_commands(commands)
    return jsonify({"status": "ok", "commands": commands})


# -------------------------------------------------
# ✅ Save Log Endpoint
# -------------------------------------------------
@app.route("/api/save_log", methods=["POST"])
def api_save_log():
    data = request.get_json() or {}
    exam_name = data.get("exam_name")
    session_id = data.get("session_id")
    student_id = data.get("student_id")
    filename = data.get("filename", "log.txt")
    content = data.get("content", "")

    if not (exam_name and session_id and student_id):
        return jsonify({"status": "error", "message": "Missing directory info"}), 400

    base_dir = os.path.expanduser(
        os.path.join("~/Documents", exam_name, session_id, student_id)
    )
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, filename)

    try:
        with open(path, "w") as f:
            f.write(content)
        return jsonify({"status": "ok", "message": f"Saved log to {path}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# -------------------------------------------------
# ✅ Execute Endpoint
# -------------------------------------------------
def _ensure_base_path(data):
    """
    Resolve the base directory for log storage based on payload.
    """
    mode = data.get("log_mode", "create")
    log_dir = data.get("log_dir")
    exam_name = data.get("exam_name")
    session_id = data.get("session_id")
    student_id = data.get("student_id")

    if mode == "existing":
        if not log_dir:
            raise ValueError("Missing log_dir for existing directory mode.")
        expanded = _expand_path(log_dir)
        if not expanded or not os.path.exists(expanded):
            raise FileNotFoundError(f"Existing directory not found: {log_dir}")
        return expanded, exam_name, session_id, student_id

    if not all([exam_name, session_id, student_id]):
        raise ValueError("Missing exam/session/student details for directory creation.")

    base_path = os.path.expanduser(
        os.path.join("~/Documents", exam_name, session_id, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    return base_path, exam_name, session_id, student_id


@app.route("/api/execute", methods=["POST"])
def api_execute():
    data = request.get_json() or {}
    commands = data.get("commands") or []
    connection = data.get("connection")

    if not commands:
        return jsonify({"status": "error", "message": "No commands provided"}), 400
    if connection not in {"serial", "ssh"}:
        return jsonify({"status": "error", "message": "Invalid connection type"}), 400

    total_commands = len(commands)

    try:
        base_path, exam_name, session_id, student_id = _ensure_base_path(data)
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    def generate():
        hostname = None
        files_written = []
        client = None
        shell = None
        ser = None
        completed = 0
        close_serial_after = True
        close_ssh_after = True

        yield stream_json_line(
            {
                "type": "progress",
                "msg": "Starting execution workflow...",
                "progress_pct": 0,
            }
        )

        try:
            if connection == "serial":
                port = data.get("serial", {}).get("port", "/dev/ttyUSB0")
                ser = None
                hostname = None
                using_cached_serial = False
                close_serial_after = True

                with connection_lock:
                    cached_info = connection_cache["serial"]
                    cached_ser = cached_info["ser"]
                    if (
                        connection_cache["type"] == "serial"
                        and cached_ser
                        and cached_ser.is_open
                        and cached_info.get("port") == port
                    ):
                        ser = cached_ser
                        hostname = cached_info.get("hostname")
                        using_cached_serial = True
                        close_serial_after = False
                    elif cached_ser:
                        _release_serial_connection_locked()
                if not using_cached_serial:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Connecting over serial: {port}",
                            "progress_pct": 0,
                        }
                    )
                    ser = connect_to_serial(port)
                    if not ser:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to open serial port {port}",
                            }
                        )
                        return
                else:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Reusing existing serial session on {port}",
                            "cmd_done": False,
                            "progress_pct": 0,
                        }
                    )
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Ensuring privileged access...",
                        "cmd_done": False,
                        "progress_pct": 0,
                    }
                )
                enter_enable_mode(ser)
                disable_paging(ser)
                try:
                    hostname = get_hostname(ser) or hostname or "device"
                except Exception:
                    hostname = hostname or "device"

                if using_cached_serial:
                    _update_cached_serial_hostname(hostname)
                else:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Connected to {hostname} via serial.",
                            "cmd_done": False,
                            "progress_pct": 0,
                        }
                    )

                for cmd in commands:
                    yield stream_json_line(
                        {"type": "progress", "msg": f"Running '{cmd}'..."}
                    )
                    try:
                        output = send_command(ser, cmd, timeout=30)
                        file_path = save_output_to_file(
                            cmd,
                            output,
                            exam_name,
                            student_id,
                            session_id,
                            hostname,
                            base_dir=base_path,
                        )
                        files_written.append(file_path)
                        completed = min(total_commands, completed + 1)
                        pct = (
                            round((completed / total_commands) * 100)
                            if total_commands
                            else 100
                        )
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"Completed '{cmd}'.",
                                "cmd_done": True,
                                "progress_pct": pct,
                            }
                        )
                    except Exception as exc:
                        del_partial_logs(
                            base_path, exam_name, session_id, student_id, hostname
                        )
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Command '{cmd}' failed: {exc}",
                            }
                        )
                        return

            else:  # SSH
                ssh_data = data.get("ssh") or {}
                host = ssh_data.get("host")
                username = ssh_data.get("username")
                password = ssh_data.get("password")

                if not all([host, username, password]):
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": "Missing SSH credentials (host/username/password).",
                        }
                    )
                    return

                client = None
                shell = None
                hostname = None
                close_ssh_after = True
                using_cached_ssh = False

                with connection_lock:
                    cached_info = connection_cache["ssh"]
                    if (
                        connection_cache["type"] == "ssh"
                        and _is_ssh_transport_active(cached_info)
                        and cached_info.get("host") == host
                        and cached_info.get("username") == username
                    ):
                        client = cached_info["client"]
                        shell = cached_info["shell"]
                        hostname = cached_info.get("hostname") or host
                        close_ssh_after = False
                        using_cached_ssh = True
                    elif cached_info.get("client") or cached_info.get("shell"):
                        _release_ssh_connection_locked()

                if using_cached_ssh:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Reusing existing SSH session to {host}",
                            "cmd_done": False,
                            "progress_pct": 0,
                        }
                    )
                else:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Connecting to {host} via SSH...",
                            "progress_pct": 0,
                        }
                    )
                    client, shell = _acquire_ssh_connection(host, username, password)
                    if not client or not shell:
                        yield stream_json_line(
                            {"type": "error", "msg": "SSH connection failed."}
                        )
                        return

                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Ensuring privileged access...",
                        "cmd_done": False,
                        "progress_pct": 0,
                    }
                )
                enter_enable_mode_ssh(shell)
                disable_paging_ssh(shell)
                try:
                    hostname = get_hostname_ssh(shell) or hostname or host
                except Exception:
                    hostname = hostname or host

                if using_cached_ssh:
                    _update_cached_ssh_hostname(hostname)
                else:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Connected to {hostname} via SSH.",
                            "cmd_done": False,
                            "progress_pct": 0,
                        }
                    )

                for cmd in commands:
                    yield stream_json_line(
                        {"type": "progress", "msg": f"Running '{cmd}'..."}
                    )
                    try:
                        output = send_command_ssh(shell, cmd, timeout=30)
                        file_path = save_output_to_file(
                            cmd,
                            output,
                            exam_name,
                            student_id,
                            session_id,
                            hostname,
                            base_dir=base_path,
                        )
                        files_written.append(file_path)
                        completed = min(total_commands, completed + 1)
                        pct = (
                            round((completed / total_commands) * 100)
                            if total_commands
                            else 100
                        )
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"Completed '{cmd}'.",
                                "cmd_done": True,
                                "progress_pct": pct,
                            }
                        )
                    except Exception as exc:
                        del_partial_logs(
                            base_path, exam_name, session_id, student_id, hostname
                        )
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Command '{cmd}' failed: {exc}",
                            }
                        )
                        return

            yield stream_json_line(
                {
                    "type": "result",
                    "msg": "All commands executed successfully.",
                    "files": files_written,
                    "progress_pct": (
                        round((completed / total_commands) * 100)
                        if total_commands
                        else 100
                    ),
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "msg": "Execution complete.",
                    "progress_pct": 100,
                }
            )
        except Exception as exc:  # Unexpected runtime exception
            tb = traceback.format_exc()
            if hostname:
                del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
            yield stream_json_line({"type": "error", "msg": str(exc), "trace": tb})
        finally:
            if ser and close_serial_after:
                try:
                    logout_close_connection(ser)
                except Exception:
                    pass
            if shell and close_ssh_after:
                try:
                    shell.close()
                except Exception:
                    pass
            if client and close_ssh_after:
                try:
                    client.close()
                except Exception:
                    pass

    return Response(generate(), mimetype="text/plain")


# -------------------------------------------------
# ✅ Run Flask
# -------------------------------------------------
if __name__ == "__main__":
    print("[*] Running Flask server on http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, threaded=True)
