"""Unit tests for global exception handlers.

Verifies that each domain exception type is mapped to the correct HTTP
status code and error envelope format.
"""

from __future__ import annotations

import structlog
from structlog.testing import LogCapture

import pytest

from litestar import Litestar, get
from litestar.testing import TestClient

from cbs.api.middleware.error_handler import EXCEPTION_HANDLERS
from cbs.domain.errors import (
    AccountClosedError,
    FXRateStaleError,
    IdempotencyConflictError,
    InsufficientBalanceError,
    NotFoundError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Fixture: isolate structlog config per test so we don't pollute other tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def log_capture():
    """Yield a fresh LogCapture and restore structlog config after the test."""
    from structlog import get_config
    original_processors = list(get_config()["processors"])

    capture = LogCapture()
    structlog.configure(processors=[capture])

    yield capture

    # Restore original config.
    structlog.configure(processors=original_processors)


# ---------------------------------------------------------------------------
# Test app with exception handlers and request_id middleware for tracing
# ---------------------------------------------------------------------------

@get("/validation")
async def _raise_validation() -> dict:  # noqa: ANN201, C901
    raise ValidationError("bad input")


@get("/notfound")
async def _raise_notfound() -> dict:  # noqa: ANN201, C901
    raise NotFoundError("nope")


@get("/account-closed")
async def _raise_account_closed() -> dict:  # noqa: ANN201, C901
    raise AccountClosedError("closed")


@get("/insufficient-balance")
async def _raise_insufficient_balance() -> dict:  # noqa: ANN201, C901
    raise InsufficientBalanceError("insufficient balance", available=100, required=500)


@get("/idempotency-conflict")
async def _raise_idempotency_conflict() -> dict:  # noqa: ANN201, C901
    raise IdempotencyConflictError("key exists")


@get("/fx-rate-stale")
async def _raise_fx_rate_stale() -> dict:  # noqa: ANN201, C901
    raise FXRateStaleError("rate stale")


@get("/unhandled")
async def _raise_unhandled() -> dict:  # noqa: ANN201, C901
    raise RuntimeError("oops")


@pytest.fixture()
def app():
    return Litestar(
        route_handlers=[
            _raise_validation,
            _raise_notfound,
            _raise_account_closed,
            _raise_insufficient_balance,
            _raise_idempotency_conflict,
            _raise_fx_rate_stale,
            _raise_unhandled,
        ],
        exception_handlers=EXCEPTION_HANDLERS,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExceptionHandler:
    """Tests for exception-to-HTTP mapping."""

    def test_validation_error_returns_400(self, app: Litestar):
        """ValidationError → 400 with INVALID_REQUEST code."""
        with TestClient(app) as client:
            response = client.get("/validation")

        assert response.status_code == 400
        data = response.json()
        assert data["status"] == "error"
        assert data["error"]["code"] == "INVALID_REQUEST"

    def test_not_found_error_returns_404(self, app: Litestar):
        """NotFoundError → 404 with NOT_FOUND code."""
        with TestClient(app) as client:
            response = client.get("/notfound")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "NOT_FOUND"

    def test_account_closed_returns_409(self, app: Litestar):
        """AccountClosedError → 409 with ACCOUNT_CLOSED code."""
        with TestClient(app) as client:
            response = client.get("/account-closed")

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "ACCOUNT_CLOSED"

    def test_insufficient_balance_returns_409(self, app: Litestar):
        """InsufficientBalanceError → 409 with INSUFFICIENT_BALANCE code."""
        with TestClient(app) as client:
            response = client.get("/insufficient-balance")

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "INSUFFICIENT_BALANCE"

    def test_idempotency_conflict_returns_409(self, app: Litestar):
        """IdempotencyConflictError → 409."""
        with TestClient(app) as client:
            response = client.get("/idempotency-conflict")

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_fx_rate_stale_returns_503(self, app: Litestar):
        """FXRateStaleError → 503."""
        with TestClient(app) as client:
            response = client.get("/fx-rate-stale")

        assert response.status_code == 503
        data = response.json()
        assert data["error"]["code"] == "FX_RATE_STALE"

    def test_unhandled_exception_returns_500(self, app: Litestar, log_capture: LogCapture):
        """Unhandled RuntimeError → 500 with INTERNAL_ERROR code."""
        with TestClient(app) as client:
            response = client.get("/unhandled")

        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"

    def test_error_envelope_format(self, app: Litestar):
        """Response has status, error.code, error.message."""
        with TestClient(app) as client:
            response = client.get("/validation")

        data = response.json()
        assert data["status"] == "error"
        assert "code" in data["error"]
        assert "message" in data["error"]

    def test_request_id_in_error(self, app: Litestar):
        """Response includes request_id field."""
        with TestClient(app) as client:
            response = client.get("/validation")

        data = response.json()
        assert "request_id" in data

    def test_no_stack_trace_in_500(self, app: Litestar):
        """Response body does NOT contain 'Traceback' for 500 errors."""
        with TestClient(app) as client:
            response = client.get("/unhandled")

        assert "Traceback" not in response.text
        assert "traceback" not in response.json()
