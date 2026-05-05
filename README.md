# Automated Cisco Configuration & Marking System

This project is part of the Final Year Project (FYP) to develop a network configuration comparison and marking tool.
It supports communication with Cisco switches/routers via SSH or Serial (USB-C/USB-A - Serial), log extraction, and automated grading against customizable rubrics for teaching units TNE10006 and TNE20002.

## Public Installation and Setup Guide

This guide is for users who want to install and run the Automated Cisco Configuration & Marking System from the public repository.

The tool provides a desktop GUI for collecting Cisco device command output over Serial or SSH, saving logs into organized folders, comparing configurations against templates, and producing grading results.

## Platform Support

This setup guide is based on Linux. The system was developed and tested primarily for Linux-based use, especially for Serial console access to Cisco devices.

Windows installation notes are included where useful, but Windows is not the main supported environment. If you install the system on Windows and encounter OS-specific errors, you may need to troubleshoot them yourself.

## Requirements

Install these before starting:

- Python 3.10 or newer
- Node.js 18 LTS or newer, including npm
- Git
- A Cisco router or switch accessible through Serial console or SSH

Check your installed versions:

```bash
python3 --version
node -v
npm -v
git --version
```

On Windows, `python` may be used instead of `python3`.

## 1. Download the Project

Clone the public repository with HTTPS:

```bash
git clone https://github.com/samuelleey0/conf-comparison-tool.git
cd conf-comparison-tool
```

If you downloaded the project as a ZIP file, extract it and open a terminal in the extracted `conf-comparison-tool` folder.

## 2. Create the Python Environment

The Electron desktop app expects the virtual environment to be named `fyp-venv` in the project root.

macOS / Linux:

```bash
python3 -m venv fyp-venv
source fyp-venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv fyp-venv
.\fyp-venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Windows Command Prompt:

```bat
python -m venv fyp-venv
fyp-venv\Scripts\activate
pip install -r requirements.txt
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again.

## 3. Install the Desktop App Dependencies

From the project root:

```bash
cd electron-gui
npm install
npm install xlsx (install this if you need to import Excel file)
```

## 4. Start the Application

From inside the `electron-gui` folder:

```bash
npm start
```

The app starts the Flask backend automatically using `../fyp-venv`.

Expected terminal output includes:

```text
[INFO] Spawning Flask using: .../fyp-venv/.../python
[INFO] Running server: .../server.py
[READY] Flask server is live at http://127.0.0.1:5050
```

An Electron desktop window should open after the backend is ready.

## 5. Connect a Cisco Device

The app supports two connection types.

### Option A: Serial Console

Use this when connecting through a console cable, USB-to-serial adapter, or PCIe serial card.

Default Cisco console settings:

- Baud rate: `9600`
- Data bits: `8`
- Parity: `None`
- Stop bits: `1`
- Flow control: `None`

Common serial ports:

- Linux: `/dev/ttyUSB0`, `/dev/ttyS0`, `/dev/ttyS4`, etc.
- macOS: `/dev/tty.usbserial-*` or `/dev/tty.usbmodem*`
- Windows: `COM3`, `COM4`, etc.

On Linux, list available serial ports:

```bash
ls /dev/ttyS* /dev/ttyUSB* 2>/dev/null
```

If you get permission errors on Linux, add your user to the `dialout` group:

```bash
sudo usermod -aG dialout $USER
```

Log out and log back in before trying again.

### Option B: SSH

Use this when the Cisco device already has network access and SSH configured.

You will need:

- Device IP address or hostname
- SSH username
- SSH password
- SSH port, usually `22`

Make sure your computer can reach the device before connecting:

```bash
ping <device-ip>
```

## 6. Basic Workflow

1. Open the app with `npm start`.
2. If no template logs are available yet, go to the Sample Logs tab and collect instructor/sample logs first.
3. If template logs are already available, proceed to Device Setup.
4. In Device Setup, set up the device details and choose the commands to run.
5. Create a new student folder, or select an existing student folder, to store logs for each student.
6. Choose the connection type: Serial or SSH.
7. Connect to the Cisco device.
8. Run all required devices and commands.
9. Review the saved outputs and grading results.

Collected files are stored under your Documents folder, usually in this structure:

```text
~/Documents/<Classroom>/<TutorName>/<ExamTime>/<StudentID>/<Hostname>/
```

Grading results are stored under the matching student/session folders.

## 7. Templates, Commands, and Rubrics

The desktop app includes administration screens for managing:

- Command lists
- Comparison templates
- Grading policies
- Rubric rules
- Student/result folders

Templates are stored in:

```text
comparison_engine/templates/
```

Student comparison copies are stored in:

```text
comparison_engine/students/
```

Generated user-facing output is stored in your system `Documents` folder.

## Troubleshooting

### Electron opens but backend features do not work

Make sure the Python virtual environment exists at the project root and is named exactly:

```text
fyp-venv
```

Then reinstall dependencies:

```bash
source fyp-venv/bin/activate
pip install -r requirements.txt
cd electron-gui
npm install
npm start
```

On Windows, activate with `.\fyp-venv\Scripts\Activate.ps1`.

### Port 5050 is already in use

The backend uses:

```text
http://127.0.0.1:5050
```

Close any existing process using port `5050`, then run `npm start` again.

### Serial device is not detected

Try unplugging and reconnecting the cable, then check the available ports again.

Linux:

```bash
dmesg | grep tty
ls /dev/ttyS* /dev/ttyUSB* 2>/dev/null
```

macOS:

```bash
ls /dev/tty.*
```

Windows:

Open Device Manager and check `Ports (COM & LPT)`.

### SSH connection fails

Check that:

- The Cisco device has SSH enabled
- The username and password are correct
- The device is reachable from your computer
- Firewalls or lab network rules are not blocking TCP port `22`

### Python package installation fails

Upgrade pip first:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Use `python3` instead of `python` on macOS/Linux if needed.

## Notes for Public Users

- This project currently runs from source. It is not packaged as a one-click installer yet.
- Keep any real student data, device credentials, and grading records private.
- Do not commit generated logs, student outputs, credentials, or local virtual environments.
- The internal SSH-to-GitHub setup from the original README is only needed for project contributors with repository write access.
