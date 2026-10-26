"""Settings endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import config, file_analyzer

router = APIRouter(prefix="/api")


@router.get("/settings")
def get_settings():
    return {"settings": config.load_settings(), "defaults": config.defaults(),
            "paths": {"root": str(config.ROOT), "models": str(config.MODELS_DIR),
                      "database": str(config.DB_FILE), "logs": str(config.LOGS_DIR)}}


@router.put("/settings")
def put_settings(updates: dict[str, Any]):
    if "allowed_roots" in updates and isinstance(updates["allowed_roots"], list):
        try:
            updates["allowed_roots"] = [str(file_analyzer.validate_root(p))
                                        for p in updates["allowed_roots"] if isinstance(p, str)]
        except file_analyzer.FileAccessError as exc:
            raise HTTPException(400, str(exc))
    try:
        saved = config.save_settings(updates)
    except config.SettingsError as exc:
        raise HTTPException(400, str(exc))
    return {"settings": saved}
