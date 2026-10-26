"""Paths and user settings.

Every path is derived from the location of this file, so the application works from any
drive letter or mount point. ``POCKETAI_HOME`` overrides the data root (used by tests).
"""
from __future__ import annotations

import json
import os
import platform
import sys
import threading
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent          # <root>/app
ROOT = Path(os.environ.get("POCKETAI_HOME") or APP_DIR.parent).resolve()

MODELS_DIR = ROOT / "models"
DATABASE_DIR = ROOT / "database"
CONFIG_DIR = ROOT / "config"
LOGS_DIR = ROOT / "logs"
PROMPTS_DIR = Path(os.environ.get("POCKETAI_PROMPTS") or APP_DIR.parent / "prompts")
FRONTEND_DIR = APP_DIR / "frontend"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
DB_FILE = DATABASE_DIR / "pocketai.db"
RUNTIME_STATE_FILE = DATABASE_DIR / "runtime.json"


def os_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def runtime_dir() -> Path:
    """Directory holding this OS's portable binaries.

    The Linux launcher may copy the runtime to a local cache when the USB is mounted
    ``noexec``; it then points ``POCKETAI_RUNTIME_DIR`` at the copy.
    """
    env = os.environ.get("POCKETAI_RUNTIME_DIR")
    if env:
        return Path(env).resolve()
    return (APP_DIR.parent / "runtime" / os_name()).resolve()


def llama_server_path() -> Path:
    exe = "llama-server.exe" if os_name() == "windows" else "llama-server"
    return runtime_dir() / "llama" / exe


def machine_arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64"}.get(m, m)


# --------------------------------------------------------------------------- settings

# (default, type, min, max) - min/max only for numbers
SCHEMA: dict[str, tuple[Any, type, Any, Any]] = {
    "active_model":     ("", str, None, None),
    "autoload_model":   (True, bool, None, None),
    "ctx_size":         (4096, int, 512, 32768),
    "threads":          (0, int, 0, 256),          # 0 = automatic (physical cores)
    "temperature":      (0.7, float, 0.0, 2.0),
    "top_p":            (0.9, float, 0.05, 1.0),
    "max_tokens":       (1024, int, 16, 8192),
    "history_messages": (20, int, 2, 200),
    "low_memory":       (False, bool, None, None),
    "enable_thinking":  (False, bool, None, None),
    "theme":            ("dark", str, None, None),
    "port":             (8765, int, 1024, 65535),
    "max_file_kb":      (256, int, 4, 2048),
    "allowed_roots":    ([], list, None, None),
}
THEMES = ("dark", "light")

# Low-memory mode caps these values regardless of the user's settings.
LOW_MEMORY_CAPS = {"ctx_size": 2048, "history_messages": 8, "max_tokens": 512}


class SettingsError(ValueError):
    pass


def defaults() -> dict[str, Any]:
    return {k: (list(v[0]) if isinstance(v[0], list) else v[0]) for k, v in SCHEMA.items()}


def validate(updates: dict[str, Any], partial: bool = True) -> dict[str, Any]:
    """Validate and coerce settings. Unknown keys and bad values raise SettingsError."""
    if not isinstance(updates, dict):
        raise SettingsError("settings must be an object")
    out: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in SCHEMA:
            raise SettingsError(f"unknown setting: {key}")
        default, typ, lo, hi = SCHEMA[key]
        if typ is bool:
            if not isinstance(value, bool):
                raise SettingsError(f"{key} must be true or false")
        elif typ is int:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
                raise SettingsError(f"{key} must be an integer")
            value = int(value)
        elif typ is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SettingsError(f"{key} must be a number")
            value = float(value)
        elif typ is str:
            if not isinstance(value, str) or len(value) > 512:
                raise SettingsError(f"{key} must be a short string")
        elif typ is list:
            if not isinstance(value, list) or not all(isinstance(x, str) and len(x) < 1024 for x in value):
                raise SettingsError(f"{key} must be a list of strings")
            if len(value) > 20:
                raise SettingsError(f"{key}: at most 20 entries")
        if lo is not None and value < lo:
            raise SettingsError(f"{key} must be >= {lo}")
        if hi is not None and value > hi:
            raise SettingsError(f"{key} must be <= {hi}")
        if key == "theme" and value not in THEMES:
            raise SettingsError("theme must be 'dark' or 'light'")
        if key == "active_model" and value and (".." in Path(value).parts or Path(value).is_absolute()):
            raise SettingsError("active_model must be a file inside the models folder")
        out[key] = value
    if not partial:
        merged = defaults()
        merged.update(out)
        return merged
    return out


_lock = threading.Lock()


def load_settings() -> dict[str, Any]:
    """Read settings.json, falling back to defaults for missing or invalid values."""
    data = defaults()
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return data
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[config] settings.json unreadable ({exc}); using defaults", file=sys.stderr)
        return data
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                data.update(validate({key: value}))
            except SettingsError as exc:
                print(f"[config] ignoring invalid setting: {exc}", file=sys.stderr)
    return data


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate ``updates``, merge into the stored settings and write atomically."""
    clean = validate(updates)
    with _lock:
        data = load_settings()
        data.update(clean)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    return data


def effective(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings with low-memory caps applied."""
    eff = dict(settings)
    if eff.get("low_memory"):
        for key, cap in LOW_MEMORY_CAPS.items():
            eff[key] = min(eff[key], cap)
    return eff


def ensure_dirs() -> None:
    for d in (MODELS_DIR, DATABASE_DIR, CONFIG_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
