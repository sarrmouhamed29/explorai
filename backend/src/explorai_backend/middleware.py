"""CSRF defence: reject state-changing requests from a foreign browser origin."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from explorai_backend.config import get_settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware:
    """Pure ASGI middleware checking the ``Origin`` header on unsafe methods.

    Browsers always send ``Origin`` on cross-site writes and scripts cannot forge
    it, so a foreign value is refused. A missing header means a non-browser
    client (curl, tests), which is not a CSRF vector. The allowed origins are
    read at request time so building the app (OpenAPI export) needs no
    environment.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            origin = next(
                (v.decode("latin-1") for k, v in scope["headers"] if k == b"origin"),
                None,
            )
            if origin is not None and origin not in get_settings().allowed_origins:
                response = JSONResponse(
                    {"detail": "origin not allowed"}, status_code=403
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
