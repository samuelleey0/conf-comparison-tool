#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
electron_gui_dir="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$electron_gui_dir/.." && pwd)"

venv_python="$repo_root/fyp-venv/bin/python"
if [[ -x "$venv_python" ]]; then
  python_exe="$venv_python"
elif command -v python3 >/dev/null 2>&1; then
  python_exe="$(command -v python3)"
else
  echo "[installer] python3 not found. Install Python 3.10+ or create fyp-venv first."
  exit 1
fi

server_script="$repo_root/server.py"
backend_dist_root="$electron_gui_dir/backend-dist"
backend_build_root="$electron_gui_dir/backend-build"
backend_name="conf-comparison-server"

echo "[installer] Using Python executable: $python_exe"

"$python_exe" -m pip install --upgrade pyinstaller

rm -rf "$backend_dist_root/$backend_name"
mkdir -p "$backend_dist_root" "$backend_build_root"

echo "[installer] Building backend executable with PyInstaller..."
"$python_exe" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name "$backend_name" \
  --distpath "$backend_dist_root" \
  --workpath "$backend_build_root" \
  --specpath "$backend_build_root" \
  --add-data "$repo_root/config:config" \
  --add-data "$repo_root/comparison_engine/templates:comparison_engine/templates" \
  --add-data "$repo_root/schemes:schemes" \
  --add-data "$repo_root/rubrics:rubrics" \
  "$server_script"

built_bin="$backend_dist_root/$backend_name/$backend_name"
if [[ ! -x "$built_bin" ]]; then
  echo "[installer] Backend executable was not created at expected path: $built_bin"
  exit 1
fi

echo "[installer] Backend build complete: $built_bin"
