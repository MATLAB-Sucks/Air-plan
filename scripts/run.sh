#!/usr/bin/env bash
# Launch Aircraft Foundry from source (after scripts/setup_macos.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec python -m aircraft_foundry "$@"
