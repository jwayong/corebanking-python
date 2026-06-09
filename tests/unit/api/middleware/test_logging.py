"""Unit tests for structured request/response logging middleware.

Verifies that the middleware logs every HTTP request with method, path,
status, duration_ms, and request_id fields at the correct log level.
"""

from __future__ import annotations

import structlog
from structlog.testing import LogCapture

import pytest

from litestar import Litestar, Response, get
from litestar.testing import TestClient

from cbs.api.middleware.logging import LoggingMiddleware


# ---------------------------------------------------------------------------
# Fixture: isolate structlog config per test so we don't pollute other tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def log_capture():
    """Yield a fresh LogCapture and restore structlog config after the test."""
    # Save current processors so we can restore them.
    from structlog import get_config
    original_processors = list(get_config()["processors"])

    capture = LogCapture()
    structlog.configure(processors=[capture])

    yield capture

    # Restore original config.
    structlog.configure(processors=original_processors)


# ---------------------------------------------------------------------------
# Test app with logging middleware (created lazily per test to use fixture config)
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    @get("/test")
    async def _ok_handler() -> dict:  # noqa: ANN201, C901
        return {"ok": True}

    @get("/notfound")
    async def _notfound_handler() -> Response:  # noqa: ANN201, C901
        return Response(content="not found", status_code=404)

    @get("/error")
    async def _error_handler() -> Response:  # noqa: ANN201, C901
        return Response(content="fail", status_code=500)

    return Litestar(
        route_handlers=[_ok_handler, _notfound_handler, _error_handler],
        middleware=[LoggingMiddleware],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoggingMiddleware:
    """Tests for ``LoggingMiddleware`` using Litestar TestClient."""

    def test_logs_request_fields(self, app: Litestar, log_capture: LogCapture):
        """Log entry contains method, path, status, duration_ms, request_id."""
        with TestClient(app) as client:
            client.get("/test")

        entry = next(e for e in log_capture.entries if e.get("event") == "http_request")
        assert entry["method"] == "GET"
        assert entry["path"] == "/test"
        assert entry["status"] == 200
        assert "duration_ms" in entry
        assert "request_id" in entry

    def test_captures_non_200_status(self, app: Litestar, log_capture: LogCapture):
        """Handler returning 404 logs status=404."""
        with TestClient(app) as client:
            client.get("/notfound")

        entry = next(e for e in log_capture.entries if e.get("event") == "http_request")
        assert entry["status"] == 404

    def test_defaults_to_200(self, app: Litestar, log_capture: LogCapture):
        """Handler with no explicit status logs status=200."""
        with TestClient(app) as client:
            client.get("/test")

        entry = next(e for e in log_capture.entries if e.get("event") == "http_request")
        assert entry["status"] == 200

    def test_500_logged_at_error_level(self, app: Litestar, log_capture: LogCapture):
        """500 response logged with error level."""
        with TestClient(app) as client:
            client.get("/error")

        entry = next(e for e in log_capture.entries if e.get("event") == "http_request")
        assert entry["status"] == 500
        assert entry["log_level"] == "error"

    def test_200_logged_at_info_level(self, app: Litestar, log_capture: LogCapture):
        """200 response logged with info level."""
        with TestClient(app) as client:
            client.get("/test")

        entry = next(e for e in log_capture.entries if e.get("event") == "http_request")
        assert entry["log_level"] == "info"

    def test_duration_ms_is_non_negative(self, app: Litestar, log_capture: LogCapture):
        """duration_ms >= 0 for any request (may be 0 for fast in-process requests)."""
        with TestClient(app) as client:
            client.get("/test")

        entry = next(e for e in log_capture.entries if e.get("event") == "http_request")
        assert entry["duration_ms"] >= 0
