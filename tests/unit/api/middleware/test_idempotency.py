"""Unit tests for Idempotency-Key middleware.

Verifies key validation, caching/replay, pending/failed states, concurrent
conflicts, and graceful DB error handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from litestar import Litestar, Response, post
from litestar.testing import TestClient

from cbs.api.middleware.idempotency import (
    IdempotencyMiddleware,
    get_idempotency_key,
)
from cbs.domain.errors import ErrIdempotencyKeyExists


# ---------------------------------------------------------------------------
# Helpers: mock DB + session
# ---------------------------------------------------------------------------

def _make_mock_db(session=None):
    """Create a mock Database with configurable session."""
    if session is None:
        session = AsyncMock()

    mock_db = MagicMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=session)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_db.session.return_value = acm
    return mock_db


TEST_UUID = "0191a2b3-c4d5-7e6f-8a9b-0c1d2e3f4a5f"


# ---------------------------------------------------------------------------
# Test app factory — creates fresh app per test with mocked DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_db():
    return _make_mock_db()


@pytest.fixture()
def app(mock_db):
    @post("/test")
    async def _handler() -> dict:  # noqa: ANN201, C901
        return {"status": "success", "data": {"id": "123"}}

    test_app = Litestar(
        route_handlers=[_handler],
        middleware=[IdempotencyMiddleware],
    )
    test_app.state.db = mock_db
    return test_app


# Tests that need a /fail route use this fixture.

@pytest.fixture()
def app_with_fail(mock_db):
    @post("/test")
    async def _handler() -> dict:  # noqa: ANN201, C901
        return {"status": "success", "data": {"id": "123"}}

    @post("/fail")
    async def _fail_handler() -> Response:  # noqa: ANN201, C901
        return Response(content="bad", status_code=400)

    test_app = Litestar(
        route_handlers=[_handler, _fail_handler],
        middleware=[IdempotencyMiddleware],
    )
    test_app.state.db = mock_db
    return test_app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIdempotencyMiddleware:
    """Tests for ``IdempotencyMiddleware``."""

    def test_no_header_passes_through(self, app: Litestar):
        """No Idempotency-Key header → handler runs normally."""
        with TestClient(app) as client:
            response = client.post("/test")

        assert response.status_code == 201  # POST returns 201 Created by default

    def test_invalid_uuid_returns_400(self, app: Litestar):
        """Invalid UUID format → 400 with INVALID_REQUEST."""
        with TestClient(app) as client:
            response = client.post("/test", headers={"Idempotency-Key": "not-a-uuid"})

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_REQUEST"

    def test_new_key_reserves_and_completes(self, app: Litestar, mock_db):
        """New key → reserve() called, then complete() on 2xx response."""
        # mock_db is used via the app fixture; referenced here to satisfy pytest.

        with patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.get",
            new_callable=AsyncMock, return_value=None,
        ) as mock_get, patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.reserve",
            new_callable=AsyncMock, return_value=None,
        ) as mock_reserve, patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.complete",
            new_callable=AsyncMock, return_value=None,
        ) as mock_complete:
            with TestClient(app) as client:
                response = client.post("/test", headers={"Idempotency-Key": TEST_UUID})

        assert response.status_code == 201
        mock_get.assert_called_once()
        mock_reserve.assert_called_once()
        mock_complete.assert_called_once()

    def test_failed_handler_marks_failed(self, app_with_fail: Litestar, mock_db):
        """Handler returns 400 → fail() called with status 400."""
        # mock_db used via app_with_fail fixture.

        with patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.get",
            new_callable=AsyncMock, return_value=None,
        ), patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.reserve",
            new_callable=AsyncMock, return_value=None,
        ), patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.fail",
            new_callable=AsyncMock, return_value=None,
        ) as mock_fail:
            with TestClient(app_with_fail) as client:
                response = client.post("/fail", headers={"Idempotency-Key": TEST_UUID})

        assert response.status_code == 400
        mock_fail.assert_called_once()

    def test_completed_key_replays_response(self, app: Litestar, mock_db):
        """Completed key → replays cached response with Idempotent-Replayed header."""
        # mock_db used via app fixture.
        cached_body = b'{"status": "success", "data": {"id": "123"}}'

        with patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.get",
            new_callable=AsyncMock,
            return_value=MagicMock(
                status="completed", response_code=201, response_body=cached_body
            ),
        ):
            with TestClient(app) as client:
                response = client.post("/test", headers={"Idempotency-Key": TEST_UUID})

        assert response.status_code == 201
        assert response.headers.get("Idempotent-Replayed") == "true"

    def test_pending_key_returns_409(self, app: Litestar, mock_db):
        """Pending key → 409 IDEMPOTENCY_KEY_IN_PROGRESS."""
        # mock_db used via app fixture.

        with patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.get",
            new_callable=AsyncMock,
            return_value=MagicMock(status="pending"),
        ):
            with TestClient(app) as client:
                response = client.post("/test", headers={"Idempotency-Key": TEST_UUID})

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "IDEMPOTENCY_KEY_IN_PROGRESS"

    def test_concurrent_reserve_returns_409(self, app: Litestar, mock_db):
        """Reserve raises ErrIdempotencyKeyExists → 409."""
        # mock_db used via app fixture.

        with patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.get",
            new_callable=AsyncMock, return_value=None,
        ), patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.reserve",
            new_callable=AsyncMock, side_effect=ErrIdempotencyKeyExists,
        ):
            with TestClient(app) as client:
                response = client.post("/test", headers={"Idempotency-Key": TEST_UUID})

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "IDEMPOTENCY_KEY_IN_PROGRESS"

    def test_failed_key_allows_retry(self, app: Litestar, mock_db):
        """Failed key → re-reserves and allows request to proceed."""
        # mock_db used via app fixture.

        with patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.get",
            new_callable=AsyncMock, return_value=MagicMock(status="failed"),
        ), patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.reserve",
            new_callable=AsyncMock, return_value=None,
        ), patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.complete",
            new_callable=AsyncMock, return_value=None,
        ):
            with TestClient(app) as client:
                response = client.post("/test", headers={"Idempotency-Key": TEST_UUID})

        assert response.status_code == 201

    def test_db_error_graceful_degradation(self, app: Litestar, mock_db):
        """DB get() raises → request passes through to handler."""
        # mock_db used via app fixture.

        with patch(
            "cbs.api.middleware.idempotency.IdempotencyRepo.get",
            new_callable=AsyncMock, side_effect=RuntimeError("db down"),
        ):
            with TestClient(app) as client:
                response = client.post("/test", headers={"Idempotency-Key": TEST_UUID})

        assert response.status_code == 201


# ---------------------------------------------------------------------------
# get_idempotency_key() helper
# ---------------------------------------------------------------------------

class TestGetIdempotencyKey:
    """Tests for ``get_idempotency_key()`` helper."""

    def test_get_idempotency_key_helper(self):
        """Returns key from mock request state."""
        mock_request = MagicMock()
        mock_request.state.idempotency_key = TEST_UUID

        assert get_idempotency_key(mock_request) == TEST_UUID

    def test_get_idempotency_key_empty(self):
        """Returns empty string when not set."""
        mock_request = MagicMock()
        del mock_request.state.idempotency_key

        assert get_idempotency_key(mock_request) == ""
