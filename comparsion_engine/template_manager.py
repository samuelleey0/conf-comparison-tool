import json
import os

from parser import detect_command_type
from parser import normalize_parsed_config
from parser import parse_device_logs_with_report

DEFAULT_HOSTNAMES = ["R1", "R2", "S1"]
SHOW_RUN_TOKENS = [
    "showrun",
    "show_run",
    "show-run",
    "show run",
    "shrun",
    "sh_run",
    "sh run",
    "show_running-config",
    "show running-config",
]


def normalize_text(value):
    """Normalize text for flexible filename matching."""
    lowered = value.lower()
    for char in ["_", "-", ".", "(", ")", "[", "]"]:
        lowered = lowered.replace(char, " ")
    return " ".join(lowered.split())


def is_show_run_filename(filename):
    """Check whether a filename looks like a show run output."""
    normalized = normalize_text(filename)
    return any(token in normalized for token in SHOW_RUN_TOKENS)


def choose_show_run_file(saved_log_paths):
    """Choose show run file from uploaded logs, prompting user only when needed."""
    show_run_candidates = [
        path for path in saved_log_paths if is_show_run_filename(os.path.basename(path))
    ]

    if len(show_run_candidates) == 1:
        return show_run_candidates[0]

    if len(show_run_candidates) > 1:
        # Prefer exact-looking names first when multiple files match.
        def score(path):
            name = normalize_text(os.path.basename(path))
            if "show running config" in name:
                return 0
            if "show run" in name:
                return 1
            if "showrun" in name:
                return 2
            if "sh run" in name:
                return 3
            return 4

        show_run_candidates.sort(key=score)
        return show_run_candidates[0]

    print("No show run-like filename detected among uploaded logs.")
    print("Select which uploaded file should be parsed as show run:")
    for idx, path in enumerate(saved_log_paths, 1):
        print(f"{idx}. {os.path.basename(path)}")

    choice = input("Enter number: ").strip()
    try:
        choice_index = int(choice)
        if 1 <= choice_index <= len(saved_log_paths):
            return saved_log_paths[choice_index - 1]
    except ValueError:
        pass

    print("Invalid choice. Using the first uploaded file as fallback.")
    return saved_log_paths[0]


def list_template_names(template_folder):
    """List available template directories."""
    templates = []
    for entry in sorted(os.listdir(template_folder)):
        full_path = os.path.join(template_folder, entry)
        if os.path.isdir(full_path):
            templates.append(entry)
    return templates


def list_hostnames_in_template(template_folder, template_name):
    """List all hostnames (device folders) in a template."""
    template_dir = os.path.join(template_folder, template_name)
    hostnames = {}

    if not os.path.isdir(template_dir):
        return hostnames

    for entry in sorted(os.listdir(template_dir)):
        hostname_dir = os.path.join(template_dir, entry)
        if os.path.isdir(hostname_dir):
            config_file = os.path.join(hostname_dir, "config.json")
            if os.path.exists(config_file):
                hostnames[entry] = config_file

    return hostnames


def upload_hostname_template(template_folder, template_name, hostname):
    """Upload one or more logs for a hostname and parse show run for comparison."""
    log_paths_input = input(
        f"Enter path(s) to {hostname} log files (comma-separated): "
    ).strip()
    teacher_file_paths = [
        path.strip() for path in log_paths_input.split(",") if path.strip()
    ]
    if not teacher_file_paths:
        print("Error: no files provided.")
        return None

    for teacher_file_path in teacher_file_paths:
        if not os.path.exists(teacher_file_path):
            print(f"Error: file '{teacher_file_path}' not found.")
            return None

    hostname_dir = os.path.join(template_folder, template_name, hostname, "logs")
    os.makedirs(hostname_dir, exist_ok=True)

    # Preserve original uploaded filenames for command-based matching.
    saved_log_paths = []
    for teacher_file_path in teacher_file_paths:
        template_filename = os.path.basename(teacher_file_path)
        template_txt_path = os.path.join(hostname_dir, template_filename)
        with open(teacher_file_path, "r") as source_file:
            template_content = source_file.read()
        with open(template_txt_path, "w") as target_file:
            target_file.write(template_content)
        saved_log_paths.append(template_txt_path)

    show_run_path = choose_show_run_file(saved_log_paths)

    # Parse and save JSON config (show run + verification command outputs).
    # Logs containing CLI command errors are skipped and reported to teacher.
    template_config, skipped_logs = parse_device_logs_with_report(saved_log_paths)
    config_json_path = os.path.join(
        template_folder, template_name, hostname, "config.json"
    )
    with open(config_json_path, "w") as target_file:
        json.dump(template_config, target_file, indent=4)

    # Save metadata so future comparisons can align logs by filename.
    skipped_files = {os.path.basename(item.get("file", "")) for item in skipped_logs}
    command_map = {}
    required_command_types = []
    for path in saved_log_paths:
        filename = os.path.basename(path)
        if filename in skipped_files:
            command_map[filename] = None
            continue
        command_type = detect_command_type(path)
        command_map[filename] = command_type
        if command_type and command_type not in required_command_types:
            required_command_types.append(command_type)

    manifest_path = os.path.join(template_folder, template_name, hostname, "logs.json")
    with open(manifest_path, "w") as manifest_file:
        json.dump(
            {
                "hostname": hostname,
                "show_run_file": os.path.basename(show_run_path),
                "logs": [os.path.basename(path) for path in saved_log_paths],
                "command_types": command_map,
                "required_command_types": required_command_types,
                "skipped_logs": skipped_logs,
            },
            manifest_file,
            indent=4,
        )

    if skipped_logs:
        print("Warning: some uploaded logs were skipped due to command errors:")
        for item in skipped_logs:
            skipped_name = os.path.basename(item.get("file", ""))
            detected_command = item.get("detected_command") or "unknown"
            reason = item.get("reason") or "command failed"
            print(f"- {skipped_name} ({detected_command}): {reason}")

    print(
        f"Saved template for {hostname} with {len(saved_log_paths)} log(s). "
        f"Show run source: {os.path.basename(show_run_path)}"
    )
    return template_config


def load_hostname_template(template_folder, template_name, hostname):
    """Load a hostname config JSON from template."""
    config_json_path = os.path.join(
        template_folder, template_name, hostname, "config.json"
    )
    if not os.path.exists(config_json_path):
        return None

    with open(config_json_path, "r") as config_file:
        loaded = json.load(config_file)
    return normalize_parsed_config(loaded)


def setup_templates(template_folder):
    """Load or upload teacher templates per hostname."""
    print("\n=== Template Setup ===")

    template_names = list_template_names(template_folder)
    if template_names:
        print("\nAvailable templates:")
        for idx, name in enumerate(template_names, 1):
            print(f"{idx}. {name}")
        print(f"{len(template_names) + 1}. Create new template")

        choice = input("Select template (number): ").strip()
        try:
            choice_index = int(choice)
            if 1 <= choice_index <= len(template_names):
                template_name = template_names[choice_index - 1]
                loaded = {}
                hostnames = list_hostnames_in_template(template_folder, template_name)
                for hostname in hostnames:
                    loaded[hostname] = load_hostname_template(
                        template_folder, template_name, hostname
                    )
                print(
                    f"Loaded template '{template_name}' with hostnames: {', '.join(sorted(loaded.keys()))}"
                )
                return template_name, loaded
        except ValueError:
            pass

    template_name = input("Enter new template name (e.g., quiz1): ").strip()
    if not template_name:
        template_name = "default"

    custom_hostnames = input(
        f"Enter hostnames separated by commas (default: {', '.join(DEFAULT_HOSTNAMES)}): "
    ).strip()
    if custom_hostnames:
        hostname_list = [h.strip() for h in custom_hostnames.split(",") if h.strip()]
    else:
        hostname_list = DEFAULT_HOSTNAMES

    templates = {}
    for hostname in hostname_list:
        print(f"\nUploading template for {hostname}...")
        template_config = upload_hostname_template(
            template_folder, template_name, hostname
        )
        if template_config is not None:
            templates[hostname] = template_config

    print(f"Template '{template_name}' ready with {len(templates)} hostnames.")
    return template_name, templates
