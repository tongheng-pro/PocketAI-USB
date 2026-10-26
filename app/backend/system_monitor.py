"""On-demand system information (no background polling thread)."""
from __future__ import annotations

import os
import platform
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from . import config, database
from .ai_engine import engine

_cpu_name: str | None = None
_size_cache: tuple[float, dict[str, int]] | None = None
_size_lock = threading.Lock()


def cpu_name() -> str:
    global _cpu_name
    if _cpu_name is not None:
        return _cpu_name
    name = ""
    try:
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
        elif Path("/proc/cpuinfo").exists():
            for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    name = line.split(":", 1)[1]
                    break
    except OSError:
        pass
    _cpu_name = " ".join((name or platform.processor() or "unknown").split())
    return _cpu_name


def prime() -> None:
    """psutil's first cpu_percent() call always returns 0; call once at startup."""
    psutil.cpu_percent(interval=None)


def _dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def app_usage(max_age: float = 120) -> dict[str, int]:
    """Bytes used by each top-level folder of the app (cached; walking a USB is slow)."""
    global _size_cache
    with _size_lock:
        if _size_cache and time.time() - _size_cache[0] < max_age:
            return _size_cache[1]
        root = config.APP_DIR.parent
        usage = {}
        for name in ("app", "runtime", "models", "database", "logs", "config", "prompts", "docs",
                     "scripts", "tests"):
            p = root / name
            if p.exists():
                usage[name] = _dir_size(p)
        usage["total"] = sum(usage.values())
        _size_cache = (time.time(), usage)
        return usage


def snapshot() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    root = config.APP_DIR.parent
    du = shutil.disk_usage(root)
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        disks.append({"mount": part.mountpoint, "fstype": part.fstype, "total": u.total,
                      "free": u.free, "percent": u.percent,
                      "removable": "removable" in (part.opts or "")})
    status = engine.status()
    freq = psutil.cpu_freq()
    return {
        "os": f"{platform.system()} {platform.release()}",
        "os_detail": platform.platform(),
        "arch": config.machine_arch(),
        "python": platform.python_version(),
        "cpu": {"name": cpu_name(), "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(), "percent": psutil.cpu_percent(interval=None),
                "mhz": round(freq.current) if freq else None},
        "memory": {"total": vm.total, "available": vm.available, "percent": vm.percent},
        "app_drive": {"path": str(root), "total": du.total, "free": du.free, "used": du.used},
        "disks": disks,
        "engine": {**status, "rss_bytes": engine.process_rss()},
        "database": database.stats(),
        "time": time.time(),
    }
