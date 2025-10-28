# server.py
from flask import Flask, jsonify, request, Response
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
from remote_utils import (
    connect_ssh,
    disable_paging_ssh,
    enter_enable_mode_ssh,
    send_command_ssh,
    get_hostname_ssh,
)
from command_manager import load_commands, save_commands

app = Flask(__name__)

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
        return None, jsonify(
            {"status": "error", "message": "Missing examName/sessionId/studentId"}
        ), 400
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
        jsonify({"status": "error", "message": f"Path not found: {existing_path}"}), 404
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
        student_dir = (
            base_docs_path / exam_name / session_id / student_id
        )
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
    """
    POST JSON:
    {
      "connection": "serial" or "ssh",
      "serial": {"port": "/dev/ttyUSB0"},
      "ssh": {"host":"1.2.3.4","username":"u","password":"p"}
    }
    """
    data = request.get_json() or {}
    conn = data.get("connection")

    try:
        if conn == "serial":
            port = data.get("serial", {}).get("port", "/dev/ttyUSB0")
            ser = connect_to_serial(port)
            if not ser:
                return jsonify({"status": "error", "message": f"Could not open {port}"}), 400

            try:
                enter_enable_mode(ser)
                disable_paging(ser)
                hostname = get_hostname(ser)
            except Exception:
                hostname = "device"

            logout_close_connection(ser)
            return jsonify({"status": "ok", "message": f"Connected to {hostname}", "hostname": hostname})

        elif conn == "ssh":
            ssh = data.get("ssh", {})
            host, user, pwd = ssh.get("host"), ssh.get("username"), ssh.get("password")
            client, shell = connect_ssh(host, user, pwd)
            if not client or not shell:
                return jsonify({"status": "error", "message": "SSH connection failed"}), 400

            try:
                enter_enable_mode_ssh(shell)
                disable_paging_ssh(shell)
                hostname = get_hostname_ssh(shell)
            except Exception:
                hostname = "device"

            client.close()
            return jsonify({"status": "ok", "message": f"Connected to {hostname}", "hostname": hostname})

        else:
            return jsonify({"status": "error", "message": "Invalid connection type"}), 400

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({"status": "error", "message": str(e), "trace": tb}), 500


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

    base_dir = os.path.expanduser(os.path.join("~/Documents", exam_name, session_id, student_id))
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

        yield stream_json_line(
            {"type": "progress", "msg": "Starting execution workflow..."}
        )

        try:
            if connection == "serial":
                port = data.get("serial", {}).get("port", "/dev/ttyUSB0")
                yield stream_json_line(
                    {"type": "progress", "msg": f"Connecting over serial: {port}"}
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

                enter_enable_mode(ser)
                disable_paging(ser)
                hostname = get_hostname(ser)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connected to {hostname} via serial.",
                        "cmd_done": False,
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
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"Completed '{cmd}'.",
                                "cmd_done": True,
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

                yield stream_json_line(
                    {"type": "progress", "msg": f"Connecting to {host} via SSH..."}
                )
                client, shell = connect_ssh(host, username, password)
                if not client or not shell:
                    yield stream_json_line(
                        {"type": "error", "msg": "SSH connection failed."}
                    )
                    return

                enter_enable_mode_ssh(shell)
                disable_paging_ssh(shell)
                hostname = get_hostname_ssh(shell)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connected to {hostname} via SSH.",
                        "cmd_done": False,
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
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"Completed '{cmd}'.",
                                "cmd_done": True,
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
                }
            )
            yield stream_json_line({"type": "done", "msg": "Execution complete."})
        except Exception as exc:  # Unexpected runtime exception
            tb = traceback.format_exc()
            if hostname:
                del_partial_logs(
                    base_path, exam_name, session_id, student_id, hostname
                )
            yield stream_json_line(
                {"type": "error", "msg": str(exc), "trace": tb}
            )
        finally:
            if ser:
                try:
                    logout_close_connection(ser)
                except Exception:
                    pass
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

    return Response(generate(), mimetype="text/plain")

# -------------------------------------------------
# ✅ Run Flask
# -------------------------------------------------
if __name__ == "__main__":
    print("[*] Running Flask server on http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, threaded=True)
