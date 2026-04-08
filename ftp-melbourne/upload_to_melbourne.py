#!/usr/bin/env python3
"""
Simple uploader for student folders.

Quick setup: Change BASE_URL and STUDENT_FOLDER below. Then run.
"""

from pathlib import Path
import json
from datetime import datetime, timezone

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
LAST_UPLOAD_SUMMARY_FILE = SCRIPT_DIR / "last_upload_summary.json"

# ===== CONFIGURE ENDPOINT HERE =====
# When Melbourne gives you the endpoint, just change this URL.
# Example: BASE_URL = "https://marking.unimelb.edu.au/api"
BASE_URL = "http://127.0.0.1:6060"
# ===================================

# ===== CONFIGURE SOURCE FOLDER HERE =====
# Change this to the folder containing student subfolders to upload
STUDENT_FOLDER = "comparison_engine/students"
# ========================================


def resolve_source_folder(student_folder: str | None = None) -> Path:
    """Resolve source folder path.

    Relative paths are resolved from project root (conf-comparison-tool).
    """
    configured = (student_folder or STUDENT_FOLDER).strip()
    source_path = Path(configured).expanduser()
    if source_path.is_absolute():
        return source_path
    return (PROJECT_ROOT / source_path).resolve()


def upload_student_folder(student_dir: Path, base_url: str | None = None) -> dict:
    resolved_base_url = (base_url or BASE_URL).strip()
    files = [
        f for f in student_dir.rglob("*") if f.is_file() and not f.name.startswith(".")
    ]
    if not files:
        return {
            "student_id": student_dir.name,
            "status": "skipped",
            "count": 0,
            "attempted_count": 0,
            "uploaded_count": 0,
        }

    attempted_count = len(files)

    form_data = {"student_id": student_dir.name}
    upload_files = [
        ("files", (str(f.relative_to(student_dir)), open(f, "rb"))) for f in files
    ]

    try:
        response = requests.post(
            f"{resolved_base_url}/api/upload-logs", data=form_data, files=upload_files
        )
        for _, (_, fh) in upload_files:
            fh.close()

        status = "ok" if response.ok else "error"
        uploaded_count = attempted_count if response.ok else 0
        return {
            "student_id": student_dir.name,
            "status": status,
            "count": uploaded_count,
            "attempted_count": attempted_count,
            "uploaded_count": uploaded_count,
        }
    except Exception as e:
        for _, (_, fh) in upload_files:
            fh.close()
        return {
            "student_id": student_dir.name,
            "status": "error",
            "count": 0,
            "attempted_count": attempted_count,
            "uploaded_count": 0,
            "error": str(e),
        }


def resolve_student_dirs(source_dir: Path) -> list[Path]:
    """Resolve parent students folder into student-id subfolders only.

    Expected layout:
    students/100000001, students/100000002, ...
    """
    # Avoid treating a single student-id folder as source; source must be parent folder.
    if source_dir.name.isdigit():
        return []

    return [
        d
        for d in sorted(source_dir.iterdir())
        if d.is_dir() and not d.name.startswith(".") and d.name.isdigit()
    ]


def run_upload(base_url: str | None = None, student_folder: str | None = None) -> dict:
    """Run upload and return a machine-readable summary."""
    resolved_base_url = (base_url or BASE_URL).strip()
    students_dir = resolve_source_folder(student_folder)

    if not students_dir.exists():
        summary = {
            "status": "error",
            "message": f"Source folder not found: {students_dir}",
            "base_url": resolved_base_url,
            "student_folder": str(students_dir),
            "results": [],
            "student_count": 0,
            "uploaded_file_count": 0,
            "attempted_file_count": 0,
        }
        _write_last_summary(summary)
        return summary

    student_dirs = resolve_student_dirs(students_dir)
    if not student_dirs:
        summary = {
            "status": "error",
            "message": (
                "No student folders found. Set STUDENT_FOLDER to the parent students folder "
                "(example: comparison_engine/students) containing numeric student ID subfolders."
            ),
            "base_url": resolved_base_url,
            "student_folder": str(students_dir),
            "results": [],
            "student_count": 0,
            "uploaded_file_count": 0,
            "attempted_file_count": 0,
        }
        _write_last_summary(summary)
        return summary

    results = [
        upload_student_folder(student_dir, base_url=resolved_base_url)
        for student_dir in student_dirs
    ]

    uploaded_file_count = sum(result.get("uploaded_count", 0) for result in results)
    attempted_file_count = sum(result.get("attempted_count", 0) for result in results)
    success_count = sum(1 for result in results if result.get("status") == "ok")
    error_count = sum(1 for result in results if result.get("status") == "error")

    overall_status = "ok" if error_count == 0 else "partial"
    summary = {
        "status": overall_status,
        "message": (
            f"Uploaded {uploaded_file_count}/{attempted_file_count} files "
            f"from {len(student_dirs)} student folder(s)"
        ),
        "base_url": resolved_base_url,
        "student_folder": str(students_dir),
        "results": results,
        "student_count": len(student_dirs),
        "uploaded_file_count": uploaded_file_count,
        "attempted_file_count": attempted_file_count,
        "success_count": success_count,
        "error_count": error_count,
    }
    _write_last_summary(summary)
    return summary


def _write_last_summary(summary: dict) -> None:
    payload = {
        **summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    LAST_UPLOAD_SUMMARY_FILE.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def main():
    summary = run_upload()
    if summary["status"] == "error":
        print(f"ERROR: {summary['message']}")
        return 1

    print(
        f"Uploading {summary['student_count']} student folder(s) to {summary['base_url']}\n"
    )

    for result in summary["results"]:
        status_symbol = (
            "✓"
            if result["status"] == "ok"
            else "✗" if result["status"] == "error" else "⊘"
        )
        print(f"{status_symbol} {result['student_id']}: {result['count']} files")

    print("\nDone.")
    return 0 if summary["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
