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
import logging
import string

# Reuse your helpers
from file_utils import save_output_to_file, del_partial_logs
from serial_utils import (
    connect_to_serial,
    READ_TIMEOUT,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    detect_hostname_with_prompt_retry,
    wait_serial_prompt_ready,
)
from remote_utils import (
    remote_connect,
    disable_paging_remote,
    enter_enable_mode_remote,
    send_command_remote,
    get_hostname_remote,
)
from command_manager import load_commands, save_commands
from comparison_engine.parser import parse_device_logs, normalize_parsed_config
from comparison_engine.comparator import compare_dicts
from comparison_engine.student_manager import find_show_run_file
from cisco_reset import reload_cisco_device
from grading_dedup import (
    load_dedup_config,
    reset_dedup_config,
    save_dedup_config,
)
from grading_rules import (
    load_grading_policy,
    load_rubric_rules,
    reset_rubric_rules,
    save_grading_policy,
    save_rubric_rules,
)
from export_melbourne import export_to_melbourne
from results_service import (
    _build_session_reports,
    _canonical_cli_command,
    _command_hint_for_feature,
    _extract_error_context,
    _extract_raw_excerpt,
    _find_log_file,
    _load_json_file,
    _normalize_text,
    _raw_log_map,
    _render_combined_raw_logs,
    _safe_resolve_child,
    _write_session_readable_results,
)
from template_service import (
    delete_templates,
    get_template_details,
    handle_upload,
    import_template_logs_folder,
    list_templates,
    load_template_configs,
    save_template_structure,
    template_has_baseline,
)

app = Flask(__name__)


@app.after_request
def add_local_app_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response

# Base directory for consistent absolute paths
BASE_DIR = Path(__file__).resolve().parent

# Grading Directories
SCHEMES_DIR = BASE_DIR / "schemes"
RUBRICS_DIR = BASE_DIR / "rubrics"
TEMPLATES_DIR = BASE_DIR / "comparison_engine" / "templates"
ENGINE_STUDENTS_DIR = BASE_DIR / "comparison_engine" / "students"
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
RESULTS_DIR = None
DOCS_DIR = (Path.home() / "Documents").resolve()
WINDOWS_DRIVES_ROOT = "__WINDOWS_DRIVES__"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
ENGINE_STUDENTS_DIR.mkdir(parents=True, exist_ok=True)

WINDOWS_INVALID_SEGMENT_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

connection_lock = threading.Lock()

current_mode = None  # "serial" or "ssh"
serial_conn = None
serial_hostname = None
last_used_serial_settings = {"port": "/dev/ttyUSB0", "baudrate": 9600}

ssh_client = None
ssh_hostname = None
last_used_ssh_credentials = {
    "host": None,
    "username": None,
    "password": None,
    "port": 22,
}

execution_abort = threading.Event()


def _is_windows_platform():
    return os.name == "nt"


def _normalize_directory_segment(value, field_label):
    segment = str(value or "").strip()
    if not segment:
        raise ValueError(f"Missing {field_label}.")
    if segment in {".", ".."}:
        raise ValueError(f"{field_label} cannot be '.' or '..'.")
    if "/" in segment or "\\" in segment:
        raise ValueError(f"{field_label} cannot contain path separators.")
    if "\x00" in segment:
        raise ValueError(f"{field_label} contains an invalid null character.")
    if not _is_windows_platform():
        return segment

    cleaned = "".join(
        "-" if ch in WINDOWS_INVALID_SEGMENT_CHARS else ch for ch in segment
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(" .")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        raise ValueError(
            f"{field_label} cannot be empty after Windows-safe cleanup."
        )

    reserved_name = cleaned.split(".")[0].upper()
    if reserved_name in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned


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


def _hostname_matches_target(expected, actual):
    expected_name = str(expected or "").strip().upper()
    actual_name = str(actual or "").strip().upper()
    if not expected_name or not actual_name:
        return True
    return expected_name == actual_name


def _engine_student_logs_dir(
    classroom, tutor_name, time_slot, student_id, hostname=None
):
    safe_classroom = str(classroom or "").strip()
    safe_tutor = str(tutor_name or "").strip()
    safe_time = str(time_slot or "").strip()
    safe_student = str(student_id or "").strip()
    if not all([safe_classroom, safe_tutor, safe_time, safe_student]):
        return None
    if safe_student.lower() in {"sample", "unknown"}:
        return None
    target_dir = (
        ENGINE_STUDENTS_DIR / safe_classroom / safe_tutor / safe_time / safe_student
    )
    if hostname:
        target_dir = target_dir / str(hostname).strip()
    return target_dir


def _delete_engine_student_logs_for_docs_target(target):
    try:
        relative = target.resolve().relative_to(DOCS_DIR)
    except Exception:
        return

    if len(relative.parts) < 1:
        return

    mirror_target = ENGINE_STUDENTS_DIR.joinpath(*relative.parts)
    if mirror_target.exists():
        shutil.rmtree(mirror_target)


def _session_student_names_path(session_dir: Path) -> Path:
    return session_dir / "students.json"


def _load_session_student_names(session_dir: Path) -> dict:
    path = _session_student_names_path(session_dir)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(k).strip()}
    except Exception:
        return {}
    return {}


def _save_session_student_names(session_dir: Path, names: dict):
    path = _session_student_names_path(session_dir)
    cleaned = {
        str(k): str(v)
        for k, v in (names or {}).items()
        if str(k).strip() and str(v).strip()
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)


def _safe_is_visible_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not path.name.startswith(".")
    except (OSError, PermissionError):
        return False


def _safe_iterdir(path: Path):
    try:
        return list(path.iterdir())
    except (OSError, PermissionError):
        return []


def _save_output_to_engine_students(
    command, output, classroom, tutor_name, time_slot, student_id, hostname
):
    """
    Save command output under
    comparison_engine/students/<classroom>/<tutor_name>/<time_slot>/<student_id>/<hostname>/.
    Only stores command logs (no config.json).
    """
    if not hostname:
        return None
    target_dir = _engine_student_logs_dir(
        classroom, tutor_name, time_slot, student_id, hostname
    )
    if target_dir is None:
        return None
    safe_command = command.replace(" ", "_").replace("/", "_")
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{safe_command}.txt"
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(output)
    return str(file_path)


# -------------------------------------------------
# ✅ Directory Endpoints
# -------------------------------------------------
def _validate_directory_payload(data):
    classroom = (
        data.get("classroom") or data.get("examName") or data.get("exam_name") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name")
        or data.get("tutorName")
        or data.get("sessionId")
        or data.get("session_id")
        or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    student_id = (data.get("studentId") or data.get("student_id") or "").strip()

    if not all([classroom, tutor_name, time_slot, student_id]):
        return (
            None,
            jsonify(
                {
                    "status": "error",
                    "message": "Missing classroom/tutor_name/time_slot/studentId",
                }
            ),
            400,
        )

    try:
        classroom = _normalize_directory_segment(classroom, "Classroom")
        tutor_name = _normalize_directory_segment(tutor_name, "Tutor name")
        time_slot = _normalize_directory_segment(time_slot, "Time slot")
        student_id = _normalize_directory_segment(student_id, "Student ID")
    except ValueError as exc:
        return None, jsonify({"status": "error", "message": str(exc)}), 400

    return (classroom, tutor_name, time_slot, student_id), None, None


@app.route("/api/create_directory", methods=["POST"])
def api_create_directory():
    """
    Create the standard directory hierarchy for a student.
    """
    data = request.get_json() or {}
    validated, error_resp, status = _validate_directory_payload(data)
    if error_resp:
        return error_resp, status

    classroom, tutor_name, time_slot, student_id = validated
    student_name = (data.get("studentName") or data.get("student_name") or "").strip()
    base_path = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    if student_name:
        session_dir = Path.home() / "Documents" / classroom / tutor_name / time_slot
        names = _load_session_student_names(session_dir)
        names[student_id] = student_name
        _save_session_student_names(session_dir, names)
    return jsonify(
        {
            "status": "ok",
            "message": f"Directory ready: {base_path}",
            "path": base_path,
            "classroom": classroom,
            "tutor_name": tutor_name,
            "time_slot": time_slot,
            # Backward-compatible response keys
            "exam_name": classroom,
            "session_id": tutor_name,
            "student_id": student_id,
            "student_name": student_name,
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
        if len(parts) >= 4:
            classroom, tutor_name, time_slot, student_id = (
                parts[-4],
                parts[-3],
                parts[-2],
                parts[-1],
            )
        else:
            classroom = (
                data.get("classroom") or data.get("examName") or data.get("exam_name")
            )
            tutor_name = (
                data.get("tutor_name")
                or data.get("tutorName")
                or data.get("sessionId")
                or data.get("session_id")
            )
            time_slot = data.get("time_slot") or data.get("timeSlot")
            student_id = data.get("studentId") or data.get("student_id")
        return jsonify(
            {
                "status": "ok",
                "message": f"Using existing directory: {existing_path}",
                "path": existing_path,
                "classroom": classroom,
                "tutor_name": tutor_name,
                "time_slot": time_slot,
                # Backward-compatible response keys
                "exam_name": classroom,
                "session_id": tutor_name,
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

    for classroom_dir in _safe_iterdir(docs_path):
        if not _safe_is_visible_dir(classroom_dir):
            continue
        for tutor_dir in _safe_iterdir(classroom_dir):
            if not _safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in _safe_iterdir(tutor_dir):
                if not _safe_is_visible_dir(time_dir):
                    continue
                student_names = _load_session_student_names(time_dir)
                for student_dir in _safe_iterdir(time_dir):
                    if not _safe_is_visible_dir(student_dir):
                        continue
                    results.append(
                        {
                            "path": str(student_dir),
                            "classroom": classroom_dir.name,
                            "tutor_name": tutor_dir.name,
                            "time_slot": time_dir.name,
                            # Backward-compatible keys
                            "exam_name": classroom_dir.name,
                            "session_id": tutor_dir.name,
                            "student_id": student_dir.name,
                            "student_name": student_names.get(student_dir.name, ""),
                            "display": (
                                f"{classroom_dir.name}/{tutor_dir.name}/"
                                f"{time_dir.name}/{student_dir.name}"
                            ),
                        }
                    )
    return sorted(results, key=lambda x: x["display"])


def _list_existing_sessions():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for classroom_dir in _safe_iterdir(docs_path):
        if not _safe_is_visible_dir(classroom_dir):
            continue
        for tutor_dir in _safe_iterdir(classroom_dir):
            if not _safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in _safe_iterdir(tutor_dir):
                if not _safe_is_visible_dir(time_dir):
                    continue
                results.append(
                    {
                        "path": str(time_dir),
                        "classroom": classroom_dir.name,
                        "tutor_name": tutor_dir.name,
                        "time_slot": time_dir.name,
                        "exam_name": classroom_dir.name,
                        "session_id": tutor_dir.name,
                        "display": f"{classroom_dir.name}/{tutor_dir.name}/{time_dir.name}",
                    }
                )
    return sorted(results, key=lambda x: x["display"])


def _list_existing_exams():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for classroom_dir in _safe_iterdir(docs_path):
        if not _safe_is_visible_dir(classroom_dir):
            continue
        # Only include dirs that contain at least one tutor/time subdirectory
        has_session = any(
            _safe_is_visible_dir(d) for d in _safe_iterdir(classroom_dir)
        )
        if has_session:
            results.append(
                {
                    "path": str(classroom_dir),
                    "classroom": classroom_dir.name,
                    "exam_name": classroom_dir.name,
                    "display": classroom_dir.name,
                }
            )
    return sorted(results, key=lambda x: x["display"])


def _is_windows_drives_root(path_val):
    return os.name == "nt" and str(path_val or "") == WINDOWS_DRIVES_ROOT


def _list_windows_drive_roots():
    drives = []
    if os.name != "nt":
        return drives

    for letter in string.ascii_uppercase:
        drive_path = f"{letter}:\\"
        if os.path.exists(drive_path):
            drives.append(
                {
                    "name": f"{letter}:",
                    "path": drive_path,
                    "is_drive": True,
                }
            )
    return drives


def _resolve_picker_path(path_val, fallback):
    if _is_windows_drives_root(path_val):
        return WINDOWS_DRIVES_ROOT
    if path_val:
        return Path(_expand_path(path_val)).resolve()
    return fallback


@app.route("/api/directories", methods=["GET"])
def api_list_directories():
    path_val = request.args.get("path")
    docs_path = (Path.home() / "Documents").resolve()

    # If a path is provided, use it as the "current" one, otherwise default to ~/Documents
    try:
        current = _resolve_picker_path(path_val, docs_path)
    except Exception:
        current = docs_path

    # Only return the managed "directories" list if we are explicitly at the managed root.
    # Otherwise, we want the frontend to fall back to 'loadSubfolders' to show the actual directory contents.
    directories = []
    if current == docs_path:
        directories = _list_existing_directories()

    if current == WINDOWS_DRIVES_ROOT:
        parent_path = WINDOWS_DRIVES_ROOT
    else:
        parent_path = str(current.parent)
        if os.name == "nt" and current.anchor:
            try:
                if current.resolve() == Path(current.anchor).resolve():
                    parent_path = WINDOWS_DRIVES_ROOT
            except Exception:
                if str(current) == current.anchor:
                    parent_path = WINDOWS_DRIVES_ROOT

    return jsonify(
        {
            "status": "ok",
            "directories": directories,
            "current_path": str(current),
            "parent_path": parent_path,
        }
    )


@app.route("/api/subfolders", methods=["GET"])
def api_list_subfolders():
    path_val = request.args.get("path")

    if _is_windows_drives_root(path_val):
        return jsonify(
            {
                "status": "ok",
                "subfolders": _list_windows_drive_roots(),
                "current_path": WINDOWS_DRIVES_ROOT,
                "parent_path": WINDOWS_DRIVES_ROOT,
            }
        )

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
        for item in _safe_iterdir(target):
            if _safe_is_visible_dir(item):
                subfolders.append({"name": item.name, "path": str(item)})
        subfolders.sort(key=lambda x: x["name"].lower())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    parent_path = str(target.parent)
    if os.name == "nt" and target.anchor:
        try:
            if target.resolve() == Path(target.anchor).resolve():
                parent_path = WINDOWS_DRIVES_ROOT
        except Exception:
            if str(target) == target.anchor:
                parent_path = WINDOWS_DRIVES_ROOT

    return jsonify(
        {
            "status": "ok",
            "subfolders": subfolders,
            "current_path": str(target),
            "parent_path": parent_path,
        }
    )


@app.route("/api/directories/bulk", methods=["POST"])
def api_bulk_directories():
    data = request.get_json() or {}
    classroom = (
        data.get("classroom") or data.get("examName") or data.get("exam_name") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name")
        or data.get("tutorName")
        or data.get("sessionId")
        or data.get("session_id")
        or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    students = data.get("students") or []

    if not classroom or not tutor_name or not time_slot or not students:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing classroom/tutor_name/time_slot/students for bulk creation.",
                }
            ),
            400,
        )

    try:
        classroom = _normalize_directory_segment(classroom, "Classroom")
        tutor_name = _normalize_directory_segment(tutor_name, "Tutor name")
        time_slot = _normalize_directory_segment(time_slot, "Time slot")
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    created = []
    base_docs_path = Path.home() / "Documents"
    session_dir = base_docs_path / classroom / tutor_name / time_slot
    session_dir.mkdir(parents=True, exist_ok=True)
    student_names = _load_session_student_names(session_dir)

    for student in students:
        student_id = (student.get("id") or "").strip()
        student_name = (student.get("name") or "").strip()
        if not student_id:
            continue
        try:
            student_id = _normalize_directory_segment(student_id, "Student ID")
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        student_dir = session_dir / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        if student_name:
            student_names[student_id] = student_name
        created.append(
            {
                "path": str(student_dir),
                "classroom": classroom,
                "tutor_name": tutor_name,
                "time_slot": time_slot,
                "exam_name": classroom,
                "session_id": tutor_name,
                "student_id": student_id,
                "student_name": student_name,
                "display": f"{classroom}/{tutor_name}/{time_slot}/{student_id}",
            }
        )

    _save_session_student_names(session_dir, student_names)

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
                queue.put(("progress", "Waking console and detecting hostname..."))
                hostname = detect_hostname_with_prompt_retry(
                    ser, fallback="device", attempts=2
                )

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
        raw_port = (
            ssh.get("port")
            or data.get("port")
            or last_used_ssh_credentials.get("port", 22)
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

        print(f"[API][connect][ssh] Connecting to {host}:{port_value} ...", flush=True)
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


@app.route("/api/reset_device", methods=["POST"])
def api_reset_device():
    execution_abort.clear()
    data = request.get_json() or {}
    mode = (data.get("mode") or data.get("connection") or "serial").lower()
    device_type = str(data.get("device_type") or "switch").strip().lower()
    if mode != "serial":
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Device reset is only supported over serial.",
                }
            ),
            400,
        )

    serial_payload = data.get("serial") or {}
    with connection_lock:
        stored_port = last_used_serial_settings.get("port")
        stored_baud = last_used_serial_settings.get("baudrate", 9600)
    port = (
        serial_payload.get("port") or data.get("port") or stored_port or "/dev/ttyUSB0"
    )
    raw_baud = (
        serial_payload.get("baudrate")
        or serial_payload.get("baud")
        or data.get("baudrate")
        or stored_baud
        or 9600
    )
    try:
        baudrate = int(raw_baud)
    except (TypeError, ValueError):
        baudrate = 9600

    if not port:
        return (
            jsonify({"status": "error", "message": "No serial port configured."}),
            400,
        )

    _close_serial_connection()
    _close_ssh_connection()

    result = reload_cisco_device(
        port=port,
        baudrate=baudrate,
        delete_vlan_database=(device_type != "router"),
        abort_event=execution_abort,
    )
    logs = result.get("logs") or []
    message = result.get("message") or "Reset completed."
    if result.get("aborted"):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": message,
                    "logs": logs,
                    "aborted": True,
                    "port": port,
                    "baudrate": baudrate,
                    "device_type": device_type,
                }
            ),
            499,
        )
    if result.get("success"):
        return jsonify(
            {
                "status": "ok",
                "message": message,
                "logs": logs,
                "port": port,
                "baudrate": baudrate,
                "device_type": device_type,
            }
        )
    return (
        jsonify(
            {
                "status": "error",
                "message": message,
                "logs": logs,
                "port": port,
                "baudrate": baudrate,
            }
        ),
        500,
    )


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
    classroom = (
        data.get("classroom") or data.get("exam_name") or data.get("examName") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name") or data.get("session_id") or data.get("sessionId") or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    student_id = data.get("student_id")
    filename = data.get("filename", "log.txt")
    content = data.get("content", "")

    if not (classroom and tutor_name and time_slot and student_id):
        return jsonify({"status": "error", "message": "Missing directory info"}), 400

    base_dir = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
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
    classroom = data.get("classroom") or data.get("exam_name") or data.get("examName")
    tutor_name = (
        data.get("tutor_name") or data.get("session_id") or data.get("sessionId")
    )
    time_slot = data.get("time_slot") or data.get("timeSlot")
    student_id = data.get("student_id")

    if mode == "existing":
        if not log_dir:
            raise ValueError("Missing log_dir for existing directory mode.")
        expanded = _expand_path(log_dir)
        if not expanded or not os.path.exists(expanded):
            raise FileNotFoundError(f"Existing directory not found: {log_dir}")
        return expanded, classroom, tutor_name, time_slot, student_id

    if not all([classroom, tutor_name, time_slot, student_id]):
        raise ValueError(
            "Missing classroom/tutor/time/student details for directory creation."
        )

    classroom = _normalize_directory_segment(classroom, "Classroom")
    tutor_name = _normalize_directory_segment(tutor_name, "Tutor name")
    time_slot = _normalize_directory_segment(time_slot, "Time slot")
    student_id = _normalize_directory_segment(student_id, "Student ID")

    base_path = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    return base_path, classroom, tutor_name, time_slot, student_id


@app.route("/api/abort", methods=["POST"])
def api_abort():
    """Signal the running execution to stop immediately."""
    execution_abort.set()
    # Close connections to force any blocking read to fail
    _close_serial_connection()
    _close_ssh_connection()
    return jsonify({"status": "ok", "message": "Abort signal sent."})


@app.route("/api/execute", methods=["POST"])
def api_execute():
    execution_abort.clear()
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
        base_path, classroom, tutor_name, time_slot, student_id = _ensure_base_path(
            data
        )
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    def generate():
        hostname = None
        files_written = []
        skip_config = bool(data.get("skip_config"))
        skip_hostname_check = bool(data.get("skip_hostname_check"))

        def run_serial():
            global current_mode
            nonlocal hostname
            serial_payload = data.get("serial") or {}
            with connection_lock:
                stored_port = last_used_serial_settings.get("port")
                stored_baud = last_used_serial_settings.get("baudrate", 9600)
                existing_ser = (
                    serial_conn if serial_conn and serial_conn.is_open else None
                )
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
                    abort_event=execution_abort,
                )
            except Exception as exc:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: {exc}",
                    }
                )
                return False
            if not ser:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: device not responding.",
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
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Waking console and detecting hostname...",
                        "progress_pct": 0,
                    }
                )
                hostname = detect_hostname_with_prompt_retry(
                    ser, fallback="device", attempts=2
                )
            except Exception as exc:
                logout_close_connection(ser)
                yield stream_json_line(
                    {"type": "error", "msg": f"Serial initialization failed: {exc}"}
                )
                return False
            if target_device and not _hostname_matches_target(target_device, hostname):
                if skip_hostname_check:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                        }
                    )
                else:
                    logout_close_connection(ser)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "error_code": "HOSTNAME_MISMATCH",
                            "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                        }
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
            try:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Waiting for device prompt before command run...",
                    }
                )
                wait_serial_prompt_ready(local_ser, timeout=6)
            except Exception as exc:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Prompt wake warning before commands: {exc}. Continuing...",
                    }
                )
            for cmd in commands:
                cli_cmd = _canonical_cli_command(cmd)
                yield stream_json_line(
                    {"type": "progress", "msg": f"Running '{cli_cmd}'..."}
                )
                try:
                    output = send_command(local_ser, cli_cmd, timeout=30)
                    yield stream_json_line(
                        {
                            "type": "raw_output",
                            "msg": f"{hostname}# {cli_cmd}\n{output}"
                        }
                    )
                    file_path = save_output_to_file(
                        cli_cmd,
                        output,
                        classroom=classroom,
                        tutor_name=tutor_name,
                        time_slot=time_slot,
                        student_id=student_id,
                        hostname=target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cli_cmd,
                        output,
                        classroom,
                        tutor_name,
                        time_slot,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = (
                        round((completed / total_commands) * 100)
                        if total_commands
                        else 100
                    )
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cli_cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, target_device or hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cli_cmd}' failed: {exc}",
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
                active_client = (
                    ssh_client if _is_ssh_client_active(ssh_client) else None
                )
                cached_host = last_used_ssh_credentials.get("host")
                cached_user = last_used_ssh_credentials.get("username")
                cached_port = last_used_ssh_credentials.get("port")
                stored_hostname = ssh_hostname or ssh_payload.get("host")
            host = ssh_payload.get("host") or cached_host
            username = ssh_payload.get("username") or cached_user
            password = ssh_payload.get("password") or last_used_ssh_credentials.get(
                "password"
            )
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
                    yield stream_json_line(
                        {"type": "error", "msg": "SSH connection failed."}
                    )
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
                if target_device and not _hostname_matches_target(
                    target_device, hostname
                ):
                    if skip_hostname_check:
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                            }
                        )
                    else:
                        try:
                            existing_shell = getattr(client, "_shell", None)
                            if existing_shell:
                                existing_shell.close()
                        except Exception:
                            pass
                        try:
                            client.close()
                        except Exception:
                            pass
                        yield stream_json_line(
                            {
                                "type": "error",
                                "error_code": "HOSTNAME_MISMATCH",
                                "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                            }
                        )
                        return False
                    _update_ssh_state(
                        client, host, username, password, hostname, resolved_port
                    )

            if not reuse:
                if target_device and not _hostname_matches_target(
                    target_device, hostname
                ):
                    if skip_hostname_check:
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                            }
                        )
                    else:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "error_code": "HOSTNAME_MISMATCH",
                                "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                            }
                        )
                        return False
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
                cli_cmd = _canonical_cli_command(cmd)
                yield stream_json_line(
                    {"type": "progress", "msg": f"Running '{cli_cmd}'..."}
                )
                try:
                    output = send_command_remote(active, cli_cmd, timeout=30)
                    yield stream_json_line(
                        {
                            "type": "raw_output",
                            "msg": f"{hostname}# {cli_cmd}\n{output}"
                        }
                    )
                    file_path = save_output_to_file(
                        cli_cmd,
                        output,
                        classroom=classroom,
                        tutor_name=tutor_name,
                        time_slot=time_slot,
                        student_id=student_id,
                        hostname=target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cli_cmd,
                        output,
                        classroom,
                        tutor_name,
                        time_slot,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = (
                        round((completed / total_commands) * 100)
                        if total_commands
                        else 100
                    )
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cli_cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, target_device or hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cli_cmd}' failed: {exc}",
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
                    "hostname": hostname,
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
            cleanup_hostname = target_device or hostname
            if cleanup_hostname:
                del_partial_logs(base_path, cleanup_hostname)
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
    directory.mkdir(parents=True, exist_ok=True)
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

    major_threshold = data.get("major_threshold", policy.get("major_threshold"))
    minor_threshold = data.get("minor_threshold", policy.get("minor_threshold"))

    try:
        major_threshold = int(major_threshold)
        minor_threshold = int(minor_threshold)
    except Exception:
        return (
            jsonify({"status": "error", "message": "Thresholds must be integers."}),
            400,
        )

    if major_threshold < 1 or minor_threshold < 1:
        return (
            jsonify({"status": "error", "message": "Thresholds must be at least 1."}),
            400,
        )

    policy = save_grading_policy(
        {
            "major_threshold": major_threshold,
            "minor_threshold": minor_threshold,
        }
    )
    return jsonify({"status": "ok", "policy": policy})


@app.route("/api/rubric_rules", methods=["GET"])
def api_get_rubric_rules():
    return jsonify({"status": "ok", "rules": load_rubric_rules()})


@app.route("/api/rubric_rules", methods=["POST"])
def api_save_rubric_rules():
    data = request.get_json() or {}
    rules = data.get("rules")
    if rules is None:
        return jsonify({"status": "error", "message": "Missing rules."}), 400
    try:
        saved = save_rubric_rules(rules)
        return jsonify({"status": "ok", "rules": saved})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/rubric_rules/reset", methods=["POST"])
def api_reset_rubric_rules():
    try:
        rules = reset_rubric_rules()
        return jsonify({"status": "ok", "rules": rules})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/grading_dedup", methods=["GET"])
def api_get_grading_dedup():
    return jsonify({"status": "ok", "config": load_dedup_config()})


@app.route("/api/grading_dedup", methods=["POST"])
def api_save_grading_dedup():
    data = request.get_json() or {}
    config = data.get("config")
    if config is None:
        return jsonify({"status": "error", "message": "Missing dedup config."}), 400
    try:
        saved = save_dedup_config(config)
        return jsonify({"status": "ok", "config": saved})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.route("/api/grading_dedup/reset", methods=["POST"])
def api_reset_grading_dedup():
    try:
        config = reset_dedup_config()
        return jsonify({"status": "ok", "config": config})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


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


@app.route("/api/error_context", methods=["POST"])
def api_error_context():
    data = request.get_json() or {}
    target_path = data.get("target_path")
    student_id = (data.get("student_id") or "").strip()
    template_name = (data.get("template_name") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    feature = (data.get("feature") or "").strip()
    expected = data.get("expected")
    actual = data.get("actual")

    if not all([target_path, student_id, template_name, hostname, feature]):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing target_path, student_id, template_name, hostname, or feature.",
                }
            ),
            400,
        )

    session_dir = Path(target_path).resolve()
    if not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    safe_session_dir = _safe_resolve_child(DOCS_DIR, session_dir)
    if not safe_session_dir:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_config_path = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname / "config.json"
    )
    template_config_path = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "config.json"
    )
    student_log_dir = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname
    )
    template_log_dir = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "logs"
    )

    template_config = (
        _load_json_file(template_config_path)
        if template_config_path and template_config_path.exists()
        else {}
    )
    student_config = (
        _load_json_file(student_config_path)
        if student_config_path and student_config_path.exists()
        else {}
    )
    command_hint = _command_hint_for_feature(feature)
    template_raw_path = _find_log_file(template_log_dir, command_hint)
    student_raw_path = _find_log_file(student_log_dir, command_hint)

    context = _extract_error_context(
        template_config, student_config, feature, expected=expected, actual=actual
    )
    template_raw_excerpt = _extract_raw_excerpt(
        template_raw_path, feature, expected=expected, actual=actual
    )
    student_raw_excerpt = _extract_raw_excerpt(
        student_raw_path, feature, expected=expected, actual=actual
    )

    return jsonify(
        {
            "status": "ok",
            "feature": feature,
            "hostname": hostname,
            "student_id": student_id,
            "template_name": template_name,
            "context_path": context["context_path"],
            "highlight_key": context["highlight_key"],
            "template_context": context["template_context"],
            "student_context": context["student_context"],
            "template_config_path": (
                str(template_config_path)
                if template_config_path and template_config_path.exists()
                else None
            ),
            "student_config_path": (
                str(student_config_path)
                if student_config_path and student_config_path.exists()
                else None
            ),
            "command_hint": command_hint,
            "template_raw_path": (
                str(template_raw_path)
                if template_raw_path and template_raw_path.exists()
                else None
            ),
            "student_raw_path": (
                str(student_raw_path)
                if student_raw_path and student_raw_path.exists()
                else None
            ),
            "template_raw_excerpt": template_raw_excerpt,
            "student_raw_excerpt": student_raw_excerpt,
        }
    )


@app.route("/api/raw_log_preview", methods=["POST"])
def api_raw_log_preview():
    data = request.get_json() or {}
    target_path = data.get("target_path")
    student_id = (data.get("student_id") or "").strip()
    template_name = (data.get("template_name") or "").strip()
    hostname = (data.get("hostname") or "").strip()

    if not all([target_path, student_id, template_name, hostname]):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing target_path, student_id, template_name, or hostname.",
                }
            ),
            400,
        )

    session_dir = Path(target_path).resolve()
    if not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    safe_session_dir = _safe_resolve_child(DOCS_DIR, session_dir)
    if not safe_session_dir:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_log_dir = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname
    )
    template_log_dir = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "logs"
    )

    template_logs = _raw_log_map(template_log_dir)
    student_logs = _raw_log_map(student_log_dir)
    command_keys = sorted(
        set(template_logs) | set(student_logs),
        key=lambda key: (
            template_logs.get(key, student_logs.get(key, {})).get("command") or key
        ).lower(),
    )

    paired_logs = []
    for key in command_keys:
        template_item = template_logs.get(key)
        student_item = student_logs.get(key)
        paired_logs.append(
            {
                "command": (
                    (template_item or {}).get("command")
                    or (student_item or {}).get("command")
                    or key
                ),
                "template": template_item,
                "student": student_item,
            }
        )

    template_items = [item["template"] for item in paired_logs if item.get("template")]
    student_items = [item["student"] for item in paired_logs if item.get("student")]

    return jsonify(
        {
            "status": "ok",
            "student_id": student_id,
            "template_name": template_name,
            "hostname": hostname,
            "template_log_dir": (
                str(template_log_dir)
                if template_log_dir and template_log_dir.exists()
                else None
            ),
            "student_log_dir": (
                str(student_log_dir)
                if student_log_dir and student_log_dir.exists()
                else None
            ),
            "logs": paired_logs,
            "template_combined": _render_combined_raw_logs(template_items, "Template"),
            "student_combined": _render_combined_raw_logs(student_items, "Student"),
        }
    )


@app.route("/api/melbourne/send", methods=["POST"])
def api_melbourne_send():
    try:
        payload = request.get_json(silent=True) or {}
        result = export_to_melbourne(payload)
        return jsonify({"status": "ok", **result})
    except Exception as exc:
        logging.exception("Melbourne export failed")
        return jsonify({"status": "error", "message": str(exc)}), 400


# -------------------------------------------------
# ✅ Admin Cleanup
# -------------------------------------------------
@app.route("/api/admin/templates", methods=["GET"])
def api_admin_list_templates():
    return jsonify({"status": "ok", "templates": list_templates()})


@app.route("/api/templates/<template_name>", methods=["GET"])
def api_get_template_details(template_name):
    try:
        return jsonify({"status": "ok", **get_template_details(template_name)})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404


@app.route("/api/templates/save_setup", methods=["POST"])
def api_save_template_setup():
    data = request.get_json() or {}
    template_name = (data.get("template_name") or "").strip()
    devices_meta = data.get("devices_meta") or {}
    source_template_name = (data.get("source_template_name") or "").strip()

    try:
        result = save_template_structure(
            template_name, devices_meta, source_template_name=source_template_name
        )
        return jsonify({"status": "ok", **result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/templates/import_logs_folder", methods=["POST"])
def api_import_template_logs_folder():
    data = request.get_json() or {}
    template_name = (data.get("template_name") or "").strip()
    source_dir = _expand_path(data.get("source_dir"))
    source_template_name = (data.get("source_template_name") or "").strip()
    strict = bool(data.get("strict"))
    devices_meta = data.get("devices_meta") or {}

    if not template_name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400
    if not source_dir or not os.path.isdir(source_dir):
        return jsonify({"status": "error", "message": "Selected logs folder was not found."}), 400
    if strict and (not isinstance(devices_meta, dict) or not devices_meta):
        return jsonify({"status": "error", "message": "Strict folder import requires template devices."}), 400

    try:
        result = import_template_logs_folder(
            template_name,
            source_dir,
            source_template_name=source_template_name,
            strict=strict,
            devices_meta=devices_meta,
        )
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify({"status": "ok", **result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/admin/templates", methods=["DELETE"])
def api_admin_delete_templates():
    data = request.get_json() or {}
    name = data.get("name")
    delete_all = bool(data.get("all"))

    try:
        message = delete_templates(name=name, delete_all=delete_all)
        return jsonify({"status": "ok", "message": message})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404


@app.route("/api/admin/results", methods=["GET"])
def api_admin_list_results():
    results = []
    docs_path = Path.home() / "Documents"
    if docs_path.exists():
        for classroom_dir in _safe_iterdir(docs_path):
            if not _safe_is_visible_dir(classroom_dir):
                continue
            for tutor_dir in _safe_iterdir(classroom_dir):
                if not _safe_is_visible_dir(tutor_dir):
                    continue
                for time_dir in _safe_iterdir(tutor_dir):
                    if not _safe_is_visible_dir(time_dir):
                        continue
                    for student_dir in _safe_iterdir(time_dir):
                        if not _safe_is_visible_dir(student_dir):
                            continue
                        results_dir = student_dir / "results"
                        if results_dir.is_dir():
                            results.append(
                                {
                                    "path": str(results_dir),
                                    "classroom": classroom_dir.name,
                                    "tutor_name": tutor_dir.name,
                                    "time_slot": time_dir.name,
                                    "exam_name": classroom_dir.name,
                                    "session_id": tutor_dir.name,
                                    "student_id": student_dir.name,
                                    "display": (
                                        f"{classroom_dir.name}/{tutor_dir.name}/"
                                        f"{time_dir.name}/{student_dir.name}"
                                    ),
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
            for classroom_dir in _safe_iterdir(docs_dir):
                if not _safe_is_visible_dir(classroom_dir):
                    continue
                for tutor_dir in _safe_iterdir(classroom_dir):
                    if not _safe_is_visible_dir(tutor_dir):
                        continue
                    for time_dir in _safe_iterdir(tutor_dir):
                        if not _safe_is_visible_dir(time_dir):
                            continue
                        for student_dir in _safe_iterdir(time_dir):
                            if not _safe_is_visible_dir(student_dir):
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
            "exams": _list_existing_exams(),
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

    target = _safe_resolve_child(DOCS_DIR, Path(path))
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Path not found."}), 404

    if target == DOCS_DIR:
        return (
            jsonify(
                {"status": "error", "message": "Refusing to delete Documents root."}
            ),
            400,
        )

    if len(target.parts) >= len(DOCS_DIR.parts) + 3:
        relative = target.relative_to(DOCS_DIR)
        if len(relative.parts) == 3:
            session_dir = DOCS_DIR / relative.parts[0] / relative.parts[1]
            names = _load_session_student_names(session_dir)
            if relative.parts[2] in names:
                names.pop(relative.parts[2], None)
                _save_session_student_names(session_dir, names)

    _delete_engine_student_logs_for_docs_target(target)
    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Deleted {target}"})


@app.route("/api/admin/sync_mirror", methods=["POST"])
def api_admin_sync_mirror():
    """Remove engine/students dirs whose corresponding Documents folders no longer exist."""
    removed = []
    if not ENGINE_STUDENTS_DIR.exists():
        return jsonify({"status": "ok", "message": "Nothing to sync.", "removed": []})

    for classroom_dir in list(ENGINE_STUDENTS_DIR.iterdir()):
        if not classroom_dir.is_dir():
            continue
        docs_classroom = DOCS_DIR / classroom_dir.name
        if not docs_classroom.exists():
            shutil.rmtree(classroom_dir)
            removed.append(classroom_dir.name)
            continue
        for tutor_dir in list(classroom_dir.iterdir()):
            if not tutor_dir.is_dir():
                continue
            docs_tutor = docs_classroom / tutor_dir.name
            if not docs_tutor.exists():
                shutil.rmtree(tutor_dir)
                removed.append(f"{classroom_dir.name}/{tutor_dir.name}")
                continue
            for time_dir in list(tutor_dir.iterdir()):
                if not time_dir.is_dir():
                    continue
                docs_time = docs_tutor / time_dir.name
                if not docs_time.exists():
                    shutil.rmtree(time_dir)
                    removed.append(
                        f"{classroom_dir.name}/{tutor_dir.name}/{time_dir.name}"
                    )
                    continue
                for student_dir in list(time_dir.iterdir()):
                    if not student_dir.is_dir():
                        continue
                    docs_student = docs_time / student_dir.name
                    if not docs_student.exists():
                        shutil.rmtree(student_dir)
                        removed.append(
                            f"{classroom_dir.name}/{tutor_dir.name}/{time_dir.name}/{student_dir.name}"
                        )

                if time_dir.exists() and not any(time_dir.iterdir()):
                    time_dir.rmdir()
                if tutor_dir.exists() and not any(tutor_dir.iterdir()):
                    tutor_dir.rmdir()
        if classroom_dir.exists() and not any(classroom_dir.iterdir()):
            classroom_dir.rmdir()

    if removed:
        msg = f"Removed {len(removed)} orphaned mirror folder(s):\n" + "\n".join(
            removed
        )
    else:
        msg = "All mirror folders are in sync. Nothing to remove."
    return jsonify({"status": "ok", "message": msg, "removed": removed})


@app.route("/api/add_student", methods=["POST"])
def api_add_student():
    data = request.get_json() or {}
    session_path = _expand_path(data.get("session_path"))
    student_id = (data.get("student_id") or "").strip()
    student_name = (data.get("student_name") or "").strip()

    if not session_path or not student_id:
        return (
            jsonify(
                {"status": "error", "message": "Missing session_path or student_id."}
            ),
            400,
        )

    try:
        student_id = _normalize_directory_segment(student_id, "Student ID")
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    session_dir = Path(session_path)
    if not session_dir.exists() or not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, session_dir)
    if not target:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_dir = session_dir / student_id
    student_dir.mkdir(parents=True, exist_ok=True)
    names = _load_session_student_names(session_dir)
    if student_name:
        names[student_id] = student_name
    existing_name = names.get(student_id, "")
    _save_session_student_names(session_dir, names)

    parts = student_dir.parts
    classroom = parts[-4] if len(parts) >= 4 else ""
    tutor_name = parts[-3] if len(parts) >= 3 else ""
    time_slot = parts[-2] if len(parts) >= 2 else ""
    return jsonify(
        {
            "status": "ok",
            "message": f"Student directory created: {student_dir}",
            "path": str(student_dir),
            "classroom": classroom,
            "tutor_name": tutor_name,
            "time_slot": time_slot,
            "exam_name": classroom,
            "session_id": tutor_name,
            "student_id": student_id,
            "student_name": student_name or existing_name,
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


from comparison_engine.compare_main import grading_pipeline


def _grade_session_from_config(target_path: str, template_name: str):
    if not template_has_baseline(template_name):
        return [], (
            f"Template '{template_name}' has device/command setup only. "
            "Upload template baseline logs before grading."
        )

    template_configs = load_template_configs(template_name)
    if not template_configs:
        return [], f"No template configs found for '{template_name}'."

    results_summary = []
    target = Path(target_path)
    if not target.is_dir():
        return [], f"Target path {target_path} not found."

    def _student_has_collected_data(student_dir: Path) -> bool:
        if not student_dir.is_dir():
            return False
        for child in student_dir.iterdir():
            if not child.is_dir() or child.name == "results":
                continue
            if (child / "config.json").exists():
                return True
            try:
                if find_show_run_file(str(child)):
                    return True
            except Exception:
                continue
        return False

    skipped_students = []

    for student_entry in sorted(target.iterdir()):
        if not student_entry.is_dir():
            continue
        student_id = student_entry.name
        if not _student_has_collected_data(student_entry):
            skipped_students.append(student_id)
            continue
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

            parsed_file = (
                student_results_dir_student / f"{hostname}_student_parsed.json"
            )
            with open(parsed_file, "w") as handle:
                json.dump(student_config, handle, indent=4)

            result_payload = {
                "student_id": student_id,
                "template_name": template_name,
                "grading_mode": "strict",
                "hostname": hostname,
                "student_show_run_file": show_run_file,
                "student_config_file": (
                    str(student_config_path) if student_config_path.exists() else None
                ),
                "student_parsed_file": str(parsed_file),
                "results": results,
            }

            student_result_file = (
                student_results_dir_student / f"{hostname}_result.json"
            )
            with open(student_result_file, "w") as handle:
                json.dump(result_payload, handle, indent=4)

        summary_file_student = student_results_dir_student / "summary.json"
        with open(summary_file_student, "w") as handle:
            json.dump(summary, handle, indent=4)

        results_summary.append(
            {"student_id": student_id, "status": "Graded", "template": template_name}
        )

    if not results_summary:
        return (
            [],
            "No collected student logs found in this session. Select a student and collect logs before grading.",
        )

    if skipped_students:
        return (
            results_summary,
            f"Grading completed for {len(results_summary)} student(s). "
            f"Skipped {len(skipped_students)} student(s) with no collected logs.",
        )

    return results_summary, "Grading completed."


@app.route("/api/grade", methods=["POST"])
def api_run_grading():
    data = request.get_json() or {}
    classroom = data.get("classroom") or data.get("exam_name")
    tutor_name = data.get("tutor_name") or data.get("session_id")
    time_slot = data.get("time_slot")
    target_path = data.get("target_path")
    template_name = data.get("template_name")
    include_reports = bool(data.get("include_reports"))

    if not target_path:
        return jsonify({"status": "error", "message": "Missing arguments"}), 400

    try:
        # Determine template to use
        available_templates = list_templates()

        chosen_template = template_name
        if not chosen_template:
            if len(available_templates) == 1:
                chosen_template = available_templates[0]
            else:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Multiple templates available. Please select a template.",
                            "templates": available_templates,
                        }
                    ),
                    400,
                )

        summary_results, message = _grade_session_from_config(
            target_path, chosen_template
        )

        if not summary_results:
            return jsonify({"status": "error", "message": message}), 400

        payload = {
            "status": "success",
            "message": message,
            "results": summary_results,
        }
        reports = _build_session_reports(target_path)
        policy = load_grading_policy()
        _write_session_readable_results(target_path, reports, policy)
        if include_reports:
            payload["reports"] = reports
            payload["policy"] = policy

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
    form_data = request.form
    files = request.files

    print(f"\n[API][templates/upload] Uploading new template...")

    try:
        results = handle_upload(files, form_data)
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
def main():

    class _SuppressDevServerWarning(logging.Filter):
        def filter(self, record):
            message = record.getMessage()
            return (
                "This is a development server. Do not use it in a production deployment."
                not in message
            )

    logging.getLogger("werkzeug").addFilter(_SuppressDevServerWarning())
    print("[*] Running Flask server on http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, threaded=True)


if __name__ == "__main__":
    main()
