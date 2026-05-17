"""
Template upload adapter for the comparison engine.

This script receives uploaded baseline logs from the Device Setup screen,
saves them into comparison_engine/templates, parses them, and writes metadata
the comparison engine can reuse later.
"""

import json
import os
import shutil
from comparison_engine.parser import detect_command_type
from comparison_engine.parser import parse_device_logs_with_report
from comparison_engine.template_manager import is_show_run_filename


def _write_template_manifest(template_dir, template_name, devices_meta, has_baseline):
    """
    Write template-level metadata describing uploaded devices and baseline state.

    server.py uses this manifest indirectly through template-management endpoints
    to know whether a template has parsed baseline configs available.
    """
    os.makedirs(template_dir, exist_ok=True)
    manifest_path = os.path.join(template_dir, "template_manifest.json")
    payload = {
        "template_name": template_name,
        "devices_meta": devices_meta or {},
        "has_baseline": bool(has_baseline),
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)


def _copy_source_template(base_dir, template_name, source_template_name):
    if not source_template_name or source_template_name == template_name:
        return
    template_dir = os.path.join(base_dir, "comparison_engine", "templates", template_name)
    source_template_dir = os.path.join(
        base_dir, "comparison_engine", "templates", source_template_name
    )
    if os.path.isdir(source_template_dir):
        shutil.copytree(source_template_dir, template_dir, dirs_exist_ok=True)


def _safe_command_name_from_file(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    command = " ".join(base.replace("_", " ").replace("-", " ").split())
    if command.lower() == "show running config":
        return "show running-config"
    return command


def _normalize_command_key(value):
    return " ".join(
        str(value or "")
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(".", " ")
        .split()
    )


def _choose_show_run_file_noninteractive(saved_log_paths):
    show_run_candidates = [
        path for path in saved_log_paths if is_show_run_filename(os.path.basename(path))
    ]
    if show_run_candidates:
        def score(path):
            name = os.path.basename(path).lower().replace("_", " ").replace("-", " ")
            if "show running config" in name:
                return 0
            if "show run" in name:
                return 1
            if "showrun" in name:
                return 2
            if "sh run" in name:
                return 3
            return 4

        show_run_candidates.sort(key=score)
        return show_run_candidates[0]
    return saved_log_paths[0] if saved_log_paths else None


def _write_device_logs_manifest(hostname_dir, hostname, saved_log_paths, skipped_logs):
    skipped_files = {os.path.basename(item.get("file", "")) for item in skipped_logs}
    command_map = {}
    required_command_types = []

    for path in saved_log_paths:
        filename = os.path.basename(path)
        if filename in skipped_files:
            command_map[filename] = None
            continue
        command_type = detect_command_type(path)
        command_map[filename] = command_type
        if command_type and command_type not in required_command_types:
            required_command_types.append(command_type)

    manifest_path = os.path.join(hostname_dir, "logs.json")
    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(
            {
                "hostname": hostname,
                "show_run_file": os.path.basename(_choose_show_run_file_noninteractive(saved_log_paths) or ""),
                "logs": [os.path.basename(path) for path in saved_log_paths],
                "command_types": command_map,
                "required_command_types": required_command_types,
                "skipped_logs": skipped_logs,
            },
            manifest_file,
            indent=4,
        )


def _parse_and_write_device_baseline(template_dir, hostname, saved_log_paths):
    hostname_dir = os.path.join(template_dir, hostname)
    template_config, skipped_logs = parse_device_logs_with_report(saved_log_paths)
    config_json_path = os.path.join(hostname_dir, "config.json")
    with open(config_json_path, "w", encoding="utf-8") as target_file:
        json.dump(template_config, target_file, indent=4)
    _write_device_logs_manifest(hostname_dir, hostname, saved_log_paths, skipped_logs)


def save_template_setup(base_dir, template_name, devices_meta, source_template_name=""):
    template_dir = os.path.join(base_dir, "comparison_engine", "templates", template_name)
    _copy_source_template(base_dir, template_name, source_template_name)

    has_baseline = False
    for hostname, commands in (devices_meta or {}).items():
        hostname_dir = os.path.join(template_dir, hostname)
        logs_dir = os.path.join(hostname_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        if os.path.exists(os.path.join(hostname_dir, "config.json")):
            has_baseline = True

    _write_template_manifest(template_dir, template_name, devices_meta or {}, has_baseline)
    return {
        "status": "success",
        "template_name": template_name,
        "devices_meta": devices_meta or {},
        "has_baseline": has_baseline,
    }


def import_template_from_logs_dir(base_dir, template_name, source_dir, source_template_name=""):
    if not source_dir or not os.path.isdir(source_dir):
        return {"status": "error", "message": "Source logs folder not found."}

    template_dir = os.path.join(base_dir, "comparison_engine", "templates", template_name)
    _copy_source_template(base_dir, template_name, source_template_name)

    devices_meta = {}
    results = {}
    has_baseline = False

    for entry in sorted(os.scandir(source_dir), key=lambda item: item.name.lower()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        hostname = entry.name
        hostname_dir = os.path.join(template_dir, hostname)
        hostname_logs_dir = os.path.join(hostname_dir, "logs")
        os.makedirs(hostname_logs_dir, exist_ok=True)

        saved_log_paths = []
        commands = []

        for child in sorted(os.scandir(entry.path), key=lambda item: item.name.lower()):
            if not child.is_file() or child.name.startswith("."):
                continue
            destination = os.path.join(hostname_logs_dir, child.name)
            shutil.copyfile(child.path, destination)
            saved_log_paths.append(destination)
            commands.append(_safe_command_name_from_file(child.name))

        devices_meta[hostname] = commands

        if not saved_log_paths:
            results[hostname] = "No log files found."
            continue

        try:
            _parse_and_write_device_baseline(template_dir, hostname, saved_log_paths)
            results[hostname] = "Success"
            has_baseline = True
        except Exception as exc:
            results[hostname] = f"Error parsing: {exc}"

    _write_template_manifest(template_dir, template_name, devices_meta, has_baseline)
    return {
        "status": "success",
        "results": results,
        "template_name": template_name,
        "devices_meta": devices_meta,
        "has_baseline": has_baseline,
    }


def import_logs_folder_strict(base_dir, template_name, source_dir, devices_meta, source_template_name=""):
    if not source_dir or not os.path.isdir(source_dir):
        return {"status": "error", "message": "Source logs folder not found."}

    template_dir = os.path.join(base_dir, "comparison_engine", "templates", template_name)
    _copy_source_template(base_dir, template_name, source_template_name)

    expected_devices = devices_meta or {}
    source_device_dirs = {
        entry.name.lower(): entry
        for entry in os.scandir(source_dir)
        if entry.is_dir() and not entry.name.startswith(".")
    }
    expected_device_keys = {str(hostname).lower() for hostname in expected_devices}

    results = {}
    ignored = {"devices": [], "commands": []}
    missing = {}
    has_baseline = False

    for hostname, commands in expected_devices.items():
        hostname_dir = os.path.join(template_dir, hostname)
        hostname_logs_dir = os.path.join(hostname_dir, "logs")
        os.makedirs(hostname_logs_dir, exist_ok=True)

        source_entry = source_device_dirs.get(str(hostname).lower())
        if not source_entry:
            missing[hostname] = list(commands or [])
            results[hostname] = "No matching device folder found."
            continue

        files_by_command = {}
        for child in sorted(os.scandir(source_entry.path), key=lambda item: item.name.lower()):
            if not child.is_file() or child.name.startswith("."):
                continue
            files_by_command[_normalize_command_key(_safe_command_name_from_file(child.name))] = child

        saved_log_paths = []
        missing_commands = []
        expected_command_keys = {_normalize_command_key(cmd) for cmd in commands or []}

        for command in commands or []:
            command_key = _normalize_command_key(command)
            child = files_by_command.get(command_key)
            if not child:
                missing_commands.append(command)
                continue

            safe_cmd = str(command).replace(" ", "_").replace("/", "_")
            destination = os.path.join(hostname_logs_dir, f"{safe_cmd}.txt")
            shutil.copyfile(child.path, destination)
            saved_log_paths.append(destination)

        for command_key, child in files_by_command.items():
            if command_key not in expected_command_keys:
                ignored["commands"].append(f"{hostname}/{child.name}")

        if missing_commands:
            missing[hostname] = missing_commands

        if not saved_log_paths:
            results[hostname] = "No matching log files found."
            continue

        try:
            _parse_and_write_device_baseline(template_dir, hostname, saved_log_paths)
            results[hostname] = "Success"
            has_baseline = True
        except Exception as exc:
            results[hostname] = f"Error parsing: {exc}"

    for source_key, source_entry in source_device_dirs.items():
        if source_key not in expected_device_keys:
            ignored["devices"].append(source_entry.name)

    if not has_baseline and os.path.isdir(template_dir):
        for entry in os.scandir(template_dir):
            if not entry.is_dir():
                continue
            if os.path.exists(os.path.join(entry.path, "config.json")):
                has_baseline = True
                break

    _write_template_manifest(template_dir, template_name, expected_devices, has_baseline)
    return {
        "status": "success",
        "results": results,
        "template_name": template_name,
        "devices_meta": expected_devices,
        "has_baseline": has_baseline,
        "ignored": ignored,
        "missing": missing,
    }


def handle_template_upload(files, form_data, base_dir):
    """
    Handles extracting uploaded logs from Device Setup, saving them into the 
    templates directory, and running the parser to generate the baseline config.

    Called by server.py's upload-template endpoint after the Electron GUI sends
    log files and device metadata. Returns a status payload for the frontend.
    """
    template_name = form_data.get("template_name", "default")
    devices_meta_str = form_data.get("devices_meta", "{}")

    try:
        devices_meta = json.loads(devices_meta_str)
    except json.JSONDecodeError:
        return {"status": "error", "message": "Invalid devices metadata format."}

    template_dir = os.path.join(base_dir, "comparison_engine", "templates", template_name)
    source_template_name = (form_data.get("source_template_name") or "").strip()
    _copy_source_template(base_dir, template_name, source_template_name)

    results = {}
    has_baseline = False

    for hostname, commands in devices_meta.items():
        hostname_logs_dir = os.path.join(template_dir, hostname, "logs")
        os.makedirs(hostname_logs_dir, exist_ok=True)

        saved_log_paths = []

        for cmd in commands:
            field_name = f"file_{hostname}_{cmd}"
            if field_name not in files:
                continue

            file_obj = files[field_name]
            if not file_obj.filename:
                continue

            safe_cmd = cmd.replace(" ", "_").replace("/", "_")
            filename = f"{safe_cmd}.txt"
            file_path = os.path.join(hostname_logs_dir, filename)

            if file_obj.filename.lower().endswith(".docx"):
                import docx2txt
                import tempfile

                with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                    file_obj.save(tmp.name)
                    tmp_path = tmp.name
                try:
                    text = docx2txt.process(tmp_path)
                    with open(file_path, "w", encoding="utf-8") as handle:
                        handle.write(text)
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
            else:
                file_obj.save(file_path)

            saved_log_paths.append(file_path)

        if not saved_log_paths:
            results[hostname] = "No baseline files uploaded."
            continue

        try:
            _parse_and_write_device_baseline(template_dir, hostname, saved_log_paths)
            results[hostname] = "Success"
            has_baseline = True
        except Exception as exc:
            results[hostname] = f"Error parsing: {exc}"

    if not has_baseline and os.path.isdir(template_dir):
        for entry in os.scandir(template_dir):
            if not entry.is_dir():
                continue
            if os.path.exists(os.path.join(entry.path, "config.json")):
                has_baseline = True
                break

    _write_template_manifest(template_dir, template_name, devices_meta, has_baseline)
    return {"status": "success", "results": results, "devices_meta": devices_meta}
