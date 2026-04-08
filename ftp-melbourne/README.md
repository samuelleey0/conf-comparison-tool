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

Simple script to upload all student folders at once.

**1. Edit the BASE_URL in the script:**

```bash
# Open the script and change BASE_URL to your endpoint
nano ftp-melbourne/upload_student_folders.py
```

Then run:

```bash
source fyp-venv/bin/activate
python ftp-melbourne/upload_student_folders.py
```

That's it. The script will upload all students from `comparison_engine/students`.

## Test Webpage

Open in browser:

```
http://127.0.0.1:6060/web/test-uploader
```

- Enter endpoint URL
- Select a student folder (folder picker)
- Click Upload Folder
- See ✓ or ✗ success/fail message

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
  -F "file=@comparison_engine/results/100000001/ABBY_result.json"
```

Batch upload:

```bash
curl -X POST http://127.0.0.1:6060/api/upload-logs \
  -F "student_id=100000001" \
  -F "files=@comparison_engine/results/100000001/ABBY_result.json" \
  -F "files=@comparison_engine/results/100000001/GATE_result.json"
```
