"""Chat and conversation-history endpoints."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import chat_service, config, database
from ..ai_engine import EngineError

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    conversation_id: Optional[int] = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=chat_service.MAX_MESSAGE_CHARS)
    mode: str = Field(default="general", pattern=r"^[a-z][a-z0-9_-]{0,31}$")


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.get("/modes")
def modes():
    return {"modes": chat_service.list_modes()}


@router.get("/conversations")
def conversations(q: str = "", limit: int = 100, offset: int = 0):
    if len(q) > 200:
        raise HTTPException(400, "search text too long")
    return {"conversations": database.list_conversations(q, limit=limit, offset=max(0, offset))}


@router.get("/conversations/{conv_id}")
def conversation(conv_id: int):
    conv = database.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    return conv


@router.patch("/conversations/{conv_id}")
def rename(conv_id: int, req: RenameRequest):
    try:
        ok = database.rename_conversation(conv_id, req.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        raise HTTPException(404, "conversation not found")
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
def delete(conv_id: int):
    if not database.delete_conversation(conv_id):
        raise HTTPException(404, "conversation not found")
    return {"ok": True}


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


_DONE = object()


@router.post("/chat/stream")
def chat_stream(req: ChatRequest):
    settings = config.load_settings()
    gen = chat_service.stream_reply(req.conversation_id, req.message, req.mode, settings)
    try:
        first = next(gen)                      # runs validation + saves the user message
    except (ValueError, EngineError) as exc:
        raise HTTPException(409 if isinstance(exc, EngineError) else 400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    # A producer thread always drives the generator to the end, so the reply is saved even
    # if the browser disconnects; a disconnect cancels the generation.
    q: "queue.Queue[object]" = queue.Queue()

    def produce() -> None:
        try:
            for ev in gen:
                q.put(ev)
        except Exception as exc:  # surfaced to the client as an error event
            q.put({"type": "error", "message": f"internal error: {exc}"})
        finally:
            q.put(_DONE)

    threading.Thread(target=produce, daemon=True, name="chat-producer").start()

    async def events():
        finished = False
        try:
            yield _sse(first)
            while True:
                try:
                    ev = await asyncio.to_thread(q.get, True, 0.5)
                except queue.Empty:
                    continue
                if ev is _DONE:
                    finished = True
                    break
                yield _sse(ev)
        finally:
            if not finished:
                chat_service.stop_generation()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no"})


@router.post("/chat/stop")
def chat_stop():
    return {"stopped": chat_service.stop_generation()}
