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

## 1. Configure Ubuntu Serial Permissions

Serial communication is the default connection method for this tool. On Ubuntu/Linux, your user must be in the `dialout` group to access USB serial devices such as `/dev/ttyUSB0`.

Run this once:

```bash
sudo usermod -aG dialout $USER
```

Then log out and log back in, or reboot, so the new group permission takes effect.

After logging back in, verify that `dialout` appears in your groups:

```bash
groups
```

Example output:

```text
ubuntu adm dialout cdrom sudo dip plugdev
```

Reconnect the USB serial cable and check the device permission:

```bash
ls -l /dev/ttyUSB0
```

You should usually see `/dev/ttyUSB0` owned by `root dialout`, for example:

```text
crw-rw---- 1 root dialout ... /dev/ttyUSB0
```

If `/dev/ttyUSB0` does not exist, check for other serial device names:

```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

## 2. Download the Project

Clone the public repository with HTTPS:

```bash
git clone https://github.com/samuelleey0/conf-comparison-tool.git
cd conf-comparison-tool
```

If you downloaded the project as a ZIP file, extract it and open a terminal in the extracted `conf-comparison-tool` folder.

## 3. Create the Python Environment

For development from the project folder, create a virtual environment named `fyp-venv` in the project root.

macOS / Linux:

```bash
python3 -m venv fyp-venv
source fyp-venv/bin/activate
pip install .
```

On Ubuntu/Linux, if `python3 -m venv fyp-venv` fails because `ensurepip` is not available, install the Python venv package first:

```bash
sudo apt update
sudo apt install python3-venv
python3 -m venv fyp-venv
```

If Ubuntu asks for a version-specific package, install the package shown in the error message instead, for example:

```bash
sudo apt install python3.14-venv
python3 -m venv fyp-venv
```

Windows PowerShell:

```powershell
python -m venv fyp-venv
.\fyp-venv\Scripts\Activate.ps1
pip install .
```

Windows Command Prompt:

```bat
python -m venv fyp-venv
fyp-venv\Scripts\activate
pip install .
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again.

You can still install dependencies with `pip install -r requirements.txt`, but `pip install .` is recommended because it also installs the `conf-comparison-server` backend command.

The Electron app first looks for the repo-local `fyp-venv`. If that environment is not present, it falls back to the installed `conf-comparison-server` command.

## 4. Install the Desktop App Dependencies

From the project root:

```bash
cd electron-gui
npm install
```

Note: `npm install` might take a while, so wait patiently for it to finish.

If you need to import Excel files, install the optional Excel dependency:

```bash
npm install xlsx
```

## 5. Start the Application

From inside the `electron-gui` folder:

```bash
npm start
```

On Ubuntu/Linux, if Electron exits with a `SUID sandbox helper binary` or `chrome-sandbox` error, fix the Electron sandbox permissions from inside the `electron-gui` folder:

```bash
sudo chown root:root node_modules/electron/dist/chrome-sandbox
sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
npm start
```

If you only need a temporary development workaround, you can start Electron without the sandbox:

```bash
npx electron --no-sandbox .
```

Use the permission fix above when possible because `--no-sandbox` disables Electron's Chromium sandbox.

The app starts the Flask backend automatically using `../fyp-venv`.

Expected terminal output includes:

```text
[INFO] Spawning Flask using: .../fyp-venv/.../python
[INFO] Running server: .../server.py
[READY] Flask server is live at http://127.0.0.1:5050
```

An Electron desktop window should open after the backend is ready.

## 6. Connect a Cisco Device

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
ls /dev/ttyS* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

If you get permission errors on Linux, complete the `dialout` setup in section 1, then log out and log back in before trying again.

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

## 7. Basic Workflow

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

## 8. Templates, Commands, and Rubrics

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
ls /dev/ttyS* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
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

## Optional: Build Packages and Installers

This section is only for developers or maintainers who want to create distributable packages or installers. You do not need these steps for a normal fresh setup from GitHub.

### Build an Installable Python Package

To move the Python backend to another device, build a wheel:

```bash
python -m pip install build
python -m build
```

Copy the generated `.whl` file from `dist/` to the other device, then install it there:

```bash
python -m pip install conf_comparison_tool-0.1.0-py3-none-any.whl
```

After installation, the backend can be started from any terminal:

```bash
conf-comparison-server
```

### Build an Ubuntu Installer

You can build Linux installers, including AppImage and Debian package outputs, on Ubuntu.

Install required build tools first:

```bash
sudo apt update
sudo apt install -y build-essential python3 python3-venv dpkg fakeroot rpm
```

From the project root:

```bash
cd electron-gui
npm install
npm run build:installer:ubuntu
```

This command:

1. Builds the Python backend into a Linux executable with PyInstaller.
2. Builds Electron Linux installers: AppImage and Debian package.

Installer outputs are created in:

```text
electron-gui/dist/
```

To install the generated Ubuntu `.deb` package on the current machine:

```bash
cd electron-gui
npm run install:ubuntu
```

If no `.deb` exists yet, the installer script builds one first. To install a specific package:

```bash
bash ./scripts/install-ubuntu.sh --deb ./dist/Cisco\ Config\ Comparison\ Tool-1.0.0-linux-amd64.deb
```

For Serial console access on Ubuntu, add your user to the `dialout` group during install:

```bash
bash ./scripts/install-ubuntu.sh --serial-permissions
```

Log out and back in after changing serial permissions.

To uninstall the Ubuntu package:

```bash
cd electron-gui
npm run uninstall:ubuntu
```

To also remove the current user's app settings/cache:

```bash
bash ./scripts/uninstall-ubuntu.sh --purge-user-data
```

### Build a Windows Installer (.exe)

You can build a one-click Windows installer that bundles both:

- The Electron desktop frontend
- The Flask/Python backend as a packaged executable

From the project root:

```powershell
cd electron-gui
npm install
npm run build:installer
```

This performs two steps automatically:

1. Builds `server.py` into `electron-gui/backend-dist/conf-comparison-server/conf-comparison-server.exe` using PyInstaller.
2. Runs Electron Builder to generate an NSIS installer.

Installer outputs are created in:

```text
electron-gui/dist/
```

Useful alternatives:

```powershell
npm run build:portable   # portable .exe
npm run build:windows    # both NSIS installer and portable
```

## Notes for Public Users

- Keep any real student data, device credentials, and grading records private.
- Do not commit generated logs, student outputs, credentials, or local virtual environments.
- The internal SSH-to-GitHub setup from the original README is only needed for project contributors with repository write access.
