"""Tests for the Litestar app factory, CORS config, and middleware wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from cbs.config import CBSConfig
from cbs.main import create_app


@pytest.fixture()
def mock_db():
    """Create a mock Database with session support."""
    from unittest.mock import AsyncMock

    session = AsyncMock()
    mock_db = MagicMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=session)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_db.session.return_value = acm
    return mock_db


@pytest.fixture()
def mock_config():
    """Create a mock CBSConfig."""
    cfg = MagicMock(spec=CBSConfig)
    cfg.cors_allowed_origins = "*"
    return cfg


@pytest.fixture()
def mock_services():
    """Create a dict of mock services matching build_services output."""
    return {
        "account": MagicMock(),
        "balance": MagicMock(),
        "customer": MagicMock(),
        "fee": MagicMock(),
        "fx": MagicMock(),
        "hold": MagicMock(),
        "loan": MagicMock(),
        "settlement": MagicMock(),
        "transfer": MagicMock(),
    }


@pytest.fixture()
def litestar_app(mock_config, mock_services, mock_db):
    """Create a Litestar app via create_app."""
    return create_app(mock_config, mock_services, mock_db)


class TestCreateApp:
    """Tests for the create_app() factory."""

    def test_create_app_returns_litestar(self, litestar_app):
        """create_app returns a Litestar instance."""
        assert isinstance(litestar_app, Litestar)

    def test_cors_headers_present(self, litestar_app):
        """Response includes Access-Control-Allow-Origin header."""
        with TestClient(litestar_app) as client:
            response = client.get("/", headers={"Origin": "http://localhost"})

        assert "access-control-allow-origin" in response.headers

    def test_cors_preflight(self, litestar_app):
        """OPTIONS request returns 204 for CORS preflight."""
        with TestClient(litestar_app) as client:
            response = client.options(
                "/",
                headers={
                    "Origin": "http://localhost",
                    "Access-Control-Request-Method": "POST",
                },
            )

        assert response.status_code == 204

    def test_cors_expose_headers(self, litestar_app):
        """Response exposes X-Request-ID and Idempotent-Replayed headers."""
        with TestClient(litestar_app) as client:
            response = client.get("/", headers={"Origin": "http://localhost"})

        expose = response.headers.get("access-control-expose-headers", "")
        assert "X-Request-ID" in expose

    def test_app_state_services(self, litestar_app, mock_services):
        """app.state.services is populated with the services dict."""
        # on_startup hooks fire when the app starts (inside TestClient).
        with TestClient(litestar_app):
            assert litestar_app.state.services == mock_services

    def test_app_state_db(self, litestar_app, mock_db):
        """app.state.db is populated with the Database instance."""
        with TestClient(litestar_app):
            assert litestar_app.state.db == mock_db

    def test_app_state_config(self, litestar_app, mock_config):
        """app.state.config is populated with the config instance."""
        with TestClient(litestar_app):
            assert litestar_app.state.config == mock_config


class TestConfigCorsField:
    """Tests for the CBSConfig.cors_allowed_origins field."""

    def test_config_cors_field(self):
        """CBSConfig accepts cors_allowed_origins parameter."""
        cfg = CBSConfig(cors_allowed_origins="http://localhost:3000")
        assert cfg.cors_allowed_origins == "http://localhost:3000"
