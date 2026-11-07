#!/usr/bin/env bash
# Create a Python 3.11 virtual environment and install Aircraft Foundry from source.
# Works on Intel (x86_64) and Apple Silicon Macs, macOS 12 Monterey or later.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3.11}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.11 not found. Install it from https://www.python.org/downloads/macos/"
  echo "(the universal2 installer runs natively on Intel) or: brew install python@3.11"
  exit 1
fi
"$PY" -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version' \
  || { echo "Aircraft Foundry is pinned to Python 3.11"; exit 1; }

MACHINE="$("$PY" -c 'import platform; print(platform.machine())')"
case "$MACHINE" in
  x86_64) LOCK=requirements/lock-macos-intel-py311.txt ;;
  arm64)  LOCK=requirements/lock-macos-arm64-py311.txt ;;
  *) echo "Unsupported architecture: $MACHINE"; exit 1 ;;
esac
if [[ "$MACHINE" == "x86_64" && "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" == "1" ]]; then
  echo "Note: this Python runs under Rosetta on an Apple Silicon Mac; the Intel build will be installed."
fi
echo "Architecture: $MACHINE  ->  $LOCK"

"$PY" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
# --only-binary guarantees nothing is compiled from source (no Xcode toolchain needed)
python -m pip install --only-binary :all: -r "$LOCK"
python -m pip install --no-deps -e .
python -m pip install "pytest>=8"

echo
echo "Installed. Verify with:   source .venv/bin/activate && pytest"
echo "Run the app with:         ./scripts/run.sh"
