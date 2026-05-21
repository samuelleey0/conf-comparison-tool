"""
Persistent command-list manager.

This script stores the Cisco show commands used during collection in
config/commands.json. The Flask backend uses load/save helpers for the GUI,
while the CLI test flow can use the interactive menu functions.
"""
# command_manager.py
import json
import os

BASE_DIR = os.path.dirname(__file__)
CONFIG_DIR = os.path.join(BASE_DIR, "config")
os.makedirs(CONFIG_DIR, exist_ok=True)  # Ensure the directory exists
COMMANDS_PATH = os.path.join(CONFIG_DIR, "commands.json")

DEFAULT_COMMANDS = [
    "show ip interface brief",
    "show running-config",
    "show spanning-tree",
    "show vlan brief",
    "show ip route",
    "show etherchannel summary",
    "show ip eigrp neighbor",
    "show ip eigrp topology",
    "show ip eigrp interfaces",
    "show ip ospf neighbor",
    "show ip ospf database",
    "show ip ospf interface",
    "show ip rip database",
    "show ip route static",
]


def load_commands():
    """
    Load the saved Cisco command list, creating or repairing it with defaults.

    Used by server.py for the Electron command editor and by command_menu() for
    the older CLI workflow.
    """
    if not os.path.exists(COMMANDS_PATH):
        save_commands(DEFAULT_COMMANDS)
        return list(DEFAULT_COMMANDS)
    try:
        with open(COMMANDS_PATH, "r") as f:
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError
            merged = list(data)
            changed = False
            for cmd in DEFAULT_COMMANDS:
                if cmd not in merged:
                    merged.append(cmd)
                    changed = True
            if changed:
                save_commands(merged)
            return merged
    except Exception:
        print("[!] commands.json broken, recreating default.")
        save_commands(DEFAULT_COMMANDS)
        return list(DEFAULT_COMMANDS)


def save_commands(cmds):
    """Persist the command list to config/commands.json."""
    with open(COMMANDS_PATH, "w") as f:
        json.dump(cmds, f, indent=4)


def display(cmds):
    """Print a numbered command list for the interactive CLI menu."""
    print("\n--- Command List ---")
    for i, c in enumerate(cmds, 1):
        print(f"{i}. {c}")
    print("--------------------")


def select(cmds):
    """Ask the CLI user which numbered commands should run and return them."""
    display(cmds)
    choice = input("Enter numbers to run (e.g. 1,3): ").strip()
    selected = []
    for c in choice.split(","):
        try:
            idx = int(c.strip()) - 1
            if 0 <= idx < len(cmds):
                selected.append(cmds[idx])
        except:
            pass
    return selected


def add(cmds):
    """Prompt for a new CLI command and save it when it is not already present."""
    new = input("Enter new command: ").strip()
    if new and new not in cmds:
        cmds.append(new)
        save_commands(cmds)
        print(f"Added: {new}")


def remove(cmds):
    """Prompt for a command number to remove from the saved CLI command list."""
    display(cmds)
    num = input("Enter number to remove: ").strip()
    try:
        idx = int(num) - 1
        removed = cmds.pop(idx)
        save_commands(cmds)
        print(f"Removed: {removed}")
    except:
        print("Invalid choice.")


def command_menu():
    """Run the interactive command-management menu used by main.py testing."""
    cmds = load_commands()
    while True:
        print("\n=== Command Menu ===")
        print("1. View commands")
        print("2. Select commands")
        print("3. Add new command")
        print("4. Remove command")
        print("5. Exit")
        opt = input("Choose 1-5: ").strip()
        if opt == "1":
            display(cmds)
        elif opt == "2":
            sel = select(cmds)
            return sel
        elif opt == "3":
            add(cmds)
        elif opt == "4":
            remove(cmds)
        elif opt == "5":
            return []
        else:
            print("Invalid.")


if __name__ == "__main__":
    result = command_menu()
    print("Selected:", result)
