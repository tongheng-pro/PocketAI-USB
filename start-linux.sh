#!/usr/bin/env bash
# Starts the PocketAI dashboard. Run with:  bash start-linux.sh
# (exFAT/NTFS drives may not keep the executable bit, so always invoke through bash.)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/linux-common.sh
. "$ROOT/scripts/linux-common.sh"

if [ "$(uname -m)" != "x86_64" ]; then
    pocketai_err "PocketAI USB needs a 64-bit Intel/AMD (x86_64) computer."
    exit 1
fi
pocketai_use_runtime || exit 1
pocketai_check_libs

"$PY" "$ROOT/app/pocketai.py" check || {
    pocketai_err "PocketAI cannot start. Fix the problem marked [!!] above."
    exit 1
}
echo
exec "$PY" "$ROOT/app/pocketai.py" serve --open
