"""Read-only file analysis helpers.

Files reach the analyzer in two ways:
  * uploaded from the browser's file picker (the backend never sees a path), or
  * read from inside a project folder the user explicitly added to ``allowed_roots``.

Paths are resolved (following symlinks) and must stay inside the chosen root. Binary files
are refused; secrets (.env, private keys, credentials) are refused unless the user gives
explicit approval for that one request. Nothing here ever writes to user files.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

ALLOWED_EXTENSIONS = {
    ".php", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".py", ".html", ".htm", ".css",
    ".scss", ".json", ".md", ".markdown", ".sql", ".txt", ".sh", ".bash", ".log", ".xml",
    ".yml", ".yaml", ".vue",
}
LANG_BY_EXT = {
    ".php": "php", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".py": "python",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "css", ".json": "json",
    ".md": "markdown", ".markdown": "markdown", ".sql": "sql", ".sh": "bash", ".bash": "bash",
    ".xml": "html", ".yml": "yaml", ".yaml": "yaml", ".vue": "html", ".txt": "text", ".log": "text",
}

SENSITIVE_NAMES = [
    (re.compile(r"^\.env(\..*)?$", re.I), "environment file (.env) - usually holds passwords and API keys"),
    (re.compile(r"\.(pem|key|p12|pfx|jks|keystore|kdbx|ppk|asc|gpg)$", re.I), "private key / keystore file"),
    (re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", re.I), "SSH key"),
    (re.compile(r"^(credentials|secrets?)(\..*)?$", re.I), "credentials file"),
    (re.compile(r"^(\.npmrc|\.pypirc|\.netrc|\.git-credentials|auth\.json|\.htpasswd)$", re.I),
     "package-manager / auth credentials"),
    (re.compile(r"^wp-config\.php$", re.I), "WordPress config with database password"),
]
SENSITIVE_CONTENT = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "API secret key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"(?im)^\s*(DB_PASSWORD|MAIL_PASSWORD|AWS_SECRET_ACCESS_KEY|APP_KEY|[A-Z_]*SECRET[A-Z_]*)"
                r"\s*=\s*\S+"), "secret assignment (KEY=value)"),
    (re.compile(r"(?i)[\"']?(password|passwd|api[_-]?key|secret|access[_-]?token)[\"']?\s*(?:=>|[:=])\s*"
                r"[\"'][^\"'\s]{6,}[\"']"), "hard-coded password or key"),
]
SKIP_DIRS = {".git", "node_modules", "vendor", "__pycache__", ".venv", "venv", ".idea", ".vscode",
             "storage", "dist", "build", ".next", ".cache"}


class FileAccessError(ValueError):
    pass


# ----------------------------------------------------------------------- inspection

def is_binary(data: bytes) -> bool:
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    text = sample.decode("utf-8", errors="replace")
    return text.count("�") / max(1, len(text)) > 0.05


def sensitive_reasons(name: str, text: str) -> list[str]:
    reasons = [why for rx, why in SENSITIVE_NAMES if rx.search(name)]
    for rx, why in SENSITIVE_CONTENT:
        if rx.search(text) and why not in reasons:
            reasons.append(why)
    return reasons


def inspect(name: str, data: bytes, max_bytes: int) -> dict[str, Any]:
    """Describe a file and decide whether it may be analyzed."""
    name = PurePosixPath(name.replace("\\", "/")).name[:255]
    ext = Path(name).suffix.lower()
    info: dict[str, Any] = {
        "name": name, "extension": ext, "size_bytes": len(data), "language": LANG_BY_EXT.get(ext, "text"),
        "lines": 0, "binary": False, "sensitive": [], "allowed": True, "problems": [],
    }
    if ext not in ALLOWED_EXTENSIONS and not name.lower().startswith(".env"):
        info["problems"].append(f"file type '{ext or name}' is not supported")
    if len(data) > max_bytes:
        info["problems"].append(f"file is larger than the {max_bytes // 1024} KB limit")
    if is_binary(data):
        info["binary"] = True
        info["problems"].append("file looks binary, not text")
    if not info["binary"]:
        text = data.decode("utf-8", errors="replace")
        info["lines"] = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        info["sensitive"] = sensitive_reasons(name, text)
    info["allowed"] = not info["problems"]
    return info


def decode(data: bytes) -> str:
    text = data.decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n")


# ----------------------------------------------------------------------- safe paths

def validate_root(path_str: str) -> Path:
    """A project folder the user wants to allow. Must exist and not be a drive root/home."""
    p = Path(os.path.expanduser(path_str.strip())).resolve()
    if not p.is_dir():
        raise FileAccessError(f"folder does not exist: {p}")
    if p.parent == p:
        raise FileAccessError("a whole drive cannot be added; choose a project folder")
    if p == Path.home().resolve():
        raise FileAccessError("your whole home folder cannot be added; choose a project folder")
    return p


def safe_join(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; refuse anything that ends up outside it."""
    if "\x00" in rel:
        raise FileAccessError("invalid path")
    rel = rel.replace("\\", "/").lstrip("/")
    if re.match(r"^[A-Za-z]:", rel):
        raise FileAccessError("absolute paths are not allowed")
    root = root.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise FileAccessError("path is outside the allowed folder")
    return target


def list_dir(root: Path, rel: str = "") -> dict[str, Any]:
    target = safe_join(root, rel)
    if not target.is_dir():
        raise FileAccessError("not a folder")
    entries = []
    with os.scandir(target) as it:
        for e in it:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                if e.is_symlink():
                    real = Path(e.path).resolve()
                    if real != root.resolve() and root.resolve() not in real.parents:
                        continue                     # never show links that leave the root
                    is_dir = real.is_dir()
                size = 0 if is_dir else e.stat().st_size
            except OSError:
                continue
            entries.append({
                "name": e.name, "dir": is_dir, "size_bytes": size,
                "skipped_by_default": is_dir and e.name in SKIP_DIRS,
                "supported": is_dir or Path(e.name).suffix.lower() in ALLOWED_EXTENSIONS,
                "sensitive": (not is_dir) and any(rx.search(e.name) for rx, _ in SENSITIVE_NAMES),
            })
            if len(entries) >= 2000:
                break
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    rel_out = target.relative_to(root.resolve()).as_posix()
    return {"path": "" if rel_out == "." else rel_out, "entries": entries}


def read_file(root: Path, rel: str, max_bytes: int) -> tuple[str, bytes]:
    target = safe_join(root, rel)
    if not target.is_file():
        raise FileAccessError("not a file")
    size = target.stat().st_size
    with open(target, "rb") as f:
        data = f.read(max_bytes + 1)   # one extra byte lets inspect() detect "too large"
    if size > max_bytes and len(data) <= max_bytes:
        data += b"\x00"
    return target.name, data


# ----------------------------------------------------------------------- Laravel

_LARAVEL_VERSION_RE = re.compile(r"const\s+VERSION\s*=\s*'([^']+)'")


def detect_project(root: Path) -> dict[str, Any]:
    """Detect a Laravel/PHP project and the *installed* framework version."""
    out: dict[str, Any] = {"framework": None, "version": None, "constraint": None,
                           "php": None, "source": None}
    composer = root / "composer.json"
    if not composer.is_file():
        if (root / "package.json").is_file():
            out["framework"] = "node"
        return out
    try:
        cj = json.loads(composer.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return out
    req = cj.get("require", {}) if isinstance(cj, dict) else {}
    out["php"] = req.get("php")
    if "laravel/framework" not in req:
        out["framework"] = "php"
        return out
    out["framework"] = "laravel"
    out["constraint"] = req["laravel/framework"]
    app_php = root / "vendor/laravel/framework/src/Illuminate/Foundation/Application.php"
    try:
        m = _LARAVEL_VERSION_RE.search(app_php.read_text(encoding="utf-8", errors="replace")[:20000])
        if m:
            out["version"], out["source"] = m.group(1), "vendor/laravel/framework"
    except OSError:
        pass
    if not out["version"]:
        try:
            lock = json.loads((root / "composer.lock").read_text(encoding="utf-8", errors="replace"))
            for pkg in lock.get("packages", []):
                if pkg.get("name") == "laravel/framework":
                    out["version"], out["source"] = str(pkg.get("version", "")).lstrip("v"), "composer.lock"
                    break
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return out


def project_guidance(project: dict[str, Any]) -> str:
    """Extra instructions that keep suggestions compatible with the detected stack."""
    if project.get("framework") != "laravel":
        if project.get("php"):
            return f"The project requires PHP {project['php']}. Only suggest code valid for that PHP version."
        return ""
    ver = project.get("version") or f"(constraint {project.get('constraint')})"
    lines = [
        f"This is a Laravel {ver} project" + (f" requiring PHP {project['php']}" if project.get("php") else "") + ".",
        "Only use APIs, helpers and syntax that exist in this exact Laravel and PHP version.",
        "Do not suggest features from newer Laravel releases or upgrade the framework unless asked.",
        "Preserve the existing architecture, naming and folder structure.",
    ]
    v = project.get("version") or ""
    if v.startswith("5.") or (project.get("constraint") or "").lstrip("^~ ").startswith("5."):
        lines.append("Laravel 5.x notes: model factories use $factory->define() (class-based "
                     "factories are Laravel 8+); migrations are named classes (anonymous "
                     "migrations are Laravel 8.37+); global str_*/array_* helpers are normal "
                     "here; do not use typed properties, arrow functions, match, nullsafe "
                     "operators or named arguments unless the required PHP version supports them.")
    return " ".join(lines)


def build_prompt(name: str, text: str, language: str, task: str, char_budget: int,
                 guidance: str = "", extra: str = "") -> tuple[str, bool]:
    """The user message for an analysis request; clips the file to ``char_budget``."""
    truncated = len(text) > char_budget
    body = text[:char_budget]
    fence = "````" if "```" in body else "```"
    parts = [f"[File: {name}] {task}"]
    if guidance:
        parts.append(f"Project context: {guidance}")
    if extra:
        parts.append(extra)
    parts.append(f"{fence}{language if language != 'text' else ''}\n{body}\n{fence}")
    if truncated:
        parts.append(f"(Only the first {char_budget} characters of the file are shown; the rest "
                     f"was cut to fit the model's context window.)")
    return "\n\n".join(parts), truncated


TASKS = {
    "explain":     ("coding", "Explain what this code does, section by section."),
    "bugs":        ("debugging", "Review this code for bugs, edge cases and security problems. "
                                 "List each issue with the line it is on and a suggested fix."),
    "improve":     ("coding", "Suggest concrete improvements to readability and structure. "
                              "Do not rewrite the whole file; show focused changes."),
    "performance": ("coding", "Find performance problems and recommend specific optimizations."),
    "docs":        ("documentation", "Write documentation for this file: purpose, public "
                                     "functions/classes, parameters and usage examples."),
    "sql":         ("coding", "Explain these database queries, their cost, and index suggestions."),
    "summary":     ("general", "Summarize this file briefly."),
}
