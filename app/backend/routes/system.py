"""System monitor endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__, system_monitor
from ..ai_engine import engine

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    # Unauthenticated liveness probe: reveals nothing beyond "running".
    return {"ok": True, "app": "PocketAI USB", "version": __version__}


@router.get("/system")
def system():
    return system_monitor.snapshot()


@router.get("/system/storage")
def storage(refresh: bool = False):
    return system_monitor.app_usage(max_age=0 if refresh else 120)


@router.get("/status")
def status():
    return {"version": __version__, "engine": engine.status()}
