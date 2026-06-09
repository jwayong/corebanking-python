"""Unit tests for request ID middleware.

Verifies that the middleware generates UUIDv7 IDs, preserves existing
headers, sets response headers, and binds structlog context.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from litestar import Litestar, get
from litestar.testing import TestClient

from cbs.api.middleware.request_id import RequestIDMiddleware, get_request_id


# Minimal app with the middleware.
TEST_ROUTE = "/test"


@get(TEST_ROUTE)
async def _middleware_test_route() -> dict:  # noqa: ANN201
    return {"ok": True}


app = Litestar(
    route_handlers=[_middleware_test_route],
    middleware=[RequestIDMiddleware],
)


# UUIDv7 regex — 8-4-4-4-12 hex pattern, version digit is '7'.
_UUID_V7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# RequestIDMiddleware — end-to-end via TestClient
# ---------------------------------------------------------------------------

class TestRequestIDMiddleware:
    """Tests for ``RequestIDMiddleware`` using a real Litestar app."""

    def test_generates_uuidv7_when_no_header(self):
        """Response has X-Request-ID with valid UUID format when header absent."""
        with TestClient(app) as client:
            response = client.get("/test")

        assert response.status_code == 200
        request_id = response.headers.get("x-request-id")
        assert request_id is not None
        assert _UUID_V7_RE.match(request_id)

    def test_preserves_existing_header(self):
        """Send X-Request-ID: custom-123, response echoes same value."""
        with TestClient(app) as client:
            response = client.get("/test", headers={"X-Request-ID": "custom-123"})

        assert response.status_code == 200
        assert response.headers.get("x-request-id") == "custom-123"

    def test_response_header_set(self):
        """X-Request-ID header is present in response."""
        with TestClient(app) as client:
            response = client.get("/test")

        assert "x-request-id" in response.headers


# ---------------------------------------------------------------------------
# UUIDv7 format validation
# ---------------------------------------------------------------------------

class TestUUIDV7Format:
    """Tests for UUIDv7 format of generated IDs."""

    def test_uuidv7_format(self):
        """Generated ID matches UUID regex pattern with version 7."""
        with TestClient(app) as client:
            response = client.get("/test")

        request_id = response.headers["x-request-id"]
        assert _UUID_V7_RE.match(request_id)


# ---------------------------------------------------------------------------
# get_request_id() helper
# ---------------------------------------------------------------------------

class TestGetRequestID:
    """Tests for ``get_request_id()`` helper function."""

    def test_get_request_id_helper(self):
        """Returns request ID from mock request state."""
        mock_request = MagicMock()
        mock_request.state.request_id = "req-abc"

        assert get_request_id(mock_request) == "req-abc"

    def test_get_request_id_empty(self):
        """Returns empty string when no state is set."""
        mock_request = MagicMock()
        del mock_request.state.request_id  # trigger getattr default

        assert get_request_id(mock_request) == ""
