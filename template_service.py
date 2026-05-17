"""Template listing, manifest, import, upload, and baseline helpers."""

import json
import os
import shutil
from pathlib import Path

from comparison_engine.parser import normalize_parsed_config
from comparison_wrapper import (
    handle_template_upload,
    import_logs_folder_strict,
    import_template_from_logs_dir,
    save_template_setup,
)
from results_service import _canonical_cli_command, _normalize_text, _safe_resolve_child


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "comparison_engine" / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def list_templates():
    if not TEMPLATES_DIR.is_dir():
        return []
    return [entry.name for entry in sorted(TEMPLATES_DIR.iterdir()) if entry.is_dir()]


def template_manifest_path(template_name: str) -> Path:
    return TEMPLATES_DIR / template_name / "template_manifest.json"


def load_template_manifest(template_name: str):
    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not target or not target.exists():
        return None

    manifest_path = target / "template_manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "r") as handle:
                manifest = json.load(handle) or {}
            manifest.setdefault("template_name", template_name)
            manifest.setdefault("devices_meta", {})
            manifest["has_baseline"] = bool(manifest.get("has_baseline"))
            return manifest
        except Exception:
            pass

    devices_meta = {}
    for hostname_dir in sorted(target.iterdir()):
        if not hostname_dir.is_dir():
            continue
        logs_manifest = hostname_dir / "logs.json"
        commands = []
        if logs_manifest.exists():
            try:
                with open(logs_manifest, "r") as handle:
                    manifest = json.load(handle) or {}
                for name in manifest.get("logs", []):
                    base = os.path.splitext(name)[0]
                    commands.append(base.replace("_", " "))
            except Exception:
                commands = []
        if commands:
            devices_meta[hostname_dir.name] = commands

    has_baseline = False
    for hostname_dir in sorted(target.iterdir()):
        if hostname_dir.is_dir() and (hostname_dir / "config.json").exists():
            has_baseline = True
            break

    return {
        "template_name": template_name,
        "devices_meta": devices_meta,
        "has_baseline": has_baseline,
    }


def _template_command_key(command: str) -> str:
    return _normalize_text(command)


def get_template_details(template_name: str):
    if not template_name:
        raise ValueError("Missing template name.")

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not target or not target.exists():
        raise FileNotFoundError("Template not found.")

    template_manifest = load_template_manifest(template_name) or {}
    devices_meta = dict(template_manifest.get("devices_meta") or {})
    logs_by_command = {}
    has_baseline = bool(template_manifest.get("has_baseline"))
    for hostname_dir in sorted(target.iterdir()):
        if not hostname_dir.is_dir():
            continue
        logs_manifest = hostname_dir / "logs.json"
        commands = [
            _canonical_cli_command(command)
            for command in list(devices_meta.get(hostname_dir.name) or [])
        ]
        command_keys = {_template_command_key(command) for command in commands}
        if logs_manifest.exists():
            try:
                with open(logs_manifest, "r") as handle:
                    logs_payload = json.load(handle) or {}
                for name in logs_payload.get("logs", []):
                    base = os.path.splitext(name)[0]
                    cmd = _canonical_cli_command(base.replace("_", " "))
                    cmd_key = _template_command_key(cmd)
                    if cmd_key not in command_keys:
                        commands.append(cmd)
                        command_keys.add(cmd_key)
                    else:
                        cmd = next(
                            (
                                existing
                                for existing in commands
                                if _template_command_key(existing) == cmd_key
                            ),
                            cmd,
                        )
                    logs_by_command.setdefault(hostname_dir.name, {})[cmd] = name
            except Exception:
                pass
        if (hostname_dir / "config.json").exists():
            has_baseline = True
        if commands:
            devices_meta[hostname_dir.name] = commands

    return {
        "template": template_name,
        "devices_meta": devices_meta,
        "logs_by_command": logs_by_command,
        "has_baseline": has_baseline,
    }


def clean_devices_meta(devices_meta):
    if not isinstance(devices_meta, dict) or not devices_meta:
        raise ValueError("No devices provided.")

    cleaned_devices = {}
    seen = set()
    for hostname, commands in devices_meta.items():
        safe_hostname = str(hostname or "").strip()
        if not safe_hostname:
            raise ValueError("All devices must have a hostname.")
        if safe_hostname.lower() in seen:
            raise ValueError(f'Duplicate hostname "{safe_hostname}".')
        seen.add(safe_hostname.lower())
        if not isinstance(commands, list) or not commands:
            raise ValueError(f'Device "{safe_hostname}" has no commands.')
        cleaned_commands = [str(cmd).strip() for cmd in commands if str(cmd).strip()]
        if not cleaned_commands:
            raise ValueError(f'Device "{safe_hostname}" has no commands.')
        cleaned_devices[safe_hostname] = cleaned_commands
    return cleaned_devices


def save_template_structure(template_name, devices_meta, source_template_name=""):
    template_name = str(template_name or "").strip()
    source_template_name = str(source_template_name or "").strip()
    if not template_name:
        raise ValueError("Missing template name.")
    cleaned_devices = clean_devices_meta(devices_meta)
    return save_template_setup(
        str(BASE_DIR),
        template_name,
        cleaned_devices,
        source_template_name=source_template_name,
    )


def import_template_logs_folder(
    template_name, source_dir, source_template_name="", strict=False, devices_meta=None
):
    template_name = str(template_name or "").strip()
    source_template_name = str(source_template_name or "").strip()
    strict = bool(strict)
    if not template_name:
        raise ValueError("Missing template name.")
    if not source_dir or not os.path.isdir(source_dir):
        raise ValueError("Selected logs folder was not found.")

    cleaned_devices = None
    if strict:
        cleaned_devices = clean_devices_meta(devices_meta or {})

    if strict:
        return import_logs_folder_strict(
            str(BASE_DIR),
            template_name,
            source_dir,
            cleaned_devices,
            source_template_name=source_template_name,
        )
    return import_template_from_logs_dir(
        str(BASE_DIR),
        template_name,
        source_dir,
        source_template_name=source_template_name,
    )


def delete_templates(name=None, delete_all=False):
    if delete_all:
        for entry in TEMPLATES_DIR.iterdir():
            if entry.is_dir():
                try:
                    shutil.rmtree(entry)
                except Exception:
                    pass
        return "All templates deleted"

    if not name:
        raise ValueError("Missing template name.")

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / name)
    if not target or not target.exists():
        raise FileNotFoundError("Template not found.")

    shutil.rmtree(target)
    return f"Template '{name}' deleted"


def load_template_configs(template_name: str):
    template_dir = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not template_dir or not template_dir.is_dir():
        return None

    template_configs = {}
    for host_dir in sorted(template_dir.iterdir()):
        if not host_dir.is_dir():
            continue
        config_path = host_dir / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r") as handle:
                data = json.load(handle) or {}
            template_configs[host_dir.name] = normalize_parsed_config(data)
        except Exception:
            continue

    return template_configs


def template_has_baseline(template_name: str) -> bool:
    manifest = load_template_manifest(template_name) or {}
    if manifest.get("has_baseline"):
        return True
    template_configs = load_template_configs(template_name) or {}
    return bool(template_configs)


def handle_upload(files, form_data):
    return handle_template_upload(files, form_data, str(BASE_DIR))
