#!/usr/bin/env bash
# Create the project virtualenv reliably, even inside editors (e.g. Cursor's
# Linux AppImage) that export APPIMAGE/APPDIR. Python 3.13 reads APPIMAGE to
# resolve its base interpreter, which otherwise makes `python3 -m venv` copy the
# editor binary as the venv's python and fail with SIGTRAP. Clearing the
# variables for this command sidesteps that entirely.
set -euo pipefail

cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
  echo "Virtualenv already exists at .venv — activate it with: source .venv/bin/activate"
  exit 0
fi

# --system-site-packages reuses an existing global PyTorch/FAISS install if
# present, avoiding a multi-gigabyte reinstall. Harmless if nothing is installed.
env -u APPIMAGE -u APPDIR python3 -m venv --system-site-packages .venv

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Done. Activate the environment with:"
echo "  source .venv/bin/activate"
