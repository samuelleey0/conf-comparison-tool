# Melbourne Test Receiver

Backend test service to verify whether your webpage can upload collected student logs.

## Run

From project root:

```bash
source fyp-venv/bin/activate
python ftp-melbourne/melbourne_receiver.py
```

Default base URL:

- http://127.0.0.1:6060

Web test page:

- http://127.0.0.1:6060/web/test-uploader

## Endpoints

- `GET /health`
- `GET /api/endpoints`
- `GET /api/inbox`
- `GET /api/uploaded-folders`
- `POST /api/upload-log` (single file)
- `POST /api/upload-logs` (multiple files)

## Bulk Upload From Student Folders

This script scans student ID folders under `comparsion_engine/students` and uploads all log files for each student in a batch request.

```bash
source fyp-venv/bin/activate
python ftp-melbourne/upload_student_folders.py \
  --base-url http://127.0.0.1:6060 \
  --students-dir comparsion_engine/students \
  --exam-name TNE20002 \
  --session-id Week5
```

Optional: upload only selected students

```bash
python ftp-melbourne/upload_student_folders.py \
  --student-id 100000000 \
  --student-id 100000001
```

After upload, open the test webpage and use **Refresh Inbox** to see uploaded student folders.

## Upload Contract

Single file:

- Required multipart field: `file`
- Optional fields: `student_id`, `exam_name`, `session_id`, `hostname`

Multiple files:

- Required multipart field: `files` (repeat this field for each file)
- Optional fields: `student_id`, `exam_name`, `session_id`

Received files are stored in:

- `ftp-melbourne/inbox/<student_id>/`

## Quick Test (curl)

Single file:

```bash
curl -X POST http://127.0.0.1:6060/api/upload-log \
  -F "student_id=100000001" \
  -F "exam_name=TNE20002" \
  -F "session_id=Week5" \
  -F "hostname=ABBY" \
  -F "file=@comparsion_engine/results/100000001/ABBY_result.json"
```

Batch upload:

```bash
curl -X POST http://127.0.0.1:6060/api/upload-logs \
  -F "student_id=100000001" \
  -F "files=@comparsion_engine/results/100000001/ABBY_result.json" \
  -F "files=@comparsion_engine/results/100000001/GATE_result.json"
```
