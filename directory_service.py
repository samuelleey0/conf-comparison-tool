"""Directory, session, student, and mirror-path helpers."""

import json
import os
import re
import shutil
import string
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = (Path.home() / "Documents").resolve()
ENGINE_STUDENTS_DIR = BASE_DIR / "comparison_engine" / "students"
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
