"""Idempotency-Key middleware — replays cached responses for duplicate keys."""

from __future__ import annotations

import json
import re

import structlog
from litestar import Request
from litestar.middleware import ASGIMiddleware
from litestar.types.asgi_types import (
    ASGIApp,
    Message,
    Receive,
    Scope,
    Send,
)

from cbs.api.responses import error_response
from cbs.domain.errors import ErrIdempotencyKeyExists
from cbs.store.postgres.idempotency_repo import IdempotencyRepo

log = structlog.get_logger()

# ASGI headers are stored as lowercase bytes — use lowercase for lookup.
_IDEMPOTENCY_KEY_HEADER = "idempotency-key"
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_valid_uuid(value: str) -> bool:
    """Quick UUID format check (8-4-4-4-12 hex digits)."""
    return bool(_UUID_RE.match(value))


async def _send_json_response(
    status_code: int,
    body: dict | bytes,
    send: Send,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Send a JSON response directly via the ASGI send callable."""
    if isinstance(body, dict):
        body_bytes = json.dumps(body).encode()
    else:
        body_bytes = body

    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body_bytes)).encode()),
    ]
    if extra_headers:
        for k, v in extra_headers.items():
            headers.append((k.encode(), v.encode()))

    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body_bytes})


class IdempotencyMiddleware(ASGIMiddleware):
    """Enforce idempotency for mutating requests via Idempotency-Key header.

    Expects the Litestar app to have ``app.state.db`` set (the Database instance).

    Follows the same pattern as RequestIDMiddleware: stores ``app`` in __init__,
    overrides __call__(scope, receive, send) as the ASGI entry point.
    """

    scopes = {"http"}

    def __init__(self, app: ASGIApp | None = None) -> None:
        self.app = app  # type: ignore[assignment]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Execute the ASGI middleware — entry point per request."""
        # Extract idempotency key from headers (ASGI stores header names as lowercase bytes).
        headers = dict((k.decode().lower(), v.decode()) for k, v in scope.get("headers", []))
        key = headers.get(_IDEMPOTENCY_KEY_HEADER, "")

        if not key:
            await self.app(scope, receive, send)  # type: ignore[attr-defined]
            return

        request_id = scope.get("state", {}).get("request_id", "")

        # 1. Validate UUID format.
        if not _is_valid_uuid(key):
            body = error_response(
                "INVALID_REQUEST",
                "Idempotency-Key must be a valid UUID",
                request_id,
            )
            await _send_json_response(400, body, send)
            return

        # 2. Get db from Litestar app state (scope["app"] is the Litestar app).
        litestar_app = scope.get("app")
        db = getattr(litestar_app, "state", None) if litestar_app else None
        if db is not None:
            db = getattr(db, "db", None)

        if db is None:
            await self.app(scope, receive, send)  # type: ignore[attr-defined]
            return

        try:
            async with db.session() as session:
                # 3. Look up existing key.
                try:
                    existing = await IdempotencyRepo.get(session, key)
                except Exception as exc:
                    log.error("idempotency_get_failed", key=key, error=str(exc))
                    await self.app(scope, receive, send)  # type: ignore[attr-defined]
                    return

                if existing is not None:
                    if existing.status == "completed":
                        await _send_json_response(
                            existing.response_code or 200,
                            existing.response_body,
                            send,
                            extra_headers={"Idempotent-Replayed": "true"},
                        )
                        return

                    if existing.status == "pending":
                        body = error_response(
                            "IDEMPOTENCY_KEY_IN_PROGRESS",
                            "a request with this idempotency key is already in progress",
                            request_id,
                        )
                        await _send_json_response(409, body, send)
                        return

                    # status == "failed" → fall through to re-reserve.

                # 4. Reserve key.
                try:
                    await IdempotencyRepo.reserve(session, key)
                except Exception as exc:
                    if exc is ErrIdempotencyKeyExists or (
                        hasattr(exc, "__cause__") and exc.__cause__ is ErrIdempotencyKeyExists
                    ):
                        body = error_response(
                            "IDEMPOTENCY_KEY_IN_PROGRESS",
                            "a request with this idempotency key is already in progress",
                            request_id,
                        )
                        await _send_json_response(409, body, send)
                        return

                    log.error("idempotency_reserve_failed", key=key, error=str(exc))
                    await self.app(scope, receive, send)  # type: ignore[attr-defined]
                    return

                await session.commit()
        except Exception as exc:
            log.error("idempotency_session_error", key=key, error=str(exc))
            await self.app(scope, receive, send)  # type: ignore[attr-defined]
            return

        # 5. Store key in scope state for downstream access.
        scope.setdefault("state", {})["idempotency_key"] = key

        # 6. Execute handler, capture response messages for DB update, then forward.
        captured_start: Message | None = None
        captured_body_messages: list[Message] = []

        async def send_capture(message: Message) -> None:
            nonlocal captured_start
            if message["type"] == "http.response.start":
                captured_start = message
            elif message["type"] == "http.response.body":
                captured_body_messages.append(message)

        await self.app(scope, receive, send_capture)  # type: ignore[attr-defined]

        # Extract status and body bytes from captured messages.
        captured_status = captured_start["status"] if captured_start else 200
        captured_body = b"".join(m.get("body", b"") for m in captured_body_messages)

        # 7. Update idempotency record with the response.
        try:
            async with db.session() as session:
                if 200 <= captured_status < 300:
                    await IdempotencyRepo.complete(session, key, captured_status, captured_body)
                else:
                    await IdempotencyRepo.fail(session, key, captured_status, captured_body)
                await session.commit()
        except Exception as exc:
            log.error("idempotency_update_failed", key=key, error=str(exc))

        # 8. Forward captured response to the client.
        if captured_start is not None:
            await send(captured_start)
        for msg in captured_body_messages:
            await send(msg)

    # ASGIMiddleware requires handle() as an abstract method — stub only.
    async def handle(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, scope: Scope, receive: Receive, send: Send, next_app: ASGIApp
    ) -> None:
        pass


def get_idempotency_key(request: Request) -> str:
    """Retrieve idempotency key from request state."""
    return getattr(request.state, "idempotency_key", "")
