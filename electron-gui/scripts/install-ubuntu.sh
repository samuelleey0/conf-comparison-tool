#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
electron_gui_dir="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$electron_gui_dir/.." && pwd)"
dist_dir="$electron_gui_dir/dist"

build_if_missing=1
deb_path=""
serial_permissions=0

usage() {
  cat <<'EOF'
Usage: bash electron-gui/scripts/install-ubuntu.sh [options]

Options:
  --deb PATH                Install a specific .deb package.
  --no-build                Do not build if no .deb package is found.
  --serial-permissions      Add the current user to the dialout group for serial console access.
  -h, --help                Show this help.

Default behavior:
  - Installs the newest .deb in electron-gui/dist.
  - If no .deb exists, builds the Ubuntu installer first.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deb)
      deb_path="${2:-}"
      if [[ -z "$deb_path" ]]; then
        echo "[install] --deb requires a path."
        exit 1
      fi
      shift 2
      ;;
    --no-build)
      build_if_missing=0
      shift
      ;;
    --serial-permissions)
      serial_permissions=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[install] Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[install] This installer is for Ubuntu/Linux only."
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "[install] apt-get was not found. This script is intended for Ubuntu/Debian systems."
  exit 1
fi

if [[ -z "$deb_path" ]]; then
  deb_path="$(find "$dist_dir" -maxdepth 1 -type f -name "*.deb" -printf "%T@ %p\n" 2>/dev/null | sort -nr | awk 'NR==1 {print $2}')"
fi

if [[ -z "$deb_path" && "$build_if_missing" -eq 1 ]]; then
  echo "[install] No .deb found in $dist_dir. Building Ubuntu installer..."
  sudo apt-get update
  sudo apt-get install -y build-essential python3 python3-venv python3-pip dpkg fakeroot rpm
  cd "$electron_gui_dir"
  npm install
  npm run build:installer:ubuntu
  deb_path="$(find "$dist_dir" -maxdepth 1 -type f -name "*.deb" -printf "%T@ %p\n" | sort -nr | awk 'NR==1 {print $2}')"
fi

if [[ -z "$deb_path" || ! -f "$deb_path" ]]; then
  echo "[install] No .deb package found. Build first with: cd electron-gui && npm run build:installer:ubuntu"
  exit 1
fi

echo "[install] Installing package: $deb_path"
sudo apt-get install -y "$deb_path"

if [[ "$serial_permissions" -eq 1 ]]; then
  install_user="${SUDO_USER:-$USER}"
  if getent group dialout >/dev/null 2>&1; then
    echo "[install] Adding $install_user to dialout for serial device access..."
    sudo usermod -aG dialout "$install_user"
    echo "[install] Log out and back in before using serial console ports."
  else
    echo "[install] Group 'dialout' does not exist on this system; skipping serial permission setup."
  fi
fi

echo "[install] Installation complete."
