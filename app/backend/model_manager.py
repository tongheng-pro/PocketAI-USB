"""GGUF model discovery, metadata and memory estimates.

Models are any ``*.gguf`` files in ``models/`` (and one level of sub-folders). Nothing is
hard-coded to a particular model; the catalog below only lists *optional* downloads that
setup offers, with sizes and SHA-256 hashes verified against Hugging Face.
"""
from __future__ import annotations

import struct
import threading
from pathlib import Path
from typing import Any, BinaryIO

from . import config

# Optional downloads offered by scripts/setup.py. Never fetched without user approval.
CATALOG: list[dict[str, Any]] = [
    {
        "id": "qwen3-1.7b", "name": "Qwen3 1.7B (recommended)",
        "repo": "unsloth/Qwen3-1.7B-GGUF", "file": "Qwen3-1.7B-Q4_K_M.gguf",
        "size": 1107409472,
        "sha256": "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
        "license": "Apache-2.0", "min_ram_gb": 8,
    },
    {
        "id": "qwen3-0.6b", "name": "Qwen3 0.6B (4 GB RAM computers)",
        "repo": "unsloth/Qwen3-0.6B-GGUF", "file": "Qwen3-0.6B-Q4_K_M.gguf",
        "size": 396705472,
        "sha256": "ac2d97712095a558e31573f62f466a3f9d93990898b0ec79d7c974c1780d524a",
        "license": "Apache-2.0", "min_ram_gb": 4,
    },
    {
        "id": "llama3.2-1b", "name": "Llama 3.2 1B Instruct",
        "repo": "bartowski/Llama-3.2-1B-Instruct-GGUF", "file": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "size": 807694464,
        "sha256": "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83",
        "license": "Llama 3.2 Community License", "min_ram_gb": 4,
    },
    {
        "id": "gemma3-1b", "name": "Gemma 3 1B Instruct",
        "repo": "ggml-org/gemma-3-1b-it-GGUF", "file": "gemma-3-1b-it-Q4_K_M.gguf",
        "size": 806058240,
        "sha256": "8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135",
        "license": "Gemma Terms of Use", "min_ram_gb": 4,
    },
]

GGUF_FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
    11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
    17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 24: "IQ1_S", 25: "IQ4_NL",
    26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
}

# ------------------------------------------------------------------------- GGUF reader

_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
            10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


class GGUFError(ValueError):
    pass


def _read(f: BinaryIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise GGUFError("unexpected end of file")
    return b


def _read_str(f: BinaryIO) -> str:
    (n,) = struct.unpack("<Q", _read(f, 8))
    if n > 1 << 24:
        raise GGUFError("string too long")
    return _read(f, n).decode("utf-8", errors="replace")


def _read_value(f: BinaryIO, vtype: int, keep: bool) -> Any:
    if vtype in _SCALARS:
        fmt = _SCALARS[vtype]
        return struct.unpack(fmt, _read(f, struct.calcsize(fmt)))[0]
    if vtype == _STRING:
        if keep:
            return _read_str(f)
        (n,) = struct.unpack("<Q", _read(f, 8))
        f.seek(n, 1)
        return None
    if vtype == _ARRAY:
        etype, count = struct.unpack("<IQ", _read(f, 12))
        if etype in _SCALARS:                      # fixed-size: skip in one seek
            f.seek(struct.calcsize(_SCALARS[etype]) * count, 1)
        else:
            for _ in range(count):
                _read_value(f, etype, False)
        return None
    raise GGUFError(f"unknown value type {vtype}")


def read_gguf_metadata(path: Path) -> dict[str, Any]:
    """Read the scalar key/value metadata from a GGUF header (arrays are skipped)."""
    meta: dict[str, Any] = {}
    with open(path, "rb") as f:
        if _read(f, 4) != b"GGUF":
            raise GGUFError("not a GGUF file")
        (version,) = struct.unpack("<I", _read(f, 4))
        if version < 2:
            raise GGUFError(f"unsupported GGUF version {version}")
        _tensors, kv_count = struct.unpack("<QQ", _read(f, 16))
        if kv_count > 100_000:
            raise GGUFError("corrupt header")
        meta["gguf.version"] = version
        for _ in range(kv_count):
            key = _read_str(f)
            (vtype,) = struct.unpack("<I", _read(f, 4))
            keep = not key.startswith("tokenizer.")
            value = _read_value(f, vtype, keep)
            if value is not None:
                meta[key] = value
    return meta


# --------------------------------------------------------------------------- discovery

_cache: dict[tuple[str, int, float], dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _summarize(meta: dict[str, Any]) -> dict[str, Any]:
    arch = meta.get("general.architecture", "")

    def a(key: str) -> Any:
        return meta.get(f"{arch}.{key}")

    n_head = a("attention.head_count") or 0
    n_embd = a("embedding_length") or 0
    head_dim = a("attention.key_length") or (n_embd // n_head if n_head else 0)
    return {
        "architecture": arch,
        "display_name": meta.get("general.name") or "",
        "size_label": meta.get("general.size_label") or "",
        "quantization": GGUF_FILE_TYPES.get(meta.get("general.file_type", -1), ""),
        "context_train": a("context_length") or 0,
        "n_layer": a("block_count") or 0,
        "n_head_kv": a("attention.head_count_kv") or n_head,
        "head_dim_k": head_dim,
        "head_dim_v": a("attention.value_length") or head_dim,
    }


def model_id(path: Path) -> str:
    return path.relative_to(config.MODELS_DIR).as_posix()


def resolve_model(mid: str) -> Path:
    """Map a model id to a file inside models/, rejecting anything that escapes it."""
    if not mid or "\\" in mid or mid.startswith("/") or ".." in mid.split("/"):
        raise FileNotFoundError("invalid model id")
    base = config.MODELS_DIR.resolve()
    path = (base / mid).resolve()
    if base not in path.parents or path.suffix.lower() != ".gguf" or not path.is_file():
        raise FileNotFoundError(f"model not found: {mid}")
    return path


def describe(path: Path) -> dict[str, Any]:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    with _cache_lock:
        if key in _cache:
            return dict(_cache[key])
    info: dict[str, Any] = {"id": model_id(path), "file": path.name, "size_bytes": st.st_size,
                            "valid": True, "error": ""}
    try:
        info.update(_summarize(read_gguf_metadata(path)))
    except (OSError, GGUFError, struct.error) as exc:
        info.update(valid=False, error=f"unreadable GGUF header: {exc}")
    if not info.get("display_name"):
        info["display_name"] = path.stem
    with _cache_lock:
        _cache[key] = info
    return dict(info)


def discover() -> list[dict[str, Any]]:
    """All GGUF models in models/ (top level and one sub-folder deep)."""
    base = config.MODELS_DIR
    if not base.is_dir():
        return []
    files = list(base.glob("*.gguf")) + list(base.glob("*/*.gguf"))
    out = []
    for p in sorted(files, key=lambda x: x.name.lower()):
        name = p.name.lower()
        if "mmproj" in name or not p.is_file():   # vision projectors are not chat models
            continue
        out.append(describe(p))
    return out


def estimate_memory(info: dict[str, Any], ctx_size: int) -> int:
    """Rough RAM need in bytes: weights + f16 KV cache + compute buffers."""
    weights = int(info.get("size_bytes", 0))
    kv = 0
    if info.get("n_layer") and info.get("n_head_kv"):
        per_token = info["n_head_kv"] * (info["head_dim_k"] + info["head_dim_v"]) * 2  # f16
        kv = info["n_layer"] * ctx_size * per_token
    # compute buffers (grow with ctx) + prompt cache; measured RSS for Qwen3 1.7B @4096 is ~2.3 GB
    overhead = 400 * 1024**2 + ctx_size * 32 * 1024
    return weights + kv + overhead


def default_threads() -> int:
    try:
        import psutil
        n = psutil.cpu_count(logical=False) or psutil.cpu_count() or 2
    except Exception:
        import os
        n = max(1, (os.cpu_count() or 2) // 2)
    return max(1, n)
