"""Read-only file analysis endpoints."""
from __future__ import annotations

import base64
import binascii
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import chat_service, config, file_analyzer
from ..ai_engine import engine

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------- file analysis

class FileRef(BaseModel):
    source: Literal["upload", "path"]
    name: Optional[str] = Field(default=None, max_length=255)
    content_b64: Optional[str] = Field(default=None, max_length=4 * 1024 * 1024)
    root: Optional[int] = Field(default=None, ge=0, le=50)
    path: Optional[str] = Field(default=None, max_length=1024)


class PrepareRequest(FileRef):
    task: str = Field(default="explain", max_length=32)
    note: str = Field(default="", max_length=4000)
    allow_sensitive: bool = False


def _root(index: Optional[int]):
    roots = config.load_settings()["allowed_roots"]
    if index is None or index >= len(roots):
        raise HTTPException(404, "project folder not found; add it in Settings first")
    try:
        return file_analyzer.validate_root(roots[index])
    except file_analyzer.FileAccessError as exc:
        raise HTTPException(400, str(exc))


def _load(ref: FileRef) -> tuple[str, bytes, dict]:
    max_bytes = config.load_settings()["max_file_kb"] * 1024
    project: dict = {}
    if ref.source == "upload":
        if not ref.name or ref.content_b64 is None:
            raise HTTPException(400, "name and content_b64 are required for uploads")
        try:
            data = base64.b64decode(ref.content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "content_b64 is not valid base64")
        name = ref.name
    else:
        root = _root(ref.root)
        try:
            name, data = file_analyzer.read_file(root, ref.path or "", max_bytes)
        except file_analyzer.FileAccessError as exc:
            raise HTTPException(400, str(exc))
        project = file_analyzer.detect_project(root)
    return name, data, project


@router.get("/files/roots")
def roots():
    out = []
    for i, p in enumerate(config.load_settings()["allowed_roots"]):
        try:
            root = file_analyzer.validate_root(p)
            out.append({"index": i, "path": str(root), "ok": True,
                        "project": file_analyzer.detect_project(root)})
        except file_analyzer.FileAccessError as exc:
            out.append({"index": i, "path": p, "ok": False, "error": str(exc)})
    return {"roots": out}


@router.get("/files/list")
def list_files(root: int, path: str = ""):
    if len(path) > 1024:
        raise HTTPException(400, "path too long")
    try:
        return file_analyzer.list_dir(_root(root), path)
    except file_analyzer.FileAccessError as exc:
        raise HTTPException(400, str(exc))


@router.get("/files/tasks")
def tasks():
    return {"tasks": [{"id": k, "mode": v[0], "instruction": v[1]}
                      for k, v in file_analyzer.TASKS.items()]}


@router.post("/files/inspect")
def inspect(ref: FileRef):
    name, data, project = _load(ref)
    max_bytes = config.load_settings()["max_file_kb"] * 1024
    info = file_analyzer.inspect(name, data, max_bytes)
    info["project"] = project
    return info


@router.post("/files/prepare")
def prepare(req: PrepareRequest):
    """Validate a file and build the chat message that asks the model to analyze it.

    Sensitive files are refused (403) unless ``allow_sensitive`` is true, which the UI only
    sends after the user confirms a warning listing what was detected.
    """
    if req.task not in file_analyzer.TASKS:
        raise HTTPException(400, "unknown task")
    settings = config.load_settings()
    name, data, project = _load(req)
    info = file_analyzer.inspect(name, data, settings["max_file_kb"] * 1024)
    if not info["allowed"]:
        raise HTTPException(400, "; ".join(info["problems"]))
    if info["sensitive"] and not req.allow_sensitive:
        raise HTTPException(403, {"sensitive": info["sensitive"],
                                  "message": "This file looks like it contains secrets."})
    mode, instruction = file_analyzer.TASKS[req.task]
    eff = config.effective(settings)
    ctx = engine.load_settings.get("ctx_size") or eff["ctx_size"]
    # Leave room for the system prompt, the reply, and a follow-up question.
    budget_tokens = max(256, ctx - eff["max_tokens"] - 600)
    char_budget = int(budget_tokens * chat_service.CHARS_PER_TOKEN)
    message, truncated = file_analyzer.build_prompt(
        name, file_analyzer.decode(data), info["language"], instruction, char_budget,
        guidance=file_analyzer.project_guidance(project), extra=req.note.strip())
    return {"message": message, "mode": mode, "info": info, "truncated": truncated,
            "project": project}
