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

**Mac / Ubuntu:**

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

(Windows: `type ~\.ssh\id_ed25519.pub`)

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

### 5. Optional: Auto Switch Environment with `direnv`

Install `direnv`:

```sh
sudo apt install direnv   # Ubuntu
brew install direnv       # macOS
```

Add the following to your `~/.bashrc` (Ubuntu) or `~/.zshrc` (macOS):

```sh
eval "$(direnv hook bash)"   # for bash
eval "$(direnv hook zsh)"    # for zsh
```

To show the venv name in your prompt, you can add a custom prompt function (optional).

---
