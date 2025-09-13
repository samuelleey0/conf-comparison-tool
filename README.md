# Automated Cisco Marking System

This project is part of the Final Year Project (FYP) to develop a **network configuration comparison and marking tool**.  
It supports communication with Cisco switches/routers via **SSH or Serial (USB-C/USB-A - Serial)**, log extraction, and automated grading against customizable rubrics for teaching units **TNE10006** and **TNE20002**.

### 1. Clone the Repository

```bash
git clone git@github.com:samuelleey0/conf-comparison-tool.git
cd conf-comparison-tool
```

### 2. Create Virtual Environment

On Mac (auto change virtual env) and Ubuntu Desktop (configure on ~/.bashrc manually using `direnv`)

```bash
python3 -m venv fyp-venv
source fyp-venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Verify List

```bash
pip list
```

### 5. Optional: Auto Switch Environment with `direnv`

Install `direnv`:

```bash
sudo apt install direnv #Ubuntu
brew install direnv #macOS
```

Then add the following to your `~/.bashrc` (Ubuntu) or `~/.zshrc` (macOS):

```bash
set_prompt() {
    if [ -n "$VIRTUAL_ENV" ]; then
        # Red color for (fyp-venv)
        venv="\[\033[01;31m\]($(basename $VIRTUAL_ENV)) \[\033[00m\]"
    else
        venv=""
    fi

    # Always use color for the rest of the prompt
    PS1="${venv}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "
}

PROMPT_COMMAND=set_prompt
...
eval "$(direnv hook bash)" || eval "($direnv hook zsh)"
```

This does two things:

-   Shows `(fyp-venv)` in red when your virtual environment is active.
-   Enables direnv to automatically activate/deactivate your venv when entering or leaving the project folder.
