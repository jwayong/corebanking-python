"""Unit tests for Litestar DI providers.

Verifies that each provider resolves the correct dependency from
request.app.state, and that provide_db_session yields an AsyncSession.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cbs.api.deps import (
    provide_account_service,
    provide_balance_service,
    provide_config,
    provide_customer_service,
    provide_db_session,
    provide_fee_service,
    provide_fx_service,
    provide_hold_service,
    provide_loan_service,
    provide_settlement_service,
    provide_transfer_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(state: dict | None = None) -> MagicMock:
    """Create a mock Request with app.state populated from *state* dict."""
    request = MagicMock()
    if state is not None:
        for key, value in state.items():
            setattr(request.app.state, key, value)
    return request


# ---------------------------------------------------------------------------
# Service providers — each resolves from request.app.state.services[name]
# ---------------------------------------------------------------------------

class TestProvideAccountService:
    """Tests for ``provide_account_service()``."""

    async def test_provide_account_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"account": svc}})

        result = await provide_account_service(request)
        assert result is svc


class TestProvideBalanceService:
    """Tests for ``provide_balance_service()``."""

    async def test_provide_balance_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"balance": svc}})

        result = await provide_balance_service(request)
        assert result is svc


class TestProvideCustomerService:
    """Tests for ``provide_customer_service()``."""

    async def test_provide_customer_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"customer": svc}})

        result = await provide_customer_service(request)
        assert result is svc


class TestProvideFeeService:
    """Tests for ``provide_fee_service()``."""

    async def test_provide_fee_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"fee": svc}})

        result = await provide_fee_service(request)
        assert result is svc


class TestProvideFXService:
    """Tests for ``provide_fx_service()``."""

    async def test_provide_fx_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"fx": svc}})

        result = await provide_fx_service(request)
        assert result is svc


class TestProvideHoldService:
    """Tests for ``provide_hold_service()``."""

    async def test_provide_hold_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"hold": svc}})

        result = await provide_hold_service(request)
        assert result is svc


class TestProvideLoanService:
    """Tests for ``provide_loan_service()``."""

    async def test_provide_loan_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"loan": svc}})

        result = await provide_loan_service(request)
        assert result is svc


class TestProvideSettlementService:
    """Tests for ``provide_settlement_service()``."""

    async def test_provide_settlement_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"settlement": svc}})

        result = await provide_settlement_service(request)
        assert result is svc


class TestProvideTransferService:
    """Tests for ``provide_transfer_service()``."""

    async def test_provide_transfer_service(self):
        svc = MagicMock()
        request = _make_request({"services": {"transfer": svc}})

        result = await provide_transfer_service(request)
        assert result is svc


# ---------------------------------------------------------------------------
# DB session provider — yields AsyncSession from db.session() context manager
# ---------------------------------------------------------------------------

class TestProvideDBSession:
    """Tests for ``provide_db_session()``."""

    async def test_provide_db_session(self):
        """Yields AsyncSession from mock db.session() async context manager."""
        session = MagicMock(spec=AsyncSession)

        # Mock db.session() to return an async context manager that yields session
        mock_db = MagicMock()
        acm = AsyncMock()
        acm.__aenter__.return_value = session
        acm.__aexit__.return_value = None
        mock_db.session.return_value = acm

        request = _make_request({"db": mock_db})

        gen = provide_db_session(request)
        yielded = await gen.__anext__()  # type: ignore[attr-defined]
        assert yielded is session

        # Exhaust generator to trigger cleanup
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()  # type: ignore[attr-defined]

        mock_db.session.assert_called_once()
        acm.__aenter__.assert_awaited_once()
        acm.__aexit__.assert_awaited_once()


# ---------------------------------------------------------------------------
# Config provider — returns CBSConfig from state
# ---------------------------------------------------------------------------

class TestProvideConfig:
    """Tests for ``provide_config()``."""

    async def test_provide_config(self):
        config = MagicMock()
        request = _make_request({"config": config})

        result = await provide_config(request)
        assert result is config
