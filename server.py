# server.py
from flask import Flask, jsonify, request, Response, stream_with_context
import threading
from queue import SimpleQueue
import os
import json
import traceback
from pathlib import Path
import time
import sys
import yaml
import re
import glob
import shutil

# Reuse your helpers
from file_utils import save_output_to_file, del_partial_logs
from serial_utils import (
    connect_to_serial,
    READ_TIMEOUT,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    get_hostname,
)
from remote_utils import (
    remote_connect,
    disable_paging_remote,
    enter_enable_mode_remote,
    send_command_remote,
    get_hostname_remote,
)
from command_manager import load_commands, save_commands
from comparsion_engine.parser import parse_device_logs, normalize_parsed_config
from comparsion_engine.comparator import compare_dicts
from comparsion_engine.student_manager import find_show_run_file

app = Flask(__name__)

# Base directory for consistent absolute paths
BASE_DIR = Path(__file__).resolve().parent

# Grading Directories
SCHEMES_DIR = BASE_DIR / "schemes"
RUBRICS_DIR = BASE_DIR / "rubrics"
TEMPLATES_DIR = BASE_DIR / "comparsion_engine" / "templates"
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
RESULTS_DIR = None
GRADING_POLICY_PATH = BASE_DIR / "config" / "grading_policy.json"
SCHEMES_DIR.mkdir(exist_ok=True)
RUBRICS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

connection_lock = threading.Lock()

current_mode = None  # "serial" or "ssh"
serial_conn = None
serial_hostname = None
last_used_serial_settings = {"port": "/dev/ttyUSB0", "baudrate": 9600}

ssh_client = None
ssh_hostname = None
last_used_ssh_credentials = {"host": None, "username": None, "password": None, "port": 22}


def _close_serial_connection():
    global serial_conn, serial_hostname
    ser = None
    with connection_lock:
        if serial_conn:
            ser = serial_conn
            serial_conn = None
            serial_hostname = None
    if ser:
        try:
            logout_close_connection(ser)
        except Exception:
            pass


def _close_ssh_connection():
    global ssh_client, ssh_hostname
    client = None
    with connection_lock:
        if ssh_client:
            client = ssh_client
            ssh_client = None
            ssh_hostname = None
    if client:
        try:
            shell = getattr(client, "_shell", None)
            if shell:
                shell.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def _is_ssh_client_active(client):
    if not client:
        return False
    try:
        transport = client.get_transport()
        return transport and transport.is_active()
    except Exception:
        return False


def _update_serial_state(ser, port, baudrate, hostname):
    global serial_conn, serial_hostname, current_mode
    with connection_lock:
        last_used_serial_settings["port"] = port
        last_used_serial_settings["baudrate"] = baudrate
        serial_conn = ser
        serial_hostname = hostname
        current_mode = "serial"


def _update_ssh_state(client, host, username, password, hostname, port):
    global ssh_client, ssh_hostname, current_mode
    with connection_lock:
        last_used_ssh_credentials["host"] = host
        last_used_ssh_credentials["username"] = username
        last_used_ssh_credentials["password"] = password
        last_used_ssh_credentials["port"] = port
        ssh_client = client
        ssh_hostname = hostname
        current_mode = "ssh"


def _default_grading_policy():
    return {
        "major_patterns": [],
        "major_threshold": 1,
        "minor_threshold": 5,
    }


def load_grading_policy():
    if not GRADING_POLICY_PATH.exists():
        GRADING_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(GRADING_POLICY_PATH, "w") as handle:
            json.dump(_default_grading_policy(), handle, indent=2)
        return _default_grading_policy()
    try:
        with open(GRADING_POLICY_PATH, "r") as handle:
            data = json.load(handle) or {}
    except Exception:
        data = {}
    policy = _default_grading_policy()
    policy.update({k: v for k, v in data.items() if v is not None})
    return policy


def save_grading_policy(data):
    policy = _default_grading_policy()
    policy.update(data or {})
    GRADING_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GRADING_POLICY_PATH, "w") as handle:
        json.dump(policy, handle, indent=2)
    return policy


def _safe_resolve_child(base: Path, target: Path) -> Path:
    base = base.resolve()
    target = target.resolve()
    if base == target or base in target.parents:
        return target
    return None


def _iter_session_students(target_path: str):
    if not target_path or not os.path.isdir(target_path):
        return []
    students = []
    for entry in sorted(os.listdir(target_path)):
        full = os.path.join(target_path, entry)
        if os.path.isdir(full):
            students.append({"student_id": entry, "path": full})
    return students


def _load_student_results(student_dir: Path, student_id: str):
    if not student_dir.is_dir():
        return None

    results_dir = student_dir / "results"
    if not results_dir.is_dir():
        return None

    host_results = {}
    all_items = []
    for file_path in sorted(results_dir.glob("*_result.json")):
        try:
            with open(file_path, "r") as handle:
                data = json.load(handle) or {}
        except Exception:
            continue

        hostname = data.get("hostname") or file_path.stem.replace("_result", "")
        results = data.get("results") or []
        host_results[hostname] = {
            "hostname": hostname,
            "grading_mode": data.get("grading_mode"),
            "template_name": data.get("template_name"),
            "student_show_run_file": data.get("student_show_run_file"),
            "student_parsed_file": data.get("student_parsed_file"),
            "results": results,
        }
        for item in results:
            item_copy = dict(item)
            item_copy["hostname"] = hostname
            all_items.append(item_copy)

    if not host_results:
        return None

    template_name = None
    grading_mode = None
    for host in host_results.values():
        if not template_name:
            template_name = host.get("template_name")
        if not grading_mode:
            grading_mode = host.get("grading_mode")

    return {
        "student_id": student_id,
        "template_name": template_name,
        "grading_mode": grading_mode,
        "hostnames": host_results,
        "items": all_items,
    }


def _classify_items(items, policy):
    major_patterns = policy.get("major_patterns") or []
    compiled = []
    for pattern in major_patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue

    summary = {
        "correct": 0,
        "missing": 0,
        "extra": 0,
        "mismatch": 0,
        "major": 0,
        "minor": 0,
    }

    classified = []
    for item in items:
        status = item.get("status")
        if status in summary:
            summary[status] += 1

        severity = None
        if status in {"missing", "extra", "mismatch"}:
            is_major = any(
                regex.search(item.get("feature", "")) for regex in compiled
            )
            severity = "major" if is_major else "minor"
            summary[severity] += 1

        item_copy = dict(item)
        if severity:
            item_copy["severity"] = severity
        classified.append(item_copy)

    return classified, summary


def _evaluate_pass_fail(summary, policy):
    major_threshold = int(policy.get("major_threshold") or 1)
    minor_threshold = int(policy.get("minor_threshold") or 5)
    failed = summary.get("major", 0) >= major_threshold
    if not failed:
        failed = summary.get("minor", 0) >= minor_threshold
    return not failed


def _build_session_reports(target_path: str):
    policy = load_grading_policy()
    reports = []
    for student in _iter_session_students(target_path):
        student_id = student.get("student_id")
        student_dir = Path(student.get("path") or "")
        report = _load_student_results(student_dir, student_id)
        if not report:
            reports.append(
                {
                    "student_id": student_id,
                    "status": "no_results",
                    "pass": False,
                    "summary": {
                        "correct": 0,
                        "missing": 0,
                        "extra": 0,
                        "mismatch": 0,
                        "major": 0,
                        "minor": 0,
                    },
                    "hostnames": {},
                    "items": [],
                }
            )
            continue

        items, summary = _classify_items(report["items"], policy)
        passed = _evaluate_pass_fail(summary, policy)
        report["items"] = items
        report["summary"] = summary
        report["pass"] = passed
        report["status"] = "graded"
        reports.append(report)

    return reports


def _acquire_ssh_connection(host, username, password, port=None):
    try:
        port_value = int(str(port)) if port is not None else 22
    except ValueError:
        port_value = 22
    client = remote_connect(host, username, password, port=port_value)
    if not client:
        return None, None, port_value
    shell = getattr(client, "_shell", None)
    if shell is None:
        try:
            shell = client.invoke_shell()
            client._shell = shell
        except Exception:
            shell = None
    return client, shell, port_value


def stream_json_line(obj):
    return json.dumps(obj) + "\n"


def _expand_path(path):
    """Expand ~ in user supplied paths."""
    return os.path.expanduser(path) if path else None


def _save_output_to_engine_students(command, output, student_id, hostname):
    """
    Save command output under comparsion_engine/students/<student_id>/<hostname>/.
    Only stores command logs (no config.json).
    """
    if not student_id or not hostname:
        return None
    safe_student = str(student_id).strip()
    if not safe_student or safe_student.lower() in {"sample", "unknown"}:
        return None
    safe_command = command.replace(" ", "_").replace("/", "_")
    target_dir = BASE_DIR / "comparsion_engine" / "students" / safe_student / hostname
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{safe_command}.txt"
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(output)
    return str(file_path)


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


def _list_existing_sessions():
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
            results.append(
                {
                    "path": str(session_dir),
                    "exam_name": exam_dir.name,
                    "session_id": session_dir.name,
                    "display": f"{exam_dir.name}/{session_dir.name}",
                }
            )
    return sorted(results, key=lambda x: x["display"])


@app.route("/api/directories", methods=["GET"])
def api_list_directories():
    path_val = request.args.get("path")
    docs_path = (Path.home() / "Documents").resolve()
    
    # If a path is provided, use it as the "current" one, otherwise default to ~/Documents
    if path_val:
        try:
            current = Path(_expand_path(path_val)).resolve()
        except Exception:
            current = docs_path
    else:
        current = docs_path
            
    # Only return the managed "directories" list if we are explicitly at the managed root.
    # Otherwise, we want the frontend to fall back to 'loadSubfolders' to show the actual directory contents.
    directories = []
    if current == docs_path:
        directories = _list_existing_directories()

    return jsonify({
        "status": "ok", 
        "directories": directories,
        "current_path": str(current)
    })

@app.route("/api/subfolders", methods=["GET"])
def api_list_subfolders():
    path_val = request.args.get("path")
    
    # If path not provided, default to user home so they can see Documents, Downloads etc.
    if not path_val:
        target = Path.home()
    else:
        try:
            target = Path(_expand_path(path_val)).resolve()
        except:
            return jsonify({"status": "error", "message": "Invalid path"}), 400
        
    if not target.exists() or not target.is_dir():
         return jsonify({"status": "error", "message": "Path not found"}), 404
         
    subfolders = []
    try:
        # List directories only
        for item in target.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                subfolders.append({
                    "name": item.name,
                    "path": str(item)
                })
        subfolders.sort(key=lambda x: x["name"].lower())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
        
    return jsonify({
        "status": "ok", 
        "subfolders": subfolders,
        "current_path": str(target),
        "parent_path": str(target.parent)
    })


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
    mode = (data.get("mode") or data.get("connection") or "").lower()

    def stream_error(message, trace=None):
        print(f"[API][connect] ERROR: {message}", flush=True)
        payload = {"type": "error", "msg": message}
        if trace:
            payload["trace"] = trace
        payload["done"] = True
        return stream_json_line(payload)

    if mode not in {"serial", "ssh"}:
        return Response(
            stream_error("Invalid connection type"), mimetype="text/plain", status=400
        )

    def serial_generator():
        global current_mode
        serial_cfg = data.get("serial") or {}
        with connection_lock:
            stored_port = last_used_serial_settings.get("port")
            stored_baud = last_used_serial_settings.get("baudrate", 9600)
            existing_hostname = serial_hostname or "device"
        port = serial_cfg.get("port") or stored_port or "/dev/ttyUSB0"
        baudrate = (
            serial_cfg.get("baudrate") or serial_cfg.get("baud") or stored_baud or 9600
        )
        with connection_lock:
            last_used_serial_settings["port"] = port
            last_used_serial_settings["baudrate"] = baudrate

        _close_serial_connection()
        _close_ssh_connection()

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
            try:
                ser = connect_to_serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=READ_TIMEOUT,
                    retry_interval=3,
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

                _update_serial_state(ser, port, baudrate, hostname)

                queue.put(("success", {"hostname": hostname, "port": port}))
            except Exception as exc:
                if ser:
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
        global current_mode
        ssh = data.get("ssh") or {}
        host = ssh.get("host") or data.get("host")
        user = ssh.get("username") or data.get("username")
        pwd = ssh.get("password") or data.get("password")
        raw_port = ssh.get("port") or data.get("port") or last_used_ssh_credentials.get(
            "port", 22
        )
        try:
            port_value = int(str(raw_port))
        except (TypeError, ValueError):
            port_value = 22

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

        with connection_lock:
            active_client = ssh_client if _is_ssh_client_active(ssh_client) else None
            cached_host = last_used_ssh_credentials.get("host")
            cached_user = last_used_ssh_credentials.get("username")
            cached_port = last_used_ssh_credentials.get("port")
            cached_hostname = ssh_hostname or host

        if (
            active_client
            and cached_host == host
            and cached_user == user
            and (cached_port or port_value) == port_value
        ):
            print(f"[API][connect][ssh] Reusing SSH session to {host}", flush=True)
            with connection_lock:
                current_mode = "ssh"
            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Reusing existing SSH session to {host}",
                }
            )
            yield stream_json_line(
                {
                    "type": "success",
                    "msg": f"Connected to {cached_hostname}",
                    "hostname": cached_hostname,
                    "host": host,
                    "port": port_value,
                    "persistent": True,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "success": True,
                    "hostname": cached_hostname,
                    "host": host,
                    "port": port_value,
                }
            )
            return

        _close_serial_connection()
        _close_ssh_connection()

        print(
            f"[API][connect][ssh] Connecting to {host}:{port_value} ...", flush=True
        )
        yield stream_json_line(
            {
                "type": "progress",
                "msg": f"Connecting to {host}:{port_value} via SSH...",
            }
        )

        try:
            result = _acquire_ssh_connection(host, user, pwd, port_value)
            client, shell, resolved_port = result
            if not client:
                print(
                    f"[API][connect][ssh] Connection to {host}:{port_value} failed.",
                    flush=True,
                )
                yield stream_json_line(
                    {"type": "error", "msg": "SSH connection failed."}
                )
                yield stream_json_line({"type": "done", "success": False})
                return

            print("[API][connect][ssh] Entering enable mode...", flush=True)
            yield stream_json_line(
                {"type": "progress", "msg": "Entering enable mode..."}
            )
            enter_enable_mode_remote(client)
            print("[API][connect][ssh] Disabling paging...", flush=True)
            yield stream_json_line({"type": "progress", "msg": "Disabling paging..."})
            disable_paging_remote(client)
            try:
                hostname = get_hostname_remote(client) or host
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

            _update_ssh_state(client, host, user, pwd, hostname, resolved_port)

            print(f"[API][connect][ssh] Connected to {hostname}", flush=True)
            yield stream_json_line(
                {
                    "type": "success",
                    "msg": f"Connected to {hostname}",
                    "hostname": hostname,
                    "host": host,
                    "port": resolved_port,
                    "persistent": True,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "success": True,
                    "hostname": hostname,
                    "host": host,
                    "port": resolved_port,
                }
            )
        except Exception as exc:
            print(f"[API][connect][ssh] EXCEPTION: {exc}", flush=True)
            yield stream_json_line(
                {"type": "error", "msg": str(exc), "trace": traceback.format_exc()}
            )
            yield stream_json_line({"type": "done", "success": False})

    generator = serial_generator() if mode == "serial" else ssh_generator()
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
    target_device = data.get("deviceId") or data.get("target_device")
    requested_mode = (
        data.get("mode") or data.get("connection") or current_mode or "serial"
    ).lower()

    print(
        f"[DEBUG] /api/execute called with mode={requested_mode}, current_mode={current_mode}, deviceId={target_device}",
        flush=True,
    )

    if not commands:
        return jsonify({"status": "error", "message": "No commands provided"}), 400
    if not requested_mode:
        return (
            jsonify({"status": "error", "message": "No connection mode selected."}),
            400,
        )
    if requested_mode not in {"serial", "ssh"}:
        return (
            jsonify({"status": "error", "message": "Invalid connection type"}),
            400,
        )

    try:
        base_path, exam_name, session_id, student_id = _ensure_base_path(data)
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    def generate():
        hostname = None
        files_written = []
        skip_config = bool(data.get("skip_config"))

        def run_serial():
            global current_mode
            nonlocal hostname
            serial_payload = data.get("serial") or {}
            with connection_lock:
                stored_port = last_used_serial_settings.get("port")
                stored_baud = last_used_serial_settings.get("baudrate", 9600)
                existing_ser = serial_conn if serial_conn and serial_conn.is_open else None
                stored_hostname = serial_hostname or "device"
            port = serial_payload.get("port") or stored_port or "/dev/ttyUSB0"
            baudrate = (
                serial_payload.get("baudrate")
                or serial_payload.get("baud")
                or stored_baud
                or 9600
            )
            if not port:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Serial mode selected but no port configured. Please connect via serial first.",
                    }
                )
                return False
            with connection_lock:
                last_used_serial_settings["port"] = port
                last_used_serial_settings["baudrate"] = baudrate

            ser = None
            reuse = False
            _close_ssh_connection()
            _close_serial_connection()
            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Connecting over serial: {port}",
                    "progress_pct": 0,
                }
            )
            try:
                ser = connect_to_serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=READ_TIMEOUT,
                    retry_interval=3,
                    max_retries=5,
                )
            except Exception as exc:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: {exc}",
                    }
                )
                return False
            try:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Ensuring privileged access...",
                        "progress_pct": 0,
                    }
                )
                enter_enable_mode(ser)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Disabling paging...",
                        "progress_pct": 0,
                    }
                )
                disable_paging(ser)
                try:
                    hostname = get_hostname(ser) or "device"
                except Exception:
                    hostname = "device"
            except Exception as exc:
                logout_close_connection(ser)
                yield stream_json_line(
                    {"type": "error", "msg": f"Serial initialization failed: {exc}"}
                )
                return False
            _update_serial_state(ser, port, baudrate, hostname)

            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Connected to {hostname} via serial.",
                    "progress_pct": 0,
                }
            )

            local_ser = ser or serial_conn
            if not local_ser:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Serial connection unavailable after setup.",
                    }
                )
                return False

            completed = 0
            total_commands = len(commands)
            for cmd in commands:
                yield stream_json_line({"type": "progress", "msg": f"Running '{cmd}'..."})
                try:
                    output = send_command(local_ser, cmd, timeout=30)
                    file_path = save_output_to_file(
                        cmd,
                        output,
                        exam_name,
                        student_id,
                        session_id,
                        target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cmd,
                        output,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = round((completed / total_commands) * 100) if total_commands else 100
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cmd}' failed: {exc}",
                        }
                    )
                    _close_serial_connection()
                    return False

            if not skip_config:
                # Build parsed config.json for the student device logs.
                try:
                    host_folder = target_device or hostname or "device"
                    host_dir = os.path.join(base_path, host_folder)
                    os.makedirs(host_dir, exist_ok=True)
                    config = parse_device_logs(files_written)
                    config_path = os.path.join(host_dir, "config.json")
                    with open(config_path, "w") as handle:
                        json.dump(config, handle, indent=4)
                    yield stream_json_line(
                        {"type": "result", "msg": f"Saved config.json to {config_path}"}
                    )
                except Exception as exc:
                    try:
                        host_folder = target_device or hostname or "device"
                        host_dir = os.path.join(base_path, host_folder)
                        os.makedirs(host_dir, exist_ok=True)
                        fallback = parse_device_logs([])
                        fallback["parse_error"] = str(exc)
                        config_path = os.path.join(host_dir, "config.json")
                        with open(config_path, "w") as handle:
                            json.dump(fallback, handle, indent=4)
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to parse logs ({exc}). Wrote fallback config.json to {config_path}",
                            }
                        )
                    except Exception as exc2:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to save config.json: {exc}; fallback failed: {exc2}",
                            }
                        )
            
            # Close the port so the user can physically unplug the cable for the next queue item
            _close_serial_connection()
            return True

        def run_ssh():
            global current_mode
            nonlocal hostname
            ssh_payload = data.get("ssh") or {}
            with connection_lock:
                active_client = ssh_client if _is_ssh_client_active(ssh_client) else None
                cached_host = last_used_ssh_credentials.get("host")
                cached_user = last_used_ssh_credentials.get("username")
                cached_port = last_used_ssh_credentials.get("port")
                stored_hostname = ssh_hostname or ssh_payload.get("host")
            host = ssh_payload.get("host") or cached_host
            username = ssh_payload.get("username") or cached_user
            password = ssh_payload.get("password") or last_used_ssh_credentials.get("password")
            raw_port = ssh_payload.get("port") or cached_port or 22
            try:
                port_value = int(str(raw_port))
            except (TypeError, ValueError):
                port_value = 22

            if not all([host, username, password]):
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Missing SSH credentials (host/username/password).",
                    }
                )
                return False

            client = None
            reuse = False
            if (
                active_client
                and cached_host == host
                and cached_user == username
                and (cached_port or port_value) == port_value
            ):
                client = active_client
                hostname = stored_hostname or host
                reuse = True
                with connection_lock:
                    current_mode = "ssh"
            else:
                _close_serial_connection()
                _close_ssh_connection()
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connecting to {host} via SSH...",
                        "progress_pct": 0,
                    }
                )
                result = _acquire_ssh_connection(host, username, password, port_value)
                client, shell, resolved_port = result
                if not client:
                    yield stream_json_line({"type": "error", "msg": "SSH connection failed."})
                    return False
                try:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": "Entering enable mode...",
                            "progress_pct": 0,
                        }
                    )
                    enter_enable_mode_remote(client)
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": "Disabling paging...",
                            "progress_pct": 0,
                        }
                    )
                    disable_paging_remote(client)
                    try:
                        hostname = get_hostname_remote(client) or host
                    except Exception:
                        hostname = host
                except Exception as exc:
                    try:
                        if client:
                            existing_shell = getattr(client, "_shell", None)
                            if existing_shell:
                                existing_shell.close()
                            client.close()
                    except Exception:
                        pass
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"SSH initialization failed: {exc}",
                        }
                    )
                    return False
                _update_ssh_state(client, host, username, password, hostname, resolved_port)

            if not reuse:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connected to {hostname} via SSH.",
                        "progress_pct": 0,
                    }
                )

            active = client or ssh_client
            if not active:
                yield stream_json_line(
                    {"type": "error", "msg": "SSH connection unavailable after setup."}
                )
                return False

            completed = 0
            total_commands = len(commands)
            for cmd in commands:
                yield stream_json_line({"type": "progress", "msg": f"Running '{cmd}'..."})
                try:
                    output = send_command_remote(active, cmd, timeout=30)
                    file_path = save_output_to_file(
                        cmd,
                        output,
                        exam_name,
                        student_id,
                        session_id,
                        target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cmd,
                        output,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = round((completed / total_commands) * 100) if total_commands else 100
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cmd}' failed: {exc}",
                        }
                    )
                    return False

            if not skip_config:
                # Build parsed config.json for the student device logs.
                try:
                    host_folder = target_device or hostname or "device"
                    host_dir = os.path.join(base_path, host_folder)
                    os.makedirs(host_dir, exist_ok=True)
                    config = parse_device_logs(files_written)
                    config_path = os.path.join(host_dir, "config.json")
                    with open(config_path, "w") as handle:
                        json.dump(config, handle, indent=4)
                    yield stream_json_line(
                        {"type": "result", "msg": f"Saved config.json to {config_path}"}
                    )
                except Exception as exc:
                    try:
                        host_folder = target_device or hostname or "device"
                        host_dir = os.path.join(base_path, host_folder)
                        os.makedirs(host_dir, exist_ok=True)
                        fallback = parse_device_logs([])
                        fallback["parse_error"] = str(exc)
                        config_path = os.path.join(host_dir, "config.json")
                        with open(config_path, "w") as handle:
                            json.dump(fallback, handle, indent=4)
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to parse logs ({exc}). Wrote fallback config.json to {config_path}",
                            }
                        )
                    except Exception as exc2:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to save config.json: {exc}; fallback failed: {exc2}",
                            }
                        )
            return True

        yield stream_json_line(
            {
                "type": "progress",
                "msg": "Starting execution workflow...",
                "progress_pct": 0,
            }
        )

        try:
            if requested_mode == "serial":
                if not (yield from run_serial()):
                    return
            else:
                if not (yield from run_ssh()):
                    return

            yield stream_json_line(
                {
                    "type": "result",
                    "msg": "All commands executed successfully.",
                    "files": files_written,
                    "progress_pct": 100,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "msg": "Execution complete.",
                    "progress_pct": 100,
                }
            )
        except Exception as exc:
            tb = traceback.format_exc()
            if hostname:
                del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
            yield stream_json_line({"type": "error", "msg": str(exc), "trace": tb})

    return Response(generate(), mimetype="text/plain")



# -------------------------------------------------
# ✅ Grading System Endpoints
# -------------------------------------------------

def _get_yaml_file(directory, file_id):
    path = directory / f"{file_id}.yaml"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return None

def _save_yaml_file(directory, file_id, data):
    path = directory / f"{file_id}.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f)
    return str(path)

def _delete_yaml_file(directory, file_id):
    path = directory / f"{file_id}.yaml"
    if path.exists():
        path.unlink()
        return True
    return False

def _list_yaml_files(directory):
    items = []
    if not directory.exists():
        return items
    for f in directory.glob("*.yaml"):
        try:
            with open(f, "r") as yf:
                data = yaml.safe_load(yf) or {}
                # Ensure ID is present
                if "id" not in data:
                    data["id"] = f.stem
                items.append(data)
        except Exception:
            continue
    return sorted(items, key=lambda x: x.get("name", ""))

# --- Schemes ---

@app.route("/api/schemes", methods=["GET"])
def api_list_schemes():
    return jsonify({"status": "ok", "schemes": _list_yaml_files(SCHEMES_DIR)})

@app.route("/api/schemes", methods=["POST"])
def api_save_scheme():
    data = request.get_json() or {}
    scheme_id = data.get("id")
    if not scheme_id:
        import uuid
        scheme_id = str(uuid.uuid4())[:8]
        data["id"] = scheme_id
    
    try:
        _save_yaml_file(SCHEMES_DIR, scheme_id, data)
        return jsonify({"status": "ok", "message": "Scheme saved", "id": scheme_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/schemes/<scheme_id>", methods=["DELETE"])
def api_delete_scheme(scheme_id):
    if _delete_yaml_file(SCHEMES_DIR, scheme_id):
        return jsonify({"status": "ok", "message": "Scheme deleted"})
    return jsonify({"status": "error", "message": "Scheme not found"}), 404

# --- Rubrics ---

@app.route("/api/rubrics", methods=["GET"])
def api_list_rubrics():
    return jsonify({"status": "ok", "rubrics": _list_yaml_files(RUBRICS_DIR)})

@app.route("/api/rubrics", methods=["POST"])
def api_save_rubric():
    data = request.get_json() or {}
    rubric_id = data.get("id")
    if not rubric_id:
        import uuid
        rubric_id = str(uuid.uuid4())[:8]
        data["id"] = rubric_id
        
    try:
        _save_yaml_file(RUBRICS_DIR, rubric_id, data)
        return jsonify({"status": "ok", "message": "Rubric saved", "id": rubric_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/rubrics/<rubric_id>", methods=["DELETE"])
def api_delete_rubric(rubric_id):
    if _delete_yaml_file(RUBRICS_DIR, rubric_id):
        return jsonify({"status": "ok", "message": "Rubric deleted"})
    return jsonify({"status": "error", "message": "Rubric not found"}), 404

# --- Grading Policy ---

@app.route("/api/grading_policy", methods=["GET"])
def api_get_grading_policy():
    return jsonify({"status": "ok", "policy": load_grading_policy()})


@app.route("/api/grading_policy", methods=["POST"])
def api_save_grading_policy():
    data = request.get_json() or {}
    policy = load_grading_policy()

    major_patterns = data.get("major_patterns", policy.get("major_patterns"))
    major_threshold = data.get("major_threshold", policy.get("major_threshold"))
    minor_threshold = data.get("minor_threshold", policy.get("minor_threshold"))

    try:
        major_threshold = int(major_threshold)
        minor_threshold = int(minor_threshold)
    except Exception:
        return jsonify({"status": "error", "message": "Thresholds must be integers."}), 400

    if major_threshold < 1 or minor_threshold < 1:
        return jsonify(
            {"status": "error", "message": "Thresholds must be at least 1."}
        ), 400

    if not isinstance(major_patterns, list):
        return jsonify(
            {"status": "error", "message": "major_patterns must be a list."}
        ), 400

    policy = save_grading_policy(
        {
            "major_patterns": major_patterns,
            "major_threshold": major_threshold,
            "minor_threshold": minor_threshold,
        }
    )
    return jsonify({"status": "ok", "policy": policy})


# --- Results View ---

@app.route("/api/results", methods=["GET"])
def api_get_results():
    target_path = request.args.get("target_path")
    if not target_path:
        return jsonify({"status": "error", "message": "Missing target_path."}), 400
    if not os.path.isdir(target_path):
        return jsonify({"status": "error", "message": "target_path not found."}), 404

    return jsonify(
        {
            "status": "ok",
            "reports": _build_session_reports(target_path),
            "policy": load_grading_policy(),
        }
    )


# -------------------------------------------------
# ✅ Admin Cleanup
# -------------------------------------------------
@app.route("/api/admin/templates", methods=["GET"])
def api_admin_list_templates():
    templates = []
    if TEMPLATES_DIR.is_dir():
        for entry in sorted(TEMPLATES_DIR.iterdir()):
            if entry.is_dir():
                templates.append(entry.name)
    return jsonify({"status": "ok", "templates": templates})


@app.route("/api/templates/<template_name>", methods=["GET"])
def api_get_template_details(template_name):
    if not template_name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Template not found."}), 404

    devices_meta = {}
    logs_by_command = {}
    for hostname_dir in sorted(target.iterdir()):
        if not hostname_dir.is_dir():
            continue
        logs_manifest = hostname_dir / "logs.json"
        commands = []
        if logs_manifest.exists():
            try:
                with open(logs_manifest, "r") as handle:
                    manifest = json.load(handle) or {}
                for name in manifest.get("logs", []):
                    base = os.path.splitext(name)[0]
                    cmd = base.replace("_", " ")
                    commands.append(cmd)
                    logs_by_command.setdefault(hostname_dir.name, {})[cmd] = name
            except Exception:
                commands = []
        if commands:
            devices_meta[hostname_dir.name] = commands

    return jsonify({
        "status": "ok",
        "template": template_name,
        "devices_meta": devices_meta,
        "logs_by_command": logs_by_command,
    })


@app.route("/api/admin/templates", methods=["DELETE"])
def api_admin_delete_templates():
    data = request.get_json() or {}
    name = data.get("name")
    delete_all = bool(data.get("all"))

    if delete_all:
        for entry in TEMPLATES_DIR.iterdir():
            if entry.is_dir():
                try:
                    shutil.rmtree(entry)
                except Exception:
                    pass
        return jsonify({"status": "ok", "message": "All templates deleted"})

    if not name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / name)
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Template not found."}), 404

    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Template '{name}' deleted"})


@app.route("/api/admin/results", methods=["GET"])
def api_admin_list_results():
    results = []
    docs_path = Path.home() / "Documents"
    if docs_path.exists():
        for exam_dir in docs_path.iterdir():
            if not exam_dir.is_dir() or exam_dir.name.startswith("."):
                continue
            for session_dir in exam_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for student_dir in session_dir.iterdir():
                    if not student_dir.is_dir():
                        continue
                    results_dir = student_dir / "results"
                    if results_dir.is_dir():
                        results.append(
                            {
                                "path": str(results_dir),
                                "exam_name": exam_dir.name,
                                "session_id": session_dir.name,
                                "student_id": student_dir.name,
                                "display": f"{exam_dir.name}/{session_dir.name}/{student_dir.name}",
                            }
                        )
    return jsonify({"status": "ok", "results": results})


@app.route("/api/admin/results", methods=["DELETE"])
def api_admin_delete_results():
    data = request.get_json() or {}
    path = data.get("path")
    delete_all = bool(data.get("all"))

    if delete_all:
        docs_dir = (Path.home() / "Documents").resolve()
        deleted = 0
        if docs_dir.exists():
            for exam_dir in docs_dir.iterdir():
                if not exam_dir.is_dir() or exam_dir.name.startswith("."):
                    continue
                for session_dir in exam_dir.iterdir():
                    if not session_dir.is_dir():
                        continue
                    for student_dir in session_dir.iterdir():
                        if not student_dir.is_dir():
                            continue
                        results_dir = student_dir / "results"
                        if results_dir.is_dir():
                            try:
                                shutil.rmtree(results_dir)
                                deleted += 1
                            except Exception:
                                pass
        return jsonify({"status": "ok", "message": f"All results deleted ({deleted})."})

    if not path:
        return jsonify({"status": "error", "message": "Missing path."}), 400

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, Path(path))
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Result not found."}), 404

    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Results deleted: {target}"})


@app.route("/api/admin/students", methods=["GET"])
def api_admin_list_students():
    return jsonify(
        {
            "status": "ok",
            "students": _list_existing_directories(),
            "sessions": _list_existing_sessions(),
        }
    )


@app.route("/api/admin/students", methods=["DELETE"])
def api_admin_delete_students():
    data = request.get_json() or {}
    path = data.get("path")
    if not path:
        return jsonify({"status": "error", "message": "Missing path."}), 400

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, Path(path))
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Path not found."}), 404

    if target == docs_dir:
        return jsonify(
            {"status": "error", "message": "Refusing to delete Documents root."}
        ), 400

    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Deleted {target}"})


@app.route("/api/add_student", methods=["POST"])
def api_add_student():
    data = request.get_json() or {}
    session_path = _expand_path(data.get("session_path"))
    student_id = (data.get("student_id") or "").strip()

    if not session_path or not student_id:
        return jsonify({"status": "error", "message": "Missing session_path or student_id."}), 400

    session_dir = Path(session_path)
    if not session_dir.exists() or not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, session_dir)
    if not target:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_dir = session_dir / student_id
    student_dir.mkdir(parents=True, exist_ok=True)

    parts = student_dir.parts
    exam_name = parts[-3] if len(parts) >= 3 else ""
    session_id = parts[-2] if len(parts) >= 2 else ""
    return jsonify(
        {
            "status": "ok",
            "message": f"Student directory created: {student_dir}",
            "path": str(student_dir),
            "exam_name": exam_name,
            "session_id": session_id,
            "student_id": student_id,
        }
    )

# --- Grading Logic ---

def _substitute_variables(pattern, variables):
    """
    Replace {{key}} in pattern with value from variables.
    """
    for key, val in variables.items():
        # strict replacement of {{key}}
        pattern = pattern.replace(f"{{{{{key}}}}}", str(val))
    return pattern

def _check_criteria(content, criteria, variables):
    """
    Check if content matches the criteria pattern.
    """
    pattern = criteria.get("pattern", "")
    # Substitute variables
    final_pattern = _substitute_variables(pattern, variables)
    
    # Try Regex search
    try:
        if re.search(final_pattern, content, re.MULTILINE | re.IGNORECASE):
            return True, final_pattern
    except re.error:
        pass
        
    # Check for simple string inclusion if regex fails or is simple
    if final_pattern in content:
        return True, final_pattern
        
    return False, final_pattern
from comparsion_engine.compare_main import grading_pipeline

def _load_template_configs(template_name: str):
    template_dir = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not template_dir or not template_dir.is_dir():
        return None

    template_configs = {}
    for host_dir in sorted(template_dir.iterdir()):
        if not host_dir.is_dir():
            continue
        config_path = host_dir / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r") as handle:
                data = json.load(handle) or {}
            template_configs[host_dir.name] = normalize_parsed_config(data)
        except Exception:
            continue

    return template_configs


def _grade_session_from_config(target_path: str, template_name: str):
    template_configs = _load_template_configs(template_name)
    if not template_configs:
        return [], f"No template configs found for '{template_name}'."

    results_summary = []
    target = Path(target_path)
    if not target.is_dir():
        return [], f"Target path {target_path} not found."

    for student_entry in sorted(target.iterdir()):
        if not student_entry.is_dir():
            continue
        student_id = student_entry.name
        student_results_dir_student = student_entry / "results"
        student_results_dir_student.mkdir(parents=True, exist_ok=True)

        summary = {
            "student_id": student_id,
            "template_name": template_name,
            "grading_mode": "strict",
            "hostnames_compared": [],
            "hostnames_missing_template": [],
            "hostnames_missing_show_run": [],
            "results": {},
        }

        for hostname, template_config in template_configs.items():
            template_config = normalize_parsed_config(template_config)
            student_host_dir = student_entry / hostname
            student_config_path = student_host_dir / "config.json"
            student_config = {}
            if student_config_path.exists():
                try:
                    with open(student_config_path, "r") as handle:
                        student_config = json.load(handle) or {}
                except Exception:
                    student_config = {}
            student_config = normalize_parsed_config(student_config)

            show_run_file = None
            if student_host_dir.is_dir():
                show_run_file = find_show_run_file(str(student_host_dir))
            if not show_run_file:
                summary["hostnames_missing_show_run"].append(hostname)

            results = compare_dicts(template_config, student_config)
            summary["hostnames_compared"].append(hostname)
            summary["results"][hostname] = results

            parsed_file = student_results_dir_student / f"{hostname}_student_parsed.json"
            with open(parsed_file, "w") as handle:
                json.dump(student_config, handle, indent=4)

            result_payload = {
                "student_id": student_id,
                "template_name": template_name,
                "grading_mode": "strict",
                "hostname": hostname,
                "student_show_run_file": show_run_file,
                "student_config_file": str(student_config_path) if student_config_path.exists() else None,
                "student_parsed_file": str(parsed_file),
                "results": results,
            }

            student_result_file = student_results_dir_student / f"{hostname}_result.json"
            with open(student_result_file, "w") as handle:
                json.dump(result_payload, handle, indent=4)

        summary_file_student = student_results_dir_student / "summary.json"
        with open(summary_file_student, "w") as handle:
            json.dump(summary, handle, indent=4)

        results_summary.append(
            {"student_id": student_id, "status": "Graded", "template": template_name}
        )

    return results_summary, "Grading completed."


@app.route("/api/grade", methods=["POST"])
def api_run_grading():
    data = request.get_json() or {}
    exam_name = data.get("exam_name")
    session_id = data.get("session_id")
    target_path = data.get("target_path")
    template_name = data.get("template_name")
    include_reports = bool(data.get("include_reports"))
    
    if not all([exam_name, session_id, target_path]):
         return jsonify({"status": "error", "message": "Missing arguments"}), 400
         
    try:
        # Determine template to use
        available_templates = []
        if TEMPLATES_DIR.is_dir():
            available_templates = [p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()]

        chosen_template = template_name
        if not chosen_template:
            if len(available_templates) == 1:
                chosen_template = available_templates[0]
            else:
                return jsonify({
                    "status": "error",
                    "message": "Multiple templates available. Please select a template.",
                    "templates": available_templates,
                }), 400

        summary_results, message = _grade_session_from_config(target_path, chosen_template)

        payload = {
            "status": "success",
            "message": message,
            "results": summary_results,
        }
        if include_reports:
            payload["reports"] = _build_session_reports(target_path)
            payload["policy"] = load_grading_policy()
        
        return jsonify(payload)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Grading failed: {str(e)}"}), 500

# -------------------------------------------------
# ✅ Template Upload
# -------------------------------------------------
@app.route("/api/templates/upload", methods=["POST"])
def api_upload_templates():
    """
    Handles form-data upload from device_setup.html.
    Creates template config.json using parsing logic.
    """
    from comparison_wrapper import handle_template_upload
    
    form_data = request.form
    files = request.files

    print(f"\n[API][templates/upload] Uploading new template...")
    # Base dir for templates
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    try:
        results = handle_template_upload(files, form_data, base_dir)
        if results.get("status") == "error":
            return jsonify(results), 400
        print(f"[API][templates/upload] Extraction successful: {results}")
        return jsonify({"status": "success", "results": results})
    except Exception as e:
        print(f"[API][templates/upload] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# -------------------------------------------------
# ✅ Run Flask
# -------------------------------------------------
if __name__ == "__main__":
    print("[*] Running Flask server on http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, threaded=True)
