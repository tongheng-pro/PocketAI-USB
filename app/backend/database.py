"""SQLite storage for conversations and messages.

One short-lived connection per operation keeps things thread-safe without a pool.
WAL + synchronous=NORMAL reduce the number of flushes to the USB drive.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

SCHEMA_VERSION = 1
ROLES = ("user", "assistant", "system")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    mode       TEXT NOT NULL DEFAULT 'general',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    reasoning       TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    stats           TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
"""


def _path() -> Path:
    return config.DB_FILE


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))


def _conv(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _msg(row: sqlite3.Row) -> dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    try:
        d["stats"] = json.loads(d.get("stats") or "{}")
    except json.JSONDecodeError:
        d["stats"] = {}
    return d


def create_conversation(title: str, mode: str = "general") -> dict[str, Any]:
    now = time.time()
    title = (title or "New chat").strip()[:120] or "New chat"
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations(title, mode, created_at, updated_at) VALUES (?,?,?,?)",
            (title, mode, now, now))
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (cur.lastrowid,)).fetchone()
    return _conv(row)


def get_conversation(conv_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if not row:
            return None
        msgs = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,)).fetchall()
    conv = _conv(row)
    conv["messages"] = [_msg(m) for m in msgs]
    return conv


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_conversations(query: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Recent conversations, optionally filtered by a search over titles and message text."""
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        if query.strip():
            pat = _like(query.strip())
            rows = conn.execute(
                """SELECT c.*, (SELECT substr(m.content, 1, 160) FROM messages m
                                 WHERE m.conversation_id=c.id AND m.content LIKE ? ESCAPE '\\'
                                 ORDER BY m.id LIMIT 1) AS snippet
                   FROM conversations c
                   WHERE c.title LIKE ? ESCAPE '\\'
                      OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id
                                 AND m.content LIKE ? ESCAPE '\\')
                   ORDER BY c.updated_at DESC LIMIT ? OFFSET ?""",
                (pat, pat, pat, limit, offset)).fetchall()
        else:
            rows = conn.execute(
                "SELECT c.*, NULL AS snippet FROM conversations c "
                "ORDER BY c.updated_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [_conv(r) for r in rows]


def rename_conversation(conv_id: int, title: str) -> bool:
    title = title.strip()[:120]
    if not title:
        raise ValueError("title must not be empty")
    with connect() as conn:
        cur = conn.execute("UPDATE conversations SET title=? WHERE id=?", (title, conv_id))
    return cur.rowcount > 0


def set_mode(conv_id: int, mode: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE conversations SET mode=? WHERE id=?", (mode, conv_id))


def delete_conversation(conv_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    return cur.rowcount > 0


def add_message(conv_id: int, role: str, content: str, reasoning: str = "",
                model: str = "", stats: dict[str, Any] | None = None) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError(f"invalid role: {role}")
    now = time.time()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages(conversation_id, role, content, reasoning, model, stats, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (conv_id, role, content, reasoning, model, json.dumps(stats or {}), now))
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
        row = conn.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
    return _msg(row)


def recent_messages(conv_id: int, limit: int) -> list[dict[str, Any]]:
    """The last ``limit`` messages of a conversation in chronological order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conv_id, limit)).fetchall()
    return [_msg(r) for r in reversed(rows)]


def stats() -> dict[str, int]:
    with connect() as conn:
        c = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    size = _path().stat().st_size if _path().exists() else 0
    return {"conversations": c, "messages": m, "db_bytes": size}
