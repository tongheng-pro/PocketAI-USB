#!/usr/bin/env bash
# Terminal AI assistant. Run with:  bash pocketai.sh
# Optional: alias pocketai='bash /media/$USER/DATA/PocketAI-USB/pocketai.sh'
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/linux-common.sh
. "$ROOT/scripts/linux-common.sh"
pocketai_use_runtime || exit 1
exec "$PY" "$ROOT/app/pocketai.py" "$@"
