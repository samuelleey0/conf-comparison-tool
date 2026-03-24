#!/usr/bin/env python3
"""
Bulk upload student folders from comparsion_engine/students to the Melbourne test webpage backend.

Example:
    python ftp-melbourne/upload_student_folders.py \
      --base-url http://127.0.0.1:6060 \
      --students-dir comparsion_engine/students
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import requests


def iter_student_dirs(students_root: Path) -> Iterable[Path]:
    for entry in sorted(students_root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            yield entry


def collect_student_files(student_dir: Path) -> list[Path]:
    files = []
    for file_path in student_dir.rglob("*"):
        if file_path.is_file() and not file_path.name.startswith("."):
            files.append(file_path)
    return sorted(files)


def upload_student(
    session: requests.Session,
    base_url: str,
    student_dir: Path,
    exam_name: str,
    session_id: str,
    timeout: int,
) -> dict:
    files = collect_student_files(student_dir)
    student_id = student_dir.name

    if not files:
        return {
            "student_id": student_id,
            "status": "skipped",
            "message": "No log files found.",
            "file_count": 0,
        }

    data = {
        "student_id": student_id,
        "exam_name": exam_name,
        "session_id": session_id,
    }

    # Keep relative paths for debugging visibility in backend responses.
    relative_paths = [str(path.relative_to(student_dir).as_posix()) for path in files]
    data["relative_paths"] = json.dumps(relative_paths)

    upload_files = []
    opened_handles = []
    try:
        for file_path in files:
            rel_name = str(file_path.relative_to(student_dir).as_posix())
            file_handle = open(file_path, "rb")
            opened_handles.append(file_handle)
            upload_files.append(("files", (rel_name, file_handle, "text/plain")))

        response = session.post(
            f"{base_url}/api/upload-logs",
            data=data,
            files=upload_files,
            timeout=timeout,
        )

        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            return {
                "student_id": student_id,
                "status": "error",
                "file_count": len(files),
                "http_status": response.status_code,
                "message": payload.get("message", "Upload failed."),
            }

        return {
            "student_id": student_id,
            "status": "ok",
            "file_count": len(files),
            "message": payload.get("message", "Uploaded."),
        }
    except requests.RequestException as exc:
        return {
            "student_id": student_id,
            "status": "error",
            "file_count": len(files),
            "message": str(exc),
        }
    finally:
        for handle in opened_handles:
            handle.close()


def fetch_uploaded_folders(
    session: requests.Session, base_url: str, timeout: int
) -> dict:
    response = session.get(f"{base_url}/api/uploaded-folders", timeout=timeout)
    response.raise_for_status()
    return response.json()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload all student log folders to Melbourne test receiver backend."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:6060",
        help="Receiver base URL (default: http://127.0.0.1:6060)",
    )
    parser.add_argument(
        "--students-dir",
        default="comparsion_engine/students",
        help="Path to students root folder (default: comparsion_engine/students)",
    )
    parser.add_argument(
        "--exam-name",
        default="TNE20002",
        help="Exam name metadata sent with uploads.",
    )
    parser.add_argument(
        "--session-id",
        default="auto-bulk",
        help="Session id metadata sent with uploads.",
    )
    parser.add_argument(
        "--student-id",
        action="append",
        dest="student_ids",
        help="Optional specific student_id to upload. Repeat to include multiple.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="HTTP timeout per request in seconds (default: 90)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    students_root = Path(args.students_dir).resolve()

    if not students_root.exists() or not students_root.is_dir():
        print(f"[ERROR] Students directory not found: {students_root}")
        return 1

    requested = set(args.student_ids or [])
    all_dirs = list(iter_student_dirs(students_root))
    if requested:
        target_dirs = [d for d in all_dirs if d.name in requested]
    else:
        target_dirs = all_dirs

    if not target_dirs:
        print("[WARN] No matching student folders found to upload.")
        return 1

    print(f"[INFO] Students root: {students_root}")
    print(f"[INFO] Receiver: {args.base_url}")
    print(f"[INFO] Uploading {len(target_dirs)} student folder(s)...")

    session = requests.Session()
    results = []

    for student_dir in target_dirs:
        result = upload_student(
            session=session,
            base_url=args.base_url.rstrip("/"),
            student_dir=student_dir,
            exam_name=args.exam_name,
            session_id=args.session_id,
            timeout=args.timeout,
        )
        results.append(result)
        print(
            f"[{result['status'].upper()}] {result['student_id']} | files={result['file_count']} | {result['message']}"
        )

    ok_count = sum(1 for r in results if r["status"] == "ok")
    err_count = sum(1 for r in results if r["status"] == "error")
    skip_count = sum(1 for r in results if r["status"] == "skipped")

    print("\n[SUMMARY]")
    print(f"  success: {ok_count}")
    print(f"  failed : {err_count}")
    print(f"  skipped: {skip_count}")

    try:
        status_payload = fetch_uploaded_folders(
            session, args.base_url.rstrip("/"), args.timeout
        )
        print("\n[UPLOADED FOLDERS ON SERVER]")
        print(json.dumps(status_payload, indent=2))
    except Exception as exc:
        print(f"\n[WARN] Could not fetch uploaded folder status: {exc}")

    return 0 if err_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
