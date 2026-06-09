"""Request ID middleware — generates UUIDv7 per request, sets X-Request-ID header."""

from __future__ import annotations

import structlog

from litestar import Request
from litestar.middleware import ASGIMiddleware
from litestar.types.asgi_types import ASGIApp, Message, Receive, Scope, Send

from cbs.util.uuid import generate_uuidv7

log = structlog.get_logger()

_REQUEST_ID_KEY = "request_id"


class RequestIDMiddleware(ASGIMiddleware):
    """Generate a UUIDv7 request ID, store in request state, set response header."""

    scopes = {"http"}

    def __init__(self, app: ASGIApp | None = None) -> None:
        self.app = app  # type: ignore[assignment]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Execute the ASGI middleware."""
        # Extract request ID from headers or generate one.
        headers = dict((k.decode(), v.decode()) for k, v in scope.get("headers", []))
        request_id = headers.get("x-request-id", "")
        if not request_id:
            request_id = str(generate_uuidv7())

        # Store in scope state for downstream access.
        scope.setdefault("state", {})["request_id"] = request_id

        # Bind to structlog context for logging.
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Wrap send to inject X-Request-ID into response headers.
        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers_list = list(message.get("headers", []))
                headers_list.append(
                    (b"x-request-id", request_id.encode())
                )
                message["headers"] = headers_list
            await send(message)

        # Call the next ASGI app in the chain.
        await self.app(scope, receive, send_with_header)

    # ASGIMiddleware requires handle() as an abstract method.
    # We override __call__ instead, so this is a no-op stub.
    async def handle(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, scope: Scope, receive: Receive, send: Send, next_app: ASGIApp
    ) -> None:
        pass


def get_request_id(request: Request) -> str:
    """Retrieve request ID from request state."""
    return getattr(request.state, _REQUEST_ID_KEY, "")
