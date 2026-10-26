"""Test helpers. Import this BEFORE any backend module.

It points POCKETAI_HOME at a throw-away directory so tests never touch the real
settings, models or chat history on the USB drive.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEST_HOME = Path(tempfile.mkdtemp(prefix="pocketai test home "))   # note the spaces
os.environ["POCKETAI_HOME"] = str(TEST_HOME)
sys.path.insert(0, str(REPO / "app"))

from backend import config, database  # noqa: E402

if config.ROOT != TEST_HOME.resolve():   # never let tests touch real chats/settings/models
    raise SystemExit(f"refusing to run tests: backend already bound to {config.ROOT}")
config.ensure_dirs()
database.init_db()


def reset_state() -> None:
    """Fresh database + default settings + empty models folder."""
    for f in config.DATABASE_DIR.glob("pocketai.db*"):
        f.unlink()
    database.init_db()
    if config.SETTINGS_FILE.exists():
        config.SETTINGS_FILE.unlink()
    for f in config.MODELS_DIR.rglob("*"):
        if f.is_file():
            f.unlink()


# ------------------------------------------------------------------------ GGUF fixture

def _gguf_str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def _gguf_value(v) -> tuple[int, bytes]:
    if isinstance(v, bool):
        return 7, struct.pack("<?", v)
    if isinstance(v, int):
        return 4, struct.pack("<I", v)
    if isinstance(v, float):
        return 6, struct.pack("<f", v)
    if isinstance(v, str):
        return 8, _gguf_str(v)
    if isinstance(v, list):
        if all(isinstance(x, str) for x in v):
            return 9, struct.pack("<IQ", 8, len(v)) + b"".join(_gguf_str(x) for x in v)
        return 9, struct.pack("<IQ", 5, len(v)) + b"".join(struct.pack("<i", x) for x in v)
    raise TypeError(v)


def write_gguf(path: Path, kv: dict, pad: int = 0) -> Path:
    body = b""
    for k, v in kv.items():
        t, payload = _gguf_value(v)
        body += _gguf_str(k) + struct.pack("<I", t) + payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, len(kv)) + body + b"\0" * pad)
    return path


QWEN_LIKE = {
    "general.architecture": "qwen3", "general.name": "Tiny Test Model", "general.size_label": "1.7B",
    "general.file_type": 15, "tokenizer.ggml.tokens": ["a", "b", "c"], "tokenizer.ggml.token_type": [1, 1, 1],
    "qwen3.context_length": 40960, "qwen3.block_count": 28, "qwen3.embedding_length": 2048,
    "qwen3.attention.head_count": 16, "qwen3.attention.head_count_kv": 8, "qwen3.attention.key_length": 128,
    "qwen3.attention.value_length": 128,
}


# ------------------------------------------------------------------------ live server

class LiveServer:
    """Runs the real FastAPI app with uvicorn in a background thread."""

    def __init__(self) -> None:
        import uvicorn
        from backend.ai_engine import free_port
        from backend.main import ServerState, create_app
        from backend.security import SessionToken

        self.port = free_port()
        self.token = SessionToken()
        self.app = create_app(ServerState(self.port, self.token), autoload=False)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                    log_level="error", access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "LiveServer":
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.025)
        raise RuntimeError("server did not start")

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def request(self, method: str, path: str, body=None, token: bool = True, headers: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if token:
            req.add_header("X-PocketAI-Token", self.token.value)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw and r.headers.get_content_type() == "application/json" else raw.decode()), r.headers
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw), e.headers
            except json.JSONDecodeError:
                return e.code, raw.decode(), e.headers

    def stream(self, body: dict) -> list[dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/chat/stream",
                                     data=json.dumps(body).encode(), method="POST")
        req.add_header("X-PocketAI-Token", self.token.value)
        req.add_header("Content-Type", "application/json")
        events = []
        with urllib.request.urlopen(req, timeout=60) as r:
            for line in r:
                line = line.decode().strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
        return events


def loopback_only_guard():
    """Patch socket.connect so any non-loopback connection attempt raises (offline test)."""
    original = socket.socket.connect
    attempts: list = []

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            attempts.append(address)
            raise OSError(f"network access blocked by test: {address}")
        return original(self, address)

    return original, guarded, attempts
