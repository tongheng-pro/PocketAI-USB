# Shared helpers for the Linux launchers. Sourced (never executed) so it needs no exec bit.
#
# pocketai_use_runtime sets PY, POCKETAI_RUNTIME_DIR and PYTHONPATH. If the USB is mounted
# without exec permission (common for some exFAT/NTFS mount options), the runtime is copied
# once to ~/.cache/pocketai and run from there. Models, chats and settings stay on the USB.

pocketai_err() { printf '[ERROR] %s\n' "$*" >&2; }

pocketai_can_exec() {
    "$1/python/bin/python3.12" -c 'import sys' >/dev/null 2>&1
}

pocketai_use_runtime() {
    local rt="$ROOT/runtime/linux"
    if [ ! -f "$rt/python/bin/python3.12" ]; then
        pocketai_err "The Linux runtime is missing. Run:  bash setup-linux.sh"
        return 1
    fi
    if ! pocketai_can_exec "$rt"; then
        chmod +x "$rt/python/bin/"* "$rt/llama/llama-server" 2>/dev/null || true
    fi
    if ! pocketai_can_exec "$rt"; then
        local stamp cache
        stamp=$(du -sb "$rt" 2>/dev/null | cut -f1)
        cache="${XDG_CACHE_HOME:-$HOME/.cache}/pocketai/runtime-$stamp"
        if [ ! -x "$cache/python/bin/python3.12" ]; then
            echo "The USB drive does not allow running programs (noexec mount)."
            echo "Copying the runtime once to $cache ..."
            mkdir -p "$cache" && cp -r "$rt/." "$cache/" || { pocketai_err "copy failed"; return 1; }
            chmod +x "$cache/python/bin/"* "$cache/llama/llama-server" 2>/dev/null || true
        fi
        rt="$cache"
        if ! pocketai_can_exec "$rt"; then
            pocketai_err "Cannot run the portable Python. See docs/TROUBLESHOOTING.md (Ubuntu)."
            return 1
        fi
    fi
    export POCKETAI_RUNTIME_DIR="$rt"
    export PYTHONPATH="$rt/site-packages:$ROOT/app"
    export PYTHONNOUSERSITE=1
    unset PYTHONHOME
    PY="$rt/python/bin/python3.12"
}

pocketai_check_libs() {
    local srv="$POCKETAI_RUNTIME_DIR/llama/llama-server"
    [ -f "$srv" ] || return 0
    command -v ldd >/dev/null 2>&1 || return 0
    local missing
    missing=$(LD_LIBRARY_PATH="$POCKETAI_RUNTIME_DIR/llama" ldd "$srv" 2>/dev/null | grep 'not found' || true)
    if [ -n "$missing" ]; then
        echo "[warn] llama-server is missing system libraries:"
        echo "$missing"
        echo "       Install them once:  sudo apt install libgomp1 libssl3"
        echo "       (Ubuntu 22.04 or newer is required; 20.04 has no OpenSSL 3.)"
    fi
}
