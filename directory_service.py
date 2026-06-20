"""Directory, session, student, and mirror-path helpers.

The app keeps a user-facing Documents tree and a mirrored
comparison_engine/students tree. Functions here maintain that mapping so pages
do not need to know which side of the mirror they are reading or writing.
"""

import json
import os
import re
import shutil
import string
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = (Path.home() / "Documents").resolve()
ENGINE_STUDENTS_DIR = BASE_DIR / "comparison_engine" / "students"
UNIFIED_LOGS_DIR_NAME = "logs"
WINDOWS_DRIVES_ROOT = "__WINDOWS_DRIVES__"
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


def is_windows_platform():
    return os.name == "nt"


def normalize_directory_segment(value, field_label):
    segment = str(value or "").strip()
    if not segment:
        raise ValueError(f"Missing {field_label}.")
    if segment in {".", ".."}:
        raise ValueError(f"{field_label} cannot be '.' or '..'.")
    if "/" in segment or "\\" in segment:
        raise ValueError(f"{field_label} cannot contain path separators.")
    if "\x00" in segment:
        raise ValueError(f"{field_label} contains an invalid null character.")
    if not is_windows_platform():
        return segment

    cleaned = "".join(
        "-" if ch in WINDOWS_INVALID_SEGMENT_CHARS else ch for ch in segment
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(" .")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        raise ValueError(f"{field_label} cannot be empty after Windows-safe cleanup.")

    reserved_name = cleaned.split(".")[0].upper()
    if reserved_name in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned


def expand_path(path):
    return os.path.expanduser(path) if path else None


def engine_student_logs_dir(classroom, tutor_name, time_slot, student_id, hostname=None):
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


def delete_engine_student_logs_for_docs_target(target):
    try:
        relative = target.resolve().relative_to(DOCS_DIR)
    except Exception:
        return

    if len(relative.parts) < 1:
        return

    mirror_target = ENGINE_STUDENTS_DIR.joinpath(*relative.parts)
    if mirror_target.exists():
        shutil.rmtree(mirror_target)


def sync_docs_student_folder_to_engine(student_dir):
    """Mirror a completed Documents student folder into comparison_engine/students."""
    source = Path(student_dir).expanduser().resolve()
    try:
        relative = source.relative_to(DOCS_DIR)
    except Exception:
        return None

    if len(relative.parts) < 4:
        return None

    student_id = relative.parts[3]
    if student_id.lower() in {"sample", "unknown"}:
        return None

    if not source.is_dir():
        return None

    mirror_target = ENGINE_STUDENTS_DIR.joinpath(*relative.parts[:4])
    if mirror_target.exists():
        shutil.rmtree(mirror_target)
    mirror_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        mirror_target,
        ignore=shutil.ignore_patterns(
            "config.json",
            "results",
            ".DS_Store",
        ),
    )
    return str(mirror_target)


def session_student_names_path(session_dir: Path) -> Path:
    return session_dir / "students.json"


def load_session_student_names(session_dir: Path) -> dict:
    path = session_student_names_path(session_dir)
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


def save_session_student_names(session_dir: Path, names: dict):
    path = session_student_names_path(session_dir)
    cleaned = {
        str(k): str(v)
        for k, v in (names or {}).items()
        if str(k).strip() and str(v).strip()
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)


def safe_is_visible_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not path.name.startswith(".")
    except (OSError, PermissionError):
        return False


def safe_iterdir(path: Path):
    try:
        return list(path.iterdir())
    except (OSError, PermissionError):
        return []


def _is_repo_container_dir(path: Path) -> bool:
    try:
        Path(BASE_DIR).resolve().relative_to(path.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False


def _unique_file_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        numbered = directory / f"{stem}_{index}{suffix}"
        if not numbered.exists():
            return numbered
        index += 1


def save_log_entry(
    student_id,
    device_id,
    command,
    raw_text,
    source_type,
    session_dir=None,
    logs_root=None,
    include_student_dir=True,
):
    """
    Save one collected log in the legacy student/device folder layout.

    Default layout under a session:
      <student_id>/<device_id>/<command>.txt
    """
    safe_student = normalize_directory_segment(student_id, "Student ID")
    safe_device = normalize_directory_segment(device_id, "Device ID")
    safe_command = str(command or "").strip()
    if not safe_command:
        raise ValueError("Missing command.")

    safe_source = str(source_type or "").strip().lower()
    if safe_source not in {"serial", "manual"}:
        raise ValueError("source_type must be 'serial' or 'manual'.")

    text = "" if raw_text is None else str(raw_text)
    if safe_source == "manual" and not text.strip():
        raise ValueError("Manual log content cannot be empty.")

    if logs_root is None:
        if session_dir is None:
            logs_root = BASE_DIR
        else:
            logs_root = Path(session_dir)
    else:
        logs_root = Path(logs_root)

    if include_student_dir:
        device_dir = logs_root / safe_student / safe_device
    else:
        device_dir = logs_root / safe_device
    device_dir.mkdir(parents=True, exist_ok=True)
    command_segment = safe_command.replace(" ", "_").replace("/", "_")
    raw_path = device_dir / f"{command_segment}.txt"
    raw_path.write_text(text, encoding="utf-8")

    metadata = {
        "student_id": safe_student,
        "device_id": safe_device,
        "command": safe_command,
        "source_type": safe_source,
        "raw_log_path": str(raw_path),
    }

    return {
        "raw_log_path": str(raw_path),
        "metadata_path": None,
        "logs_root": str(logs_root),
        "metadata": metadata,
    }


def _iter_unified_session_log_dirs(docs_dir: Path):
    if not docs_dir.exists():
        return
    for classroom_dir in safe_iterdir(docs_dir):
        if not safe_is_visible_dir(classroom_dir):
            continue
        if _is_repo_container_dir(classroom_dir):
            continue
        for tutor_dir in safe_iterdir(classroom_dir):
            if not safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in safe_iterdir(tutor_dir):
                if not safe_is_visible_dir(time_dir):
                    continue
                logs_dir = time_dir / UNIFIED_LOGS_DIR_NAME
                if logs_dir.is_dir():
                    yield classroom_dir.name, tutor_dir.name, time_dir.name, logs_dir


def _iter_legacy_session_log_files(docs_dir: Path):
    ignored_names = {
        "config.json",
        "logs.json",
        "summary.json",
        "readableresult.txt",
        "students.json",
    }
    if not docs_dir.exists():
        return
    for classroom_dir in safe_iterdir(docs_dir):
        if not safe_is_visible_dir(classroom_dir):
            continue
        if _is_repo_container_dir(classroom_dir):
            continue
        for tutor_dir in safe_iterdir(classroom_dir):
            if not safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in safe_iterdir(tutor_dir):
                if not safe_is_visible_dir(time_dir):
                    continue
                for student_dir in safe_iterdir(time_dir):
                    if not safe_is_visible_dir(student_dir):
                        continue
                    if student_dir.name in {UNIFIED_LOGS_DIR_NAME, "results"}:
                        continue
                    for device_dir in safe_iterdir(student_dir):
                        if not safe_is_visible_dir(device_dir):
                            continue
                        if device_dir.name in {"results", "raw", "metadata"}:
                            continue
                        nested_logs_dir = device_dir / UNIFIED_LOGS_DIR_NAME
                        log_dirs = (
                            [nested_logs_dir, device_dir]
                            if safe_is_visible_dir(nested_logs_dir)
                            else [device_dir]
                        )
                        for log_dir in log_dirs:
                            for log_path in safe_iterdir(log_dir):
                                if not log_path.is_file() or log_path.name.startswith("."):
                                    continue
                                if log_path.name.lower() in ignored_names:
                                    continue
                                if log_path.suffix.lower() not in {"", ".txt", ".log"}:
                                    continue
                                yield (
                                    classroom_dir.name,
                                    tutor_dir.name,
                                    time_dir.name,
                                    student_dir.name,
                                    device_dir.name,
                                    log_path,
                                )


def _destination_for_mirror_log(target_dir: Path, source_path: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    candidate = target_dir / source_path.name
    if candidate.exists():
        try:
            if candidate.read_bytes() == source_path.read_bytes():
                return candidate
        except OSError:
            pass
    for existing in target_dir.glob(f"{source_path.stem}*{source_path.suffix}"):
        try:
            if existing.is_file() and existing.read_bytes() == source_path.read_bytes():
                return existing
        except OSError:
            continue
    return _unique_file_path(target_dir, source_path.name)


def sync_unified_logs_to_mirror(docs_dir=None, engine_students_dir=None):
    docs_root = Path(docs_dir) if docs_dir is not None else DOCS_DIR
    engine_root = (
        Path(engine_students_dir)
        if engine_students_dir is not None
        else ENGINE_STUDENTS_DIR
    )
    synced_count = 0
    skipped_count = 0
    valid_count = 0

    def copy_to_mirror(classroom, tutor_name, time_slot, student_id, device_id, source_path):
        nonlocal synced_count, skipped_count, valid_count
        valid_count += 1
        target_dir = (
            engine_root
            / classroom
            / tutor_name
            / time_slot
            / student_id
            / device_id
        )
        destination = _destination_for_mirror_log(target_dir, source_path)
        if destination.exists():
            skipped_count += 1
            return
        shutil.copy2(source_path, destination)
        synced_count += 1

    for classroom, tutor_name, time_slot, logs_dir in _iter_unified_session_log_dirs(
        docs_root
    ):
        for metadata_path in logs_dir.glob("*/*/metadata/*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                skipped_count += 1
                continue

            student_id = str(metadata.get("student_id") or "").strip()
            device_id = str(metadata.get("device_id") or "").strip()
            raw_log_path = Path(str(metadata.get("raw_log_path") or ""))
            if (
                not student_id
                or student_id.lower() in {"sample", "unknown"}
                or not device_id
                or not raw_log_path.is_file()
            ):
                skipped_count += 1
                continue

            copy_to_mirror(
                classroom, tutor_name, time_slot, student_id, device_id, raw_log_path
            )

    for (
        classroom,
        tutor_name,
        time_slot,
        student_id,
        device_id,
        log_path,
    ) in _iter_legacy_session_log_files(docs_root):
        if str(student_id).lower() in {"sample", "unknown"}:
            skipped_count += 1
            continue
        copy_to_mirror(classroom, tutor_name, time_slot, student_id, device_id, log_path)

    return {
        "success": valid_count > 0,
        "synced_count": synced_count,
        "skipped_count": skipped_count,
        "message": (
            "Mirror sync completed"
            if valid_count > 0
            else "No valid logs available for mirror sync"
        ),
    }


def save_output_to_engine_students(
    command, output, classroom, tutor_name, time_slot, student_id, hostname
):
    if not hostname:
        return None
    target_dir = engine_student_logs_dir(
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


def validate_directory_payload(data):
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
        raise ValueError("Missing classroom/tutor_name/time_slot/studentId")

    return (
        normalize_directory_segment(classroom, "Classroom"),
        normalize_directory_segment(tutor_name, "Tutor name"),
        normalize_directory_segment(time_slot, "Time slot"),
        normalize_directory_segment(student_id, "Student ID"),
    )


def list_existing_directories():
    results = []
    if not DOCS_DIR.exists():
        return results

    for classroom_dir in safe_iterdir(DOCS_DIR):
        if not safe_is_visible_dir(classroom_dir):
            continue
        for tutor_dir in safe_iterdir(classroom_dir):
            if not safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in safe_iterdir(tutor_dir):
                if not safe_is_visible_dir(time_dir):
                    continue
                student_names = load_session_student_names(time_dir)
                for student_dir in safe_iterdir(time_dir):
                    if not safe_is_visible_dir(student_dir):
                        continue
                    results.append(
                        {
                            "path": str(student_dir),
                            "classroom": classroom_dir.name,
                            "tutor_name": tutor_dir.name,
                            "time_slot": time_dir.name,
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


def list_existing_sessions():
    results = []
    if not DOCS_DIR.exists():
        return results

    for classroom_dir in safe_iterdir(DOCS_DIR):
        if not safe_is_visible_dir(classroom_dir):
            continue
        for tutor_dir in safe_iterdir(classroom_dir):
            if not safe_is_visible_dir(tutor_dir):
                continue
            for time_dir in safe_iterdir(tutor_dir):
                if not safe_is_visible_dir(time_dir):
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


def list_existing_exams():
    results = []
    if not DOCS_DIR.exists():
        return results

    for classroom_dir in safe_iterdir(DOCS_DIR):
        if not safe_is_visible_dir(classroom_dir):
            continue
        has_session = any(safe_is_visible_dir(d) for d in safe_iterdir(classroom_dir))
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


def is_windows_drives_root(path_val):
    return os.name == "nt" and str(path_val or "") == WINDOWS_DRIVES_ROOT


def list_windows_drive_roots():
    drives = []
    if os.name != "nt":
        return drives

    for letter in string.ascii_uppercase:
        drive_path = f"{letter}:\\"
        if os.path.exists(drive_path):
            drives.append({"name": f"{letter}:", "path": drive_path, "is_drive": True})
    return drives


def resolve_picker_path(path_val, fallback):
    if is_windows_drives_root(path_val):
        return WINDOWS_DRIVES_ROOT
    if path_val:
        return Path(expand_path(path_val)).resolve()
    return fallback


def create_directory(data):
    classroom, tutor_name, time_slot, student_id = validate_directory_payload(data)
    student_name = (data.get("studentName") or data.get("student_name") or "").strip()
    base_path = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    if student_name:
        session_dir = DOCS_DIR / classroom / tutor_name / time_slot
        names = load_session_student_names(session_dir)
        names[student_id] = student_name
        save_session_student_names(session_dir, names)
    return {
        "message": f"Directory ready: {base_path}",
        "path": base_path,
        "classroom": classroom,
        "tutor_name": tutor_name,
        "time_slot": time_slot,
        "exam_name": classroom,
        "session_id": tutor_name,
        "student_id": student_id,
        "student_name": student_name,
    }


def select_directory(data):
    existing_path = expand_path(data.get("existingPath"))
    if not existing_path:
        raise ValueError("Missing existingPath for selection")
    if not os.path.exists(existing_path):
        raise FileNotFoundError(f"Path not found: {existing_path}")

    parts = Path(existing_path).parts
    if len(parts) >= 4:
        classroom, tutor_name, time_slot, student_id = (
            parts[-4],
            parts[-3],
            parts[-2],
            parts[-1],
        )
    else:
        classroom = data.get("classroom") or data.get("examName") or data.get("exam_name")
        tutor_name = (
            data.get("tutor_name")
            or data.get("tutorName")
            or data.get("sessionId")
            or data.get("session_id")
        )
        time_slot = data.get("time_slot") or data.get("timeSlot")
        student_id = data.get("studentId") or data.get("student_id")
    return {
        "message": f"Using existing directory: {existing_path}",
        "path": existing_path,
        "classroom": classroom,
        "tutor_name": tutor_name,
        "time_slot": time_slot,
        "exam_name": classroom,
        "session_id": tutor_name,
        "student_id": student_id,
    }


def create_bulk_directories(data):
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
        raise ValueError("Missing classroom/tutor_name/time_slot/students for bulk creation.")

    classroom = normalize_directory_segment(classroom, "Classroom")
    tutor_name = normalize_directory_segment(tutor_name, "Tutor name")
    time_slot = normalize_directory_segment(time_slot, "Time slot")

    created = []
    session_dir = DOCS_DIR / classroom / tutor_name / time_slot
    session_dir.mkdir(parents=True, exist_ok=True)
    student_names = load_session_student_names(session_dir)
    for student in students:
        student_id = (student.get("id") or "").strip()
        student_name = (student.get("name") or "").strip()
        if not student_id:
            continue
        student_id = normalize_directory_segment(student_id, "Student ID")
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
    save_session_student_names(session_dir, student_names)
    return created


def add_student_to_session(data):
    session_path = expand_path(data.get("session_path"))
    student_id = (data.get("student_id") or "").strip()
    student_name = (data.get("student_name") or "").strip()
    if not session_path or not student_id:
        raise ValueError("Missing session_path or student_id.")
    student_id = normalize_directory_segment(student_id, "Student ID")

    session_dir = Path(session_path)
    if not session_dir.exists() or not session_dir.is_dir():
        raise FileNotFoundError("Session path not found.")
    try:
        session_dir.resolve().relative_to(DOCS_DIR)
    except Exception:
        raise ValueError("Invalid session path.")

    student_dir = session_dir / student_id
    student_dir.mkdir(parents=True, exist_ok=True)
    names = load_session_student_names(session_dir)
    if student_name:
        names[student_id] = student_name
    existing_name = names.get(student_id, "")
    save_session_student_names(session_dir, names)

    parts = student_dir.parts
    classroom = parts[-4] if len(parts) >= 4 else ""
    tutor_name = parts[-3] if len(parts) >= 3 else ""
    time_slot = parts[-2] if len(parts) >= 2 else ""
    return {
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
