# Automated Cisco Marking System

This project is part of the Final Year Project (FYP) to develop a **network configuration comparison and marking tool**.
It supports communication with Cisco switches/routers via **SSH or Serial (USB-C/USB-A - Serial)**, log extraction, and automated grading against customizable rubrics for teaching units **TNE10006** and **TNE20002**.

---

## SSH Setup for GitHub

This project uses SSH authentication for Git operations. Follow these steps to configure SSH and clone the repository:

### 1. Generate an SSH Key

**Mac / Ubuntu (Linux):**

```sh
ssh-keygen -t ed25519 -C "your_email@example.com"
```

Press Enter to accept the default file location (`~/.ssh/id_ed25519`).
Set a passphrase for extra security (optional).

**Windows (PowerShell or Git Bash):**

```sh
ssh-keygen -t ed25519 -C "your_email@example.com"
```

Follow the same steps as above.

### 2. Start the SSH Agent and Add the Key

**Mac / Ubuntu / Windows (Git Bash):**

```sh
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

**Windows (PowerShell):**

```sh
Start-Service ssh-agent
ssh-add ~\.ssh\id_ed25519
```

### 3. Add SSH Key to GitHub

Copy your public key:

```sh
cat ~/.ssh/id_ed25519.pub
```

(Windows: `type Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub`)

-   Go to GitHub → Settings → SSH and GPG keys
-   Click **New SSH key**, paste your public key, and save.

### 4. Test SSH Connection

```sh
ssh -T git@github.com
```

Expected output:

> Hi <username>! You've successfully authenticated, but GitHub does not provide shell access.

### 5. Clone the Repository

Use the SSH URL (not HTTPS):

```sh
git clone git@github.com:samuelleey0/conf-comparison-tool.git
```

### 6. Common Git Commands

Check remote URL:

```sh
git remote -v
```

Switch from HTTPS to SSH if already cloned:

```sh
git remote set-url origin git@github.com:samuelleey0/conf-comparison-tool.git
```

---

## Project Setup

### 1. Clone the Repository

```sh
git clone git@github.com:samuelleey0/conf-comparison-tool.git
cd conf-comparison-tool
```

### 2. Create Virtual Environment (per device/OS)

Each team member should create their own virtual environment locally. The venv is not shared or committed to GitHub. For consistency, use the name `fyp-venv`:

```sh
python3 -m venv fyp-venv
source fyp-venv/bin/activate
```

-   On Windows, activate with: `fyp-venv\Scripts\activate`

> **Note:** The `fyp-venv/` folder is listed in `.gitignore` and will not be tracked by Git. Each device/OS must create its own venv.

### 3. Install Dependencies

```sh
pip install -r requirements.txt
```

### 4. Verify Installed Packages

```sh
pip list
```

---

## Setup on Ubuntu with PCIe Serial Port

This section guides you through preparing an Ubuntu desktop with a **PCIe serial interface** to run the scripts.

### Hardware & Driver Setup

1. **Install the PCIe Serial Card** properly on the desktop.
2. Confirm the system detects it:
    ```sh
    lspci | grep -i serial
    ```
    You should see something like:
    ```
    0000:03:00.0 Serial controller: Oxford Semiconductor Ltd OXPCIe952 Dual Serial Port
    ```
3. Check if the serial port device is created:
    ```sh
    dmesg | grep tty
    ```
    or
    ```sh
    ls /dev/ttyS* /dev/ttyUSB* 2>/dev/null
    ```
    Typical outputs: `/dev/ttyS4`, `/dev/ttyS5`, etc.

If the serial ports do not appear, load the driver manually:

```sh
sudo modprobe 8250_pci
```

---

### Permissions

Grant the current user permission to access serial devices:

```sh
sudo usermod -aG dialout $USER
```

Then **log out and log back in** before continuing.

---

### Identify Serial Port

After reboot or device replug:

```sh
dmesg | grep tty
```

Example result:

```
[  5.321877] serial8250: ttyS4 at I/O 0x3020 (irq = 17) is a 16550A
```

Use the correct port in your Python script, for example:

```python
port = "/dev/ttyS4"
```

---

### 🐍 4️⃣ Python Environment

Ensure your Python environment (virtual or system) includes the required packages:

```sh
pip install -r requirements.txt
```

```sh
python3 main.py
```

---

### Test Serial Communication

Verify the serial link using **Minicom** before running the automation script:

```sh
sudo apt install minicom
sudo minicom -D /dev/ttyS4 -b 9600
```

Default serial settings for Cisco devices:

-   **Baudrate:** 9600
-   **Data bits:** 8
-   **Parity:** None
-   **Stop bits:** 1
-   **Flow control:** None

---
