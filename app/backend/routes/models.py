"""Model manager endpoints."""
from __future__ import annotations

from typing import Optional

import psutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import config, model_manager
from ..ai_engine import BusyError, EngineError, MemoryWarning, engine

router = APIRouter(prefix="/api/models")


class LoadRequest(BaseModel):
    model: str = Field(min_length=1, max_length=512)
    force: bool = False


class ParamsRequest(BaseModel):
    ctx_size: Optional[int] = None
    threads: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    low_memory: Optional[bool] = None
    enable_thinking: Optional[bool] = None


@router.get("")
def list_models():
    settings = config.load_settings()
    eff = config.effective(settings)
    avail = psutil.virtual_memory().available
    models = []
    for m in model_manager.discover():
        ctx = min(eff["ctx_size"], m.get("context_train") or eff["ctx_size"])
        need = model_manager.estimate_memory(m, ctx) if m["valid"] else 0
        m.update(estimated_ram=need, fits_in_ram=need <= avail)
        models.append(m)
    return {
        "models": models, "active": settings["active_model"], "status": engine.status(),
        "models_dir": str(config.MODELS_DIR), "available_ram": avail,
        "total_ram": psutil.virtual_memory().total,
        "default_threads": model_manager.default_threads(),
        "catalog": model_manager.CATALOG,
        "params": {k: settings[k] for k in ("ctx_size", "threads", "temperature", "top_p",
                                            "max_tokens", "low_memory", "enable_thinking")},
        "low_memory_caps": config.LOW_MEMORY_CAPS,
    }


@router.get("/status")
def status():
    return engine.status()


@router.post("/load")
def load(req: LoadRequest):
    settings = config.load_settings()
    try:
        engine.load(req.model, settings, force=req.force)
    except MemoryWarning as exc:
        return JSONResponse(status_code=409, content={
            "detail": str(exc), "memory_warning": True,
            "needed": exc.needed, "available": exc.available})
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except BusyError as exc:
        raise HTTPException(409, str(exc))
    except EngineError as exc:
        raise HTTPException(500, str(exc))
    config.save_settings({"active_model": req.model})
    return engine.status()


@router.post("/unload")
def unload():
    engine.unload()
    return engine.status()


@router.put("/params")
def params(req: ParamsRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        before = config.effective(config.load_settings())
        after = config.effective(config.save_settings(updates))
    except config.SettingsError as exc:
        raise HTTPException(400, str(exc))
    reload_keys = ("ctx_size", "threads", "low_memory")
    needs_reload = engine.status()["state"] in ("ready", "loading") and any(
        before[k] != after[k] for k in reload_keys)
    return {"ok": True, "needs_reload": needs_reload}
