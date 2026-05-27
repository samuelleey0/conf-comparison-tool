#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
electron_gui_dir="$(cd "$script_dir/.." && pwd)"
dist_dir="$electron_gui_dir/dist"

purge_user_data=0
package_name=""

usage() {
  cat <<'EOF'
Usage: bash electron-gui/scripts/uninstall-ubuntu.sh [options]

Options:
  --package NAME            Remove a specific Debian package name.
  --purge-user-data         Also remove this user's app settings/cache under ~/.config.
  -h, --help                Show this help.

Default behavior:
  - Tries to read the package name from the newest .deb in electron-gui/dist.
  - Falls back to common package names used by this project.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --package)
      package_name="${2:-}"
      if [[ -z "$package_name" ]]; then
        echo "[uninstall] --package requires a name."
        exit 1
      fi
      shift 2
      ;;
    --purge-user-data)
      purge_user_data=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[uninstall] Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[uninstall] This uninstaller is for Ubuntu/Linux only."
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "[uninstall] apt-get was not found. This script is intended for Ubuntu/Debian systems."
  exit 1
fi

if [[ -z "$package_name" ]]; then
  latest_deb="$(find "$dist_dir" -maxdepth 1 -type f -name "*.deb" -printf "%T@ %p\n" 2>/dev/null | sort -nr | awk 'NR==1 {print $2}')"
  if [[ -n "${latest_deb:-}" ]] && command -v dpkg-deb >/dev/null 2>&1; then
    package_name="$(dpkg-deb -f "$latest_deb" Package 2>/dev/null || true)"
  fi
fi

candidate_packages=()
if [[ -n "$package_name" ]]; then
  candidate_packages+=("$package_name")
fi
candidate_packages+=("electron-gui" "cisco-config-comparison-tool" "cisco-config-tool")

installed_package=""
for candidate in "${candidate_packages[@]}"; do
  if dpkg-query -W -f='${Status}' "$candidate" 2>/dev/null | grep -q "install ok installed"; then
    installed_package="$candidate"
    break
  fi
done

if [[ -z "$installed_package" ]]; then
  echo "[uninstall] App package is not installed, or its package name was not recognized."
  echo "[uninstall] Use --package NAME if you installed it under a different Debian package name."
else
  echo "[uninstall] Removing package: $installed_package"
  sudo apt-get remove -y "$installed_package"
fi

if [[ "$purge_user_data" -eq 1 ]]; then
  echo "[uninstall] Removing current user's app settings/cache..."
  rm -rf \
    "$HOME/.config/Cisco Config Comparison Tool" \
    "$HOME/.config/cisco-config-comparison-tool" \
    "$HOME/.cache/Cisco Config Comparison Tool" \
    "$HOME/.cache/cisco-config-comparison-tool"
fi

echo "[uninstall] Uninstall complete."
