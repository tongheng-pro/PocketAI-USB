"""Local-only access control.

* The server binds to 127.0.0.1 only.
* The Host header must be 127.0.0.1/localhost on our port (blocks DNS-rebinding attacks).
* Every /api request needs the per-session token in the ``X-PocketAI-Token`` header. A
  custom header cannot be sent cross-site without a CORS preflight, which we never approve,
  so other websites open in the same browser cannot drive the API.
* A present Origin header must be our own origin.
* Strict Content-Security-Policy: scripts, styles and connections from 'self' only.
"""
from __future__ import annotations

import secrets
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

TOKEN_HEADER = "x-pocketai-token"

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")


class SessionToken:
    def __init__(self, value: str | None = None) -> None:
        self.value = value or secrets.token_urlsafe(32)

    def check(self, candidate: str | None) -> bool:
        return bool(candidate) and secrets.compare_digest(candidate, self.value)


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: SessionToken, port_ref: Callable[[], int]) -> None:
        super().__init__(app)
        self.token = token
        self.port_ref = port_ref

    def _allowed_hosts(self) -> set[str]:
        port = self.port_ref()
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        host = request.headers.get("host", "")
        if host not in self._allowed_hosts():
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{h}" for h in self._allowed_hosts()}:
            return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            if not self.token.check(request.headers.get(TOKEN_HEADER)):
                return JSONResponse({"detail": "missing or invalid session token"}, status_code=401)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response
