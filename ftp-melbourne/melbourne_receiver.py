#!/usr/bin/env python3
"""
Melbourne test receiver backend.

Purpose:
- Provide HTTP endpoints that a webpage can call to upload collected student logs.
- Save incoming files locally so you can verify transfer behavior before integrating
  with the real Melbourne marking system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from upload_student_folders import (
    BASE_URL as SENDER_BASE_URL,
    STUDENT_FOLDER as SENDER_STUDENT_FOLDER,
    resolve_source_folder,
    run_upload,
)
from werkzeug.utils import secure_filename


app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "inbox"
INBOX_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR = BASE_DIR / "web"
WEB_DIR.mkdir(parents=True, exist_ok=True)

# Default address you can call from your webpage.
DEFAULT_BASE_URL = "http://127.0.0.1:6060"


def _student_folder(student_id: str) -> Path:
    student = (student_id or "unknown_student").strip() or "unknown_student"
    target = INBOX_DIR / secure_filename(student)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _save_uploaded_file(student_id: str, upload_file) -> dict[str, Any]:
    original_name = upload_file.filename or "unnamed.log"
    # Preserve subdirectory structure: ABBY/sh_run.txt stays as ABBY/sh_run.txt
    safe_name = "/".join(secure_filename(part) for part in original_name.split("/"))
    safe_name = safe_name or "unnamed.log"
    target_dir = _student_folder(student_id)

    # Create subdirectories if needed (e.g., ABBY/, GATE/)
    target_file = target_dir / safe_name
    target_file.parent.mkdir(parents=True, exist_ok=True)
    upload_file.save(target_file)

    return {
        "student_id": student_id or "unknown_student",
        "original_name": original_name,
        "saved_name": safe_name,
        "saved_path": str(target_file),
        "size_bytes": target_file.stat().st_size,
    }


def _list_inbox() -> dict[str, Any]:
    students = []
    total_files = 0
    total_bytes = 0

    if not INBOX_DIR.exists():
        return {"students": [], "total_files": 0, "total_bytes": 0}

    for student_dir in sorted(INBOX_DIR.iterdir()):
        if not student_dir.is_dir():
            continue

        files = []
        student_total = 0
        # Recursively list all files to preserve folder structure
        for file_path in sorted(student_dir.rglob("*")):
            if not file_path.is_file():
                continue
            size = file_path.stat().st_size
            # Keep relative path to show folder structure (e.g., ABBY/sh_run.txt)
            rel_path = str(file_path.relative_to(student_dir))
            files.append(
                {
                    "name": rel_path,
                    "size_bytes": size,
                    "path": str(file_path),
                }
            )
            student_total += size

        total_files += len(files)
        total_bytes += student_total
        students.append(
            {
                "student_id": student_dir.name,
                "file_count": len(files),
                "total_bytes": student_total,
                "files": files,
            }
        )

    return {
        "students": students,
        "total_files": total_files,
        "total_bytes": total_bytes,
    }


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/upload-log", methods=["OPTIONS"])
@app.route("/api/upload-logs", methods=["OPTIONS"])
@app.route("/api/sender-upload", methods=["OPTIONS"])
def upload_options():
    return ("", 204)


@app.get("/")
def index():
    return jsonify(
        {
            "status": "ok",
            "service": "melbourne-test-receiver",
            "message": "Receiver is running.",
            "default_base_url": DEFAULT_BASE_URL,
            "endpoint_examples": {
                "health": f"{DEFAULT_BASE_URL}/health",
                "upload_single": f"{DEFAULT_BASE_URL}/api/upload-log",
                "upload_batch": f"{DEFAULT_BASE_URL}/api/upload-logs",
            },
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "inbox": str(INBOX_DIR)})


@app.get("/web/test-uploader")
def test_uploader_page():
    return send_from_directory(WEB_DIR, "test_uploader.html")


@app.get("/api/inbox")
def inbox_listing():
    listing = _list_inbox()
    return jsonify({"status": "ok", **listing})


@app.get("/api/uploaded-folders")
def uploaded_folders():
    listing = _list_inbox()
    folders = []
    for student in listing.get("students", []):
        folders.append(
            {
                "student_id": student.get("student_id"),
                "file_count": student.get("file_count", 0),
                "total_bytes": student.get("total_bytes", 0),
            }
        )
    return jsonify(
        {
            "status": "ok",
            "folder_count": len(folders),
            "total_files": listing.get("total_files", 0),
            "total_bytes": listing.get("total_bytes", 0),
            "folders": folders,
        }
    )


@app.get("/api/endpoints")
def endpoints():
    base_url = request.host_url.rstrip("/")
    return jsonify(
        {
            "status": "ok",
            "base_url": base_url,
            "upload_single": f"{base_url}/api/upload-log",
            "upload_batch": f"{base_url}/api/upload-logs",
            "uploaded_folders": f"{base_url}/api/uploaded-folders",
            "upload_method": "POST multipart/form-data",
            "required_field_single": "file",
            "optional_fields": ["student_id", "exam_name", "session_id", "hostname"],
            "required_field_batch": "files",
        }
    )


@app.get("/api/sender-config")
def sender_config():
    return jsonify(
        {
            "status": "ok",
            "source_folder": str(resolve_source_folder(SENDER_STUDENT_FOLDER)),
            "endpoint_base_url": SENDER_BASE_URL,
            "upload_url": f"{SENDER_BASE_URL}/api/upload-logs",
        }
    )


@app.post("/api/sender-upload")
def sender_upload():
    payload = request.get_json(silent=True) or {}
    confirm_value = (
        str(payload.get("confirm", request.form.get("confirm", ""))).strip().lower()
    )

    if confirm_value != "yes":
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Confirmation required. Send confirm='yes' to upload.",
                }
            ),
            400,
        )

    summary = run_upload()
    http_status = 200 if summary.get("status") != "error" else 400
    return jsonify(summary), http_status


@app.post("/api/upload-log")
def upload_log():
    if "file" not in request.files:
        return (
            jsonify({"status": "error", "message": "Missing file field 'file'."}),
            400,
        )

    student_id = request.form.get("student_id", "unknown_student")
    upload_file = request.files["file"]

    if not upload_file or not upload_file.filename:
        return jsonify({"status": "error", "message": "No file selected."}), 400

    saved = _save_uploaded_file(student_id, upload_file)
    saved["meta"] = {
        "exam_name": request.form.get("exam_name"),
        "session_id": request.form.get("session_id"),
        "hostname": request.form.get("hostname"),
    }

    return jsonify(
        {
            "status": "ok",
            "message": "File received successfully.",
            "endpoint": "/api/upload-log",
            "received": saved,
        }
    )


@app.post("/api/upload-logs")
def upload_logs_batch():
    files = request.files.getlist("files")
    if not files:
        return (
            jsonify({"status": "error", "message": "Missing files field 'files'."}),
            400,
        )

    student_id = request.form.get("student_id", "unknown_student")
    uploaded = []

    for upload_file in files:
        if upload_file and upload_file.filename:
            uploaded.append(_save_uploaded_file(student_id, upload_file))

    if not uploaded:
        return (
            jsonify({"status": "error", "message": "No valid files found in request."}),
            400,
        )

    return jsonify(
        {
            "status": "ok",
            "message": f"Received {len(uploaded)} files.",
            "endpoint": "/api/upload-logs",
            "student_id": student_id,
            "files": uploaded,
        }
    )


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 6060
    print("[Melbourne Receiver] Running test backend")
    print(f"[Melbourne Receiver] Base URL: {DEFAULT_BASE_URL}")
    print(
        f"[Melbourne Receiver] Upload single endpoint: {DEFAULT_BASE_URL}/api/upload-log"
    )
    print(
        f"[Melbourne Receiver] Upload batch endpoint: {DEFAULT_BASE_URL}/api/upload-logs"
    )
    app.run(host=host, port=port, debug=False)
