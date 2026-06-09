"""Request/response structured logging middleware."""

from __future__ import annotations

import time

import structlog
from litestar.middleware import ASGIMiddleware
from litestar.types.asgi_types import (
    ASGIApp,
    Message,
    Receive,
    Scope,
    Send,
)

log = structlog.get_logger()


class LoggingMiddleware(ASGIMiddleware):
    """Log every HTTP request with structured fields after the response is sent."""

    scopes = {"http"}

    def __init__(self, app: ASGIApp | None = None) -> None:
        self.app = app  # type: ignore[assignment]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Execute the ASGI middleware."""
        start = time.monotonic()
        status_code: int = 200

        async def send_with_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_with_capture)

        duration_ms = int((time.monotonic() - start) * 1000)

        request_id = scope.get("state", {}).get("request_id", "")

        fields = {
            "method": scope["method"],  # type: ignore[literal-required]
            "path": scope["path"],  # type: ignore[literal-required]
            "status": status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
        }

        if status_code >= 500:
            log.error("http_request", **fields)
        else:
            log.info("http_request", **fields)

    # ASGIMiddleware requires handle() as an abstract method.
    # We override __call__ instead, so this is a no-op stub.
    async def handle(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, scope: Scope, receive: Receive, send: Send, next_app: ASGIApp
    ) -> None:
        pass
