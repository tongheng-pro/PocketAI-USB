#!/usr/bin/env bash
# One-time ONLINE setup for Ubuntu x64. Run with:  bash setup-linux.sh
# Downloads a portable Python (python-build-standalone, ~35 MB), then runs
# scripts/setup.py, which fetches llama.cpp + Python packages and asks before
# downloading any AI model. Nothing is installed system-wide; no sudo needed.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/linux-common.sh
. "$ROOT/scripts/linux-common.sh"

PYURL="https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.12.15%2B20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PYSHA="6d7521138fd33afddc1ccd08abb007d9a2829e38117c2576f9efdc0ec73ce772"
PYDIR="$ROOT/runtime/linux/python"

if [ "$(uname -m)" != "x86_64" ]; then
    pocketai_err "PocketAI USB needs a 64-bit Intel/AMD (x86_64) computer."
    exit 1
fi
for tool in tar sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || { pocketai_err "'$tool' is required"; exit 1; }
done

if [ ! -f "$PYDIR/bin/python3.12" ]; then
    echo "== Downloading portable Python 3.12 (about 35 MB)"
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --progress-bar -o "$TMP/python.tar.gz" "$PYURL" || { pocketai_err "download failed"; exit 1; }
    elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress -O "$TMP/python.tar.gz" "$PYURL" || { pocketai_err "download failed"; exit 1; }
    else
        pocketai_err "curl or wget is required (sudo apt install curl)"; exit 1
    fi
    echo "$PYSHA  $TMP/python.tar.gz" | sha256sum -c --quiet - || { pocketai_err "checksum mismatch"; exit 1; }
    echo "   verified SHA-256"
    # Extract on the local disk first. exFAT/FAT cannot store symlinks, and the ones in
    # this archive are only aliases (bin/python3 -> python3.12), so drop them before copying.
    tar -xzf "$TMP/python.tar.gz" -C "$TMP" || { pocketai_err "extract failed"; exit 1; }
    find "$TMP/python" -type l -delete
    mkdir -p "$PYDIR"
    cp -r "$TMP/python/." "$PYDIR/" || { pocketai_err "copy to the USB failed"; exit 1; }
fi

pocketai_use_runtime || exit 1
"$PY" "$ROOT/scripts/setup.py" "$@"
status=$?
# The runtime may have changed (packages/llama.cpp); re-check libraries.
pocketai_use_runtime >/dev/null 2>&1 && pocketai_check_libs
exit $status
