"""Melbourne export packaging helpers.

The GUI calls into this module through server.py. It prepares temporary
Melbourne-required files inside a work directory, copies only the final student
deliverables into the zip, and intentionally keeps exam_config.toml and
master_solution.ini out of the exported archive.
"""

import configparser
import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ENGINE_STUDENTS_DIR = BASE_DIR / "comparison_engine" / "students"
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


def _quote_toml(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_toml_value(value):
    if isinstance(value, str):
        return _quote_toml(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_render_toml_value(item) for item in value) + "]"
    return _quote_toml(value)


def _render_toml(data):
    lines = []
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        simple_values = {
            key: value for key, value in values.items() if not isinstance(value, dict)
        }
        nested_values = {
            key: value for key, value in values.items() if isinstance(value, dict)
        }
        if simple_values:
            lines.append(f"[{section}]")
            for key, value in simple_values.items():
                lines.append(f"{key} = {_render_toml_value(value)}")
            lines.append("")
        for nested_section, nested_values_map in nested_values.items():
            lines.append(f"[{section}.{nested_section}]")
            for key, value in nested_values_map.items():
                lines.append(f"{key} = {_render_toml_value(value)}")
            lines.append("")
    return "\n".join(lines)


def _slug(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned or "melbourne-export"


def _safe_int(value, default, minimum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _parse_csv(value, default=None):
    items = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return items or list(default or [])


def _parse_int_csv(value, default):
    parsed = []
    for item in _parse_csv(value, default):
        try:
            parsed.append(int(item))
        except ValueError as exc:
            raise ValueError(f'Invalid number "{item}" in comma-separated value.') from exc
    return parsed


def _derive_exam_fields(exam_name):
    words = str(exam_name or "").split()
    unitcode = words[0] if words else "UNITCODE"
    shortname = "exam"
    if len(words) > 1:
        shortname = re.sub(r"[^A-Za-z0-9_]+", "_", words[1].lower()).strip("_") or shortname
    return unitcode, shortname


def _session_path(payload):
    session_path = payload.get("session_path")
    if session_path:
        resolved = Path(session_path).expanduser().resolve()
    else:
        classroom = _normalize_directory_segment(payload.get("classroom"), "classroom")
        tutor_name = _normalize_directory_segment(payload.get("tutor_name"), "tutor name")
        time_slot = _normalize_directory_segment(payload.get("time_slot"), "time slot")
        resolved = (ENGINE_STUDENTS_DIR / classroom / tutor_name / time_slot).resolve()
    try:
        resolved.relative_to(ENGINE_STUDENTS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("Session directory must be inside comparison_engine/students.") from exc
    if not resolved.is_dir():
        raise ValueError(f'Session directory "{resolved}" does not exist.')
    return resolved


def _student_dirs(collection_root):
    return sorted(
        path for path in collection_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _discover_devices(collection_root):
    devices = set()
    for student_dir in _student_dirs(collection_root):
        devices.update(
            path.name
            for path in student_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
    if not devices:
        raise ValueError(f'No device folders found below "{collection_root}".')
    return sorted(devices)


def _infer_device_type(collection_root, device_name):
    for log_file in collection_root.glob(f"*/{device_name}/*"):
        if not log_file.is_file():
            continue
        log_name = log_file.name.lower()
        if "vlan" in log_name or "trunk" in log_name or "spanning" in log_name:
            return "switch"
        if "route" in log_name:
            return "router"
    return "router"


def _write_exam_config(output_file, collection_root, payload):
    exam_name = str(payload.get("exam_name") or "").strip()
    if not exam_name:
        raise ValueError("Exam name is required.")
    inferred_unitcode, inferred_shortname = _derive_exam_fields(exam_name)
    semester = str(payload.get("semester") or "2026 S1").strip()
    unitcode = str(payload.get("unitcode") or inferred_unitcode).strip()
    shortname = str(payload.get("shortname") or inferred_shortname).strip()
    timeout = _safe_int(payload.get("timeout"), 180, 1)
    scheme_values = _parse_csv(payload.get("scheme_values"))
    if not scheme_values:
        raise ValueError("Allowed scheme values are required.")

    device_rows = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    device_types = {}
    device_names = {}
    for row in device_rows:
        folder = str((row or {}).get("folder") or "").strip()
        if not folder:
            continue
        device_type = str((row or {}).get("type") or "router").strip().lower()
        if device_type not in {"router", "switch", "asa"}:
            raise ValueError(f'Device "{folder}" has invalid type "{device_type}".')
        device_types[folder] = device_type
        device_names[folder] = str((row or {}).get("exam_name") or folder).strip() or folder

    lines = [
        "[details]",
        f"name = {_quote_toml(exam_name)}",
        f"semester = {_quote_toml(semester)}",
        f"unitcode = {_quote_toml(unitcode)}",
        f"shortname = {_quote_toml(shortname)}",
        "",
        "[collect]",
        f"timeout = {timeout}",
        "",
    ]
    for device in _discover_devices(collection_root):
        exam_device_name = device_names.get(device, device)
        device_type = device_types.get(device, _infer_device_type(collection_root, device))
        lines.extend(
            [
                f"[collect.{exam_device_name}]",
                f"type = {_quote_toml(device_type)}",
                "",
            ]
        )
    lines.extend(
        [
            "[options]",
            "scheme = [" + ", ".join(_quote_toml(value) for value in scheme_values) + "]",
            "",
        ]
    )
    output_file.write_text("\n".join(lines), encoding="utf-8")
    return {
        "exam_name": exam_name,
        "semester": semester,
        "unitcode": unitcode,
        "shortname": shortname,
        "timeout": timeout,
        "scheme_values": scheme_values,
        "devices": {
            device_names.get(device, device): {
                "type": device_types.get(device, _infer_device_type(collection_root, device)),
                "source_folder": device,
            }
            for device in _discover_devices(collection_root)
        },
    }


def _log_filename_to_command(filename):
    stem = Path(filename).stem.replace("_", " ")
    replacements = {
        "show running-config": "sh run",
        "show ip interface brief": "sh ip int brief",
        "show ip route": "sh ip route",
        "show vlan brief": "sh vlan brief",
        "show interfaces trunk": "sh int trunk",
        "show spanning-tree": "sh spanning-tree",
    }
    return replacements.get(stem, stem)


def _commands_from_collection(collection_root, source_device_name):
    commands = []
    seen = set()
    for log_file in sorted(collection_root.glob(f"*/{source_device_name}/*")):
        if not log_file.is_file():
            continue
        command = _log_filename_to_command(log_file.name)
        if command not in seen:
            commands.append(command)
            seen.add(command)
    preferred_order = [
        "sh run",
        "sh ip int brief",
        "sh ip route",
        "sh vlan brief",
        "sh int trunk",
        "sh spanning-tree",
    ]
    return sorted(
        commands,
        key=lambda command: (
            preferred_order.index(command) if command in preferred_order else 999,
            command,
        ),
    )


def _write_master_solution(output_file, collection_root, config):
    rubric_name = str(config.get("rubric_name") or "Major_Minor").strip() or "Major_Minor"
    maximum = _safe_int(config.get("maximum_marks"), 100, 1)
    minor_pen = _parse_int_csv(config.get("minor_penalties"), [10, 20, 30, 40])
    exam_details = config["exam_details"]
    devices = exam_details["devices"]
    lines = [
        "[Exam Details]",
        f'name = {exam_details["exam_name"]}',
        f'semester = {exam_details["semester"]}',
        f'unitcode = {exam_details["unitcode"]}',
        f'shortname = {exam_details["shortname"]}',
    ]
    for device_name in devices:
        lines.append(f"devices[] = {device_name}")
    lines.extend(
        [
            "",
            "[Collect]",
            "data_source = offline",
            "parallel = true",
            f'timeout_delay = {exam_details["timeout"]}',
            "",
        ]
    )
    for device_name, details in devices.items():
        source_folder = details.get("source_folder", device_name)
        device_type = {"router": "R", "switch": "S", "asa": "A"}.get(
            details.get("type", "router"), "R"
        )
        lines.extend([f"[{device_name}]", f"type = {device_type}"])
        commands = _commands_from_collection(collection_root, source_folder)
        if not commands:
            commands = ["sh run", "sh ip int brief"]
            if device_type == "R":
                commands.append("sh ip route")
            if device_type == "S":
                commands.extend(["sh vlan brief", "sh int trunk", "sh spanning-tree"])
        for command in commands:
            lines.append(f"commands[] = {command}")
        lines.append("")
    lines.extend(
        [
            "[Rubric]",
            f'rubric[{rubric_name}] = maximum:{maximum},minor_pen:{":".join(str(item) for item in minor_pen)}',
            "",
        ]
    )
    output_file.write_text("\n".join(lines), encoding="utf-8")
    return {"rubric_name": rubric_name, "maximum": maximum, "minor_penalties": minor_pen}


def _extract_rubrics(solution_file):
    rubric_re = re.compile(r"^\s*\w+\[(?P<rubric>[A-Za-z0-9_]+)\]\s*=\s*(?P<body>.+?)\s*$")
    rubrics = {}
    with solution_file.open("r", encoding="utf-8") as file:
        for line in file:
            match = rubric_re.match(line)
            if not match:
                continue
            entries = {}
            for item in match.group("body").split(","):
                parts = [part.strip() for part in item.split(":") if part.strip()]
                if len(parts) < 2:
                    continue
                values = [int(part) if part.isdigit() else part for part in parts[1:]]
                entries[parts[0]] = values[0] if len(values) == 1 else values
            rubrics[match.group("rubric")] = entries
    return rubrics


def _write_student_files(export_root, exam_name, solution_file, student_schemes):
    finalised = 0
    missing_schemes = []
    for student_dir in _student_dirs(export_root):
        scheme = str(student_schemes.get(student_dir.name) or "").strip()
        if not scheme:
            missing_schemes.append(student_dir.name)
            continue
        config = configparser.ConfigParser()
        config["Student Options"] = {"scheme": scheme}
        with (student_dir / "options.ini").open("w", encoding="utf-8") as file:
            config.write(file)
        shutil.copyfile(solution_file, student_dir / "solution.ini")
        exam_information = {
            "Information": {"name": exam_name},
            "Rubrics": _extract_rubrics(solution_file),
        }
        with (student_dir / "exam_info.toml").open("w", encoding="utf-8") as file:
            file.write(_render_toml(exam_information))
        finalised += 1
    return finalised, missing_schemes


def _zip_folder(source_dir, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_dir))


def export_to_melbourne(payload):
    collection_root = _session_path(payload)
    source_students = _student_dirs(collection_root)
    if not source_students:
        raise ValueError("No student folders found for this session.")

    classroom = str(payload.get("classroom") or collection_root.parts[-3])
    tutor_name = str(payload.get("tutor_name") or collection_root.parts[-2])
    export_time = datetime.now().strftime("%H%M")
    export_base_name = _slug(f"{classroom}-{tutor_name}-{export_time}")
    downloads_dir = Path.home() / "Downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    export_root = downloads_dir / export_base_name
    if export_root.exists():
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
        export_root = downloads_dir / f"{export_base_name}-{suffix}"
    shutil.copytree(
        collection_root,
        export_root,
        ignore=shutil.ignore_patterns(
            ".DS_Store",
            "results",
            "exam_info.toml",
            "options.ini",
            "solution.ini",
        ),
    )

    exam_config_file = export_root / "exam_config.toml"
    master_solution_file = export_root / "master_solution.ini"
    # These files drive finalisation, but Melbourne does not want them in the zip.
    exam_details = _write_exam_config(exam_config_file, export_root, payload)
    rubric_details = _write_master_solution(
        master_solution_file,
        export_root,
        {
            "exam_details": exam_details,
            "rubric_name": payload.get("rubric_name"),
            "maximum_marks": payload.get("maximum_marks"),
            "minor_penalties": payload.get("minor_penalties"),
        },
    )

    raw_student_schemes = payload.get("student_schemes")
    if not isinstance(raw_student_schemes, dict):
        raw_student_schemes = {}
    finalised, missing_schemes = _write_student_files(
        export_root,
        exam_details["exam_name"],
        master_solution_file,
        raw_student_schemes,
    )

    for internal_file in (exam_config_file, master_solution_file):
        try:
            # Remove after per-student options/solution/exam_info files are written.
            internal_file.unlink()
        except FileNotFoundError:
            pass

    zip_path = export_root.with_suffix(".zip")
    if zip_path.exists():
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
        zip_path = export_root.parent / f"{export_root.name}-{suffix}.zip"
    _zip_folder(export_root, zip_path)

    return {
        "message": "Melbourne export completed.",
        "export_folder": str(export_root),
        "zip_path": str(zip_path),
        "student_count": len(source_students),
        "finalised_count": finalised,
        "missing_schemes": missing_schemes,
        "exam_config": str(exam_config_file),
        "master_solution": str(master_solution_file),
        "rubric": rubric_details,
    }
