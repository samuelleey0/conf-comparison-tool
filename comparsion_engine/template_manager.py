import json
import os

from parser import parse_showrun

DEFAULT_DEVICES = ["R1", "R2", "S1"]


def list_template_profiles(template_folder):
    """List available template profile directories."""
    profiles = []
    for entry in sorted(os.listdir(template_folder)):
        full_path = os.path.join(template_folder, entry)
        if os.path.isdir(full_path):
            profiles.append(entry)
    return profiles


def list_templates_in_profile(template_folder, profile_name):
    """List device templates in a profile directory."""
    profile_dir = os.path.join(template_folder, profile_name)
    templates = {}

    if not os.path.isdir(profile_dir):
        return templates

    for file in os.listdir(profile_dir):
        if file.endswith(".json"):
            device_name = file[:-5]
            templates[device_name] = os.path.join(profile_dir, file)

    return templates


def upload_device_template(template_folder, profile_name, device_name):
    """Upload and parse a template show run for a specific device."""
    teacher_file_path = input(
        f"Enter path to teacher {device_name} show run file: "
    ).strip()

    if not os.path.exists(teacher_file_path):
        print(f"Error: file '{teacher_file_path}' not found.")
        return None

    profile_dir = os.path.join(template_folder, profile_name)
    os.makedirs(profile_dir, exist_ok=True)

    template_txt_path = os.path.join(profile_dir, f"{device_name}.txt")
    with open(teacher_file_path, "r") as source_file:
        template_content = source_file.read()
    with open(template_txt_path, "w") as target_file:
        target_file.write(template_content)

    template_config = parse_showrun(template_txt_path)
    template_json_path = os.path.join(profile_dir, f"{device_name}.json")
    with open(template_json_path, "w") as target_file:
        json.dump(template_config, target_file, indent=4)

    print(f"Saved template for {device_name} in profile '{profile_name}'.")
    return template_config


def load_device_template(template_folder, profile_name, device_name):
    """Load a device template JSON for a profile."""
    template_json_path = os.path.join(
        template_folder, profile_name, f"{device_name}.json"
    )
    if not os.path.exists(template_json_path):
        return None

    with open(template_json_path, "r") as template_file:
        return json.load(template_file)


def setup_templates(template_folder):
    """Load or upload teacher templates per device."""
    print("\n=== Template Setup ===")

    profiles = list_template_profiles(template_folder)
    if profiles:
        print("\nAvailable template profiles:")
        for idx, profile in enumerate(profiles, 1):
            print(f"{idx}. {profile}")
        print(f"{len(profiles) + 1}. Create new profile")

        choice = input("Select profile (number): ").strip()
        try:
            choice_index = int(choice)
            if 1 <= choice_index <= len(profiles):
                profile_name = profiles[choice_index - 1]
                loaded = {}
                for device_name in list_templates_in_profile(
                    template_folder, profile_name
                ):
                    loaded[device_name] = load_device_template(
                        template_folder, profile_name, device_name
                    )
                print(
                    "Loaded profile "
                    f"'{profile_name}' with devices: {', '.join(sorted(loaded.keys()))}"
                )
                return profile_name, loaded
        except ValueError:
            pass

    profile_name = input("Enter new template profile name (e.g., quiz1): ").strip()
    if not profile_name:
        profile_name = "default"

    custom_devices = input(
        f"Enter device names separated by commas (default: {', '.join(DEFAULT_DEVICES)}): "
    ).strip()
    if custom_devices:
        device_list = [
            device.strip() for device in custom_devices.split(",") if device.strip()
        ]
    else:
        device_list = DEFAULT_DEVICES

    templates = {}
    for device_name in device_list:
        print(f"\nUploading template for device {device_name}...")
        template_config = upload_device_template(
            template_folder, profile_name, device_name
        )
        if template_config is not None:
            templates[device_name] = template_config

    print(f"Template profile '{profile_name}' ready with {len(templates)} devices.")
    return profile_name, templates
