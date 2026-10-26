"""Conversation logic shared by the web dashboard and the terminal assistant.

Builds the prompt (system prompt + trimmed history), streams the reply from the engine and
saves the finished (or stopped) reply to SQLite in a single write.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterator

from . import config, database
from .ai_engine import EngineError, engine

log = logging.getLogger("pocketai.chat")

CHARS_PER_TOKEN = 3.0          # conservative estimate; real tokenizers average 3.5-4 for English
MAX_MESSAGE_CHARS = 200_000
_MODE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


# ------------------------------------------------------------------------------ prompts

def list_modes() -> list[dict[str, str]]:
    out = []
    for p in sorted(config.PROMPTS_DIR.glob("*.md")):
        if _MODE_RE.match(p.stem):
            first = p.read_text(encoding="utf-8").splitlines()[0:1]
            title = first[0].lstrip("# ").strip() if first else p.stem
            out.append({"id": p.stem, "title": title})
    return out


def system_prompt(mode: str) -> str:
    if not _MODE_RE.match(mode or ""):
        raise ValueError(f"invalid mode: {mode!r}")
    path = config.PROMPTS_DIR / f"{mode}.md"
    if not path.is_file():
        raise ValueError(f"unknown mode: {mode}")
    text = path.read_text(encoding="utf-8")
    # The first line is a title for the UI; the rest is the prompt.
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


# ------------------------------------------------------------------------------ history

def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 4


def build_messages(conv_id: int, mode: str, settings: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    """System prompt + as much recent history as fits in the context window.

    Returns (messages, trimmed) where ``trimmed`` says older turns were left out.
    """
    eff = config.effective(settings)
    ctx = engine.load_settings.get("ctx_size") or eff["ctx_size"]
    budget = ctx - eff["max_tokens"] - 64
    sys_text = system_prompt(mode)
    budget -= estimate_tokens(sys_text)

    history = database.recent_messages(conv_id, eff["history_messages"])
    picked: list[dict[str, str]] = []
    trimmed = len(history) >= eff["history_messages"]
    for msg in reversed(history):
        if msg["role"] not in ("user", "assistant"):
            continue
        content = msg["content"]
        cost = estimate_tokens(content)
        if cost > budget:
            if not picked:           # the newest user message must always be sent
                keep = max(200, int(budget * CHARS_PER_TOKEN))
                content = content[:keep] + "\n\n[... message truncated to fit the context window ...]"
                picked.append({"role": msg["role"], "content": content})
            trimmed = True
            break
        budget -= cost
        picked.append({"role": msg["role"], "content": content})
    picked.reverse()
    # Chat templates expect the history to start with a user turn.
    while picked and picked[0]["role"] != "user":
        picked.pop(0)
        trimmed = True
    return [{"role": "system", "content": sys_text}] + picked, trimmed


def _title_from(text: str) -> str:
    line = " ".join(text.strip().split())
    line = re.sub(r"^\[File: [^\]]*\]\s*", "", line)
    return (line[:57] + "...") if len(line) > 60 else (line or "New chat")


# ------------------------------------------------------------------------------ streaming

class ThinkSplitter:
    """Moves ``<think>...</think>`` spans out of the content stream.

    llama-server normally separates reasoning itself (``--reasoning-format deepseek``); this
    is a fallback for templates where the tags still arrive inside the content.
    """
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self.buf += text
        out: list[tuple[str, str]] = []
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            idx = self.buf.find(tag)
            if idx >= 0:
                if idx:
                    out.append(("reasoning" if self.inside else "content", self.buf[:idx]))
                self.buf = self.buf[idx + len(tag):]
                self.inside = not self.inside
                continue
            # keep a possible partial tag at the end for the next chunk
            keep = 0
            for n in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-n:]):
                    keep = n
                    break
            emit = self.buf[:len(self.buf) - keep]
            if emit:
                out.append(("reasoning" if self.inside else "content", emit))
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self.buf = self.buf, ""
        return [("reasoning" if self.inside else "content", rest)] if rest else []


def stream_reply(conv_id: int | None, user_text: str, mode: str,
                 settings: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Persist the user message, stream the assistant reply, persist the reply.

    Yields events: meta, reasoning, content, done, error.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        raise ValueError("message is empty")
    if len(user_text) > MAX_MESSAGE_CHARS:
        raise ValueError(f"message is too long (max {MAX_MESSAGE_CHARS} characters)")
    system_prompt(mode)                         # validates the mode before anything is written
    st = engine.status()
    if st["state"] != "ready":
        raise EngineError("No model is loaded. Open 'AI Models' and load one first."
                          if st["state"] != "loading" else "The model is still loading, please wait.")
    if engine.generating:
        raise EngineError("Another reply is still being generated.")

    if conv_id is None:
        conv = database.create_conversation(_title_from(user_text), mode)
        conv_id = conv["id"]
    else:
        conv = database.get_conversation(conv_id)
        if conv is None:
            raise LookupError("conversation not found")
        if conv["mode"] != mode:
            database.set_mode(conv_id, mode)
    user_msg = database.add_message(conv_id, "user", user_text)

    messages, trimmed = build_messages(conv_id, mode, settings)
    eff = config.effective(settings)
    yield {"type": "meta", "conversation_id": conv_id, "user_message_id": user_msg["id"],
           "model": engine.model_id, "history_trimmed": trimmed}

    content, reasoning = [], []
    splitter = ThinkSplitter()
    stats: dict[str, Any] = {}
    stopped = False
    error = ""
    try:
        for ev in engine.stream_chat(messages, eff):
            if ev["type"] == "done":
                stats, stopped = ev["stats"], ev["stopped"]
                continue
            pieces = [("reasoning", ev["text"])] if ev["type"] == "reasoning" else splitter.feed(ev["text"])
            for kind, text in pieces:
                (reasoning if kind == "reasoning" else content).append(text)
                yield {"type": kind, "text": text}
        for kind, text in splitter.flush():
            (reasoning if kind == "reasoning" else content).append(text)
            yield {"type": kind, "text": text}
    except EngineError as exc:
        error = str(exc)
        log.error("generation failed: %s", exc)
    finally:
        text = "".join(content).strip()
        if text or reasoning:
            if stopped:
                stats["stopped"] = True
            saved = database.add_message(conv_id, "assistant", text, "".join(reasoning).strip(),
                                         engine.model_id, stats)
            stats["message_id"] = saved["id"]
    if error:
        yield {"type": "error", "message": error}
    yield {"type": "done", "stopped": stopped, "stats": stats, "conversation_id": conv_id}


def stop_generation() -> bool:
    if engine.generating:
        engine.cancel()
        return True
    return False
