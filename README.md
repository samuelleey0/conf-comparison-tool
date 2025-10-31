⸻

Automated Cisco Marking System

This project is part of the Final Year Project (FYP) to develop a network configuration comparison and marking tool.
It supports communication with Cisco switches/routers via SSH or Serial (USB-C/USB-A - Serial), log extraction, and automated grading against customizable rubrics for teaching units TNE10006 and TNE20002.

⸻

SSH Setup for GitHub

This project uses SSH authentication for Git operations. Follow these steps to configure SSH and clone the repository:

1. Generate an SSH Key

Mac / Ubuntu (Linux):

ssh-keygen -t ed25519 -C "your_email@example.com"

Press Enter to accept the default file location (~/.ssh/id_ed25519).
Set a passphrase for extra security (optional).

Windows (PowerShell or Git Bash):

ssh-keygen -t ed25519 -C "your_email@example.com"

Follow the same steps as above.

2. Start the SSH Agent and Add the Key

Mac / Ubuntu / Windows (Git Bash):

eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

Windows (PowerShell):

Start-Service ssh-agent
ssh-add ~\.ssh\id_ed25519

3. Add SSH Key to GitHub

Copy your public key:

cat ~/.ssh/id_ed25519.pub

(Windows: type Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub)
	•	Go to GitHub → Settings → SSH and GPG keys
	•	Click New SSH key, paste your public key, and save.

4. Test SSH Connection

ssh -T git@github.com

Expected output:

Hi ! You’ve successfully authenticated, but GitHub does not provide shell access.

5. Clone the Repository

git clone git@github.com:samuelleey0/conf-comparison-tool.git

6. Common Git Commands

Check remote URL:

git remote -v

Switch from HTTPS to SSH if already cloned:

git remote set-url origin git@github.com:samuelleey0/conf-comparison-tool.git


⸻

Project Setup

1. Clone the Repository

git clone git@github.com:samuelleey0/conf-comparison-tool.git
cd conf-comparison-tool

2. Create Virtual Environment (per device/OS)

Each team member should create their own virtual environment locally.
The venv is not shared or committed to GitHub. For consistency, use the name fyp-venv:

python3 -m venv fyp-venv
source fyp-venv/bin/activate

	•	On Windows:
fyp-venv\Scripts\activate

Note: The fyp-venv/ folder is listed in .gitignore and will not be tracked by Git.

3. Install Dependencies

pip install -r requirements.txt

4. Verify Installed Packages

pip list


⸻

Setup on Ubuntu with PCIe Serial Port

This section guides you through preparing an Ubuntu desktop with a PCIe serial interface to run the scripts.

Hardware & Driver Setup
	1.	Install the PCIe Serial Card properly on the desktop.
	2.	Confirm the system detects it:

lspci | grep -i serial

Example:

0000:03:00.0 Serial controller: Oxford Semiconductor Ltd OXPCIe952 Dual Serial Port


	3.	Check for serial device:

dmesg | grep tty

or

ls /dev/ttyS* /dev/ttyUSB* 2>/dev/null

Typical outputs: /dev/ttyS4, /dev/ttyUSB0

If missing, load the driver manually:

sudo modprobe 8250_pci


⸻

Permissions

Grant serial access to your user:

sudo usermod -aG dialout $USER

Then log out and log back in.

⸻

Identify Serial Port

After reboot or device replug:

dmesg | grep tty

Example:

[  5.321877] serial8250: ttyS4 at I/O 0x3020 (irq = 17) is a 16550A

Use this in Python:

port = "/dev/ttyS4"


⸻

🐍 4️⃣ Python Environment

Install dependencies again if needed:

pip install -r requirements.txt

Run the main CLI version:

python3 main.py


⸻

Test Serial Communication

sudo apt install minicom
sudo minicom -D /dev/ttyS4 -b 9600

Default Cisco settings:
	•	Baudrate: 9600
	•	Data bits: 8
	•	Parity: None
	•	Stop bits: 1
	•	Flow control: None

⸻

🖥️ Electron GUI Setup (for Visualization & Execution)

The project also includes a graphical interface built with Electron + Flask.
This GUI allows instructors to run configurations, view outputs, and store logs easily.

⸻

⚙️ Setup Guide — Running the Electron GUI

🪜 1. Install Required Software

🔹 Python 3.10+

python3 --version

If missing, install from python.org/downloads

🔹 Node.js + npm

node -v
npm -v

Install from nodejs.org (v18+ LTS recommended)

⸻

🪜 2. Create & Activate Python Virtual Environment

python3 -m venv fyp-venv
source fyp-venv/bin/activate     # macOS/Linux
# or:
fyp-venv\Scripts\activate        # Windows

Install Python dependencies:

pip install -r requirements.txt


⸻

🪜 3. Set Up the Electron GUI

Go inside the GUI folder:

cd electron-gui

Install Node dependencies:

npm install


⸻

🪜 4. Start the App

Launch the GUI and backend together:

npm start

✅ You should see:

[INFO] Spawning Flask using: /path/to/fyp-venv/bin/python
[INFO] Running server: /path/to/server.py
Flask server is live at http://127.0.0.1:5050

Then the Electron window will open automatically.

⸻

🧠 Notes
	•	Flask runs automatically inside Electron — no need to start it manually.
	•	Outputs are stored in:

~/Documents/<ExamName>/<SessionID>/<StudentID>



⸻