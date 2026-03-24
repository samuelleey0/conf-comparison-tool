#!/usr/bin/env python3
"""
Simple bulk uploader for student folders.

Quick setup: Change BASE_URL below. Then run.
"""

from pathlib import Path

import requests

# ===== CONFIGURE ENDPOINT HERE =====
# When Melbourne gives you the endpoint, just change this URL.
# Example: BASE_URL = "https://marking.unimelb.edu.au/api"
BASE_URL = "http://127.0.0.1:6060"
# ===================================

# ===== CONFIGURE SOURCE FOLDER HERE =====
# Change this to the folder containing student subfolders to upload
STUDENT_FOLDER = "comparsion_engine/students"
# ========================================


def upload_student_folder(student_dir: Path) -> dict:
    files = [
        f for f in student_dir.rglob("*") if f.is_file() and not f.name.startswith(".")
    ]
    if not files:
        return {"student_id": student_dir.name, "status": "skipped", "count": 0}

    form_data = {"student_id": student_dir.name}
    upload_files = [
        ("files", (str(f.relative_to(student_dir)), open(f, "rb"))) for f in files
    ]

    try:
        response = requests.post(
            f"{BASE_URL}/api/upload-logs", data=form_data, files=upload_files
        )
        for _, (_, fh) in upload_files:
            fh.close()

        status = "ok" if response.ok else "error"
        return {"student_id": student_dir.name, "status": status, "count": len(files)}
    except Exception as e:
        for _, (_, fh) in upload_files:
            fh.close()
        return {
            "student_id": student_dir.name,
            "status": "error",
            "count": len(files),
            "error": str(e),
        }


def main():
    students_dir = Path(STUDENT_FOLDER)
    if not students_dir.exists():
        print(f"ERROR: {students_dir} not found")
        return 1

    student_dirs = [
        d
        for d in sorted(students_dir.iterdir())
        if d.is_dir() and not d.name.startswith(".")
    ]
    if not student_dirs:
        print("No student folders found")
        return 1

    print(f"Uploading {len(student_dirs)} student folder(s) to {BASE_URL}\n")

    for student_dir in student_dirs:
        result = upload_student_folder(student_dir)
        status_symbol = (
            "✓"
            if result["status"] == "ok"
            else "✗" if result["status"] == "error" else "⊘"
        )
        print(f"{status_symbol} {result['student_id']}: {result['count']} files")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
