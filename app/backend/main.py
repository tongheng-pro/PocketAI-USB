"""FastAPI application factory."""
from __future__ import annotations

import html
import logging
import logging.handlers
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, database, model_manager, system_monitor
from .ai_engine import EngineError, engine
from .routes import chat, files, models, settings, system
from .security import LocalOnlyMiddleware, SessionToken

log = logging.getLogger("pocketai")


def setup_logging(level: int = logging.INFO) -> None:
    """Small rotating log on the USB. Conversation text is never logged."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("pocketai")
    if root.handlers:
        return
    handler = logging.handlers.RotatingFileHandler(
        config.LOGS_DIR / "pocketai.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)


class ServerState:
    def __init__(self, port: int, token: SessionToken) -> None:
        self.port = port
        self.token = token


def create_app(state: ServerState, autoload: bool = True) -> FastAPI:
    config.ensure_dirs()
    database.init_db()
    system_monitor.prime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if autoload:
            t = threading.Thread(target=autoload_model, daemon=True, name="autoload")
            _app.state.autoload_thread = t      # the terminal client joins it before its banner
            t.start()
        yield
        engine.unload()

    app = FastAPI(title="PocketAI USB", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(LocalOnlyMiddleware, token=state.token, port_ref=lambda: state.port)
    for r in (chat.router, models.router, system.router, settings.router, files.router):
        app.include_router(r)

    index_file = config.FRONTEND_DIR / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def index():
        page = index_file.read_text(encoding="utf-8")
        return page.replace("{{SESSION_TOKEN}}", html.escape(state.token.value, quote=True))

    app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
    return app


def autoload_model() -> None:
    """Load the configured (or first discovered) model at startup, if enabled."""
    s = config.load_settings()
    if not s["autoload_model"]:
        return
    mid = s["active_model"]
    if not mid:
        found = [m for m in model_manager.discover() if m["valid"]]
        if not found:
            log.info("no models found in %s", config.MODELS_DIR)
            return
        mid = found[0]["id"]
    try:
        engine.load(mid, s)       # the memory check applies; a warning leaves it unloaded
        config.save_settings({"active_model": mid})
    except FileNotFoundError:
        log.warning("configured model %s not found", mid)
        engine.error = f"Configured model '{mid}' was not found in the models folder."
    except EngineError as exc:
        log.warning("autoload skipped: %s", exc)
        engine.error = str(exc)
