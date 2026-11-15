#!/usr/bin/env bash
# Run the full test suite (including Home Assistant tests) on Linux / WSL.
# From Windows: wsl -d Ubuntu -- bash scripts/test-ha.sh [pytest args]
set -euo pipefail

cd "$(dirname "$0")/.."

# Keep the Linux venv outside the shared Windows folder.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/ev_charge_log}"
export UV_LINK_MODE=copy
UV="${UV:-$HOME/.local/bin/uv}"

"$UV" sync --quiet --group ha
"$UV" run --group ha pytest "$@"
