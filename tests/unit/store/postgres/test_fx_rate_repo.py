"""Unit tests for fx_rate_repo module-level functions.

Tests verify INSERT/SELECT SQL, row mapping, and ErrNotFound handling
using mocked async sessions.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from cbs.domain.errors import ErrNotFound
from cbs.store.postgres.fx_rate_repo import FXRate, get_by_effective_at, get_latest, insert
from tests.unit.store.postgres.fixtures import make_mock_result, make_mock_row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fx_row(rate=1.085, effective_at=None):
    if effective_at is None:
        effective_at = datetime.now()
    return make_mock_row(rate=rate, effective_at=effective_at)


# ---------------------------------------------------------------------------
# get_latest()
# ---------------------------------------------------------------------------

class TestGetLatest:
    async def test_found(self, mock_session):
        now = datetime.now()
        row = _make_fx_row(rate=1.085, effective_at=now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_latest(mock_session, "USD", "EUR")

        assert isinstance(result, FXRate)
        assert result.sell_currency == "USD"
        assert result.buy_currency == "EUR"
        assert result.rate == 1.085
        assert result.effective_at == now

    async def test_not_found_raises_err_not_found(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        with pytest.raises(Exception) as exc_info:
            await get_latest(mock_session, "USD", "JPY")
        assert exc_info.value is ErrNotFound

    async def test_sends_correct_params(self, mock_session):
        row = _make_fx_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_latest(mock_session, "GBP", "CHF")
        params = mock_session.execute.call_args[0][1]
        assert params["sell"] == "GBP"
        assert params["buy"] == "CHF"

    async def test_uses_order_by_desc_limit(self, mock_session):
        row = _make_fx_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_latest(mock_session, "USD", "EUR")
        sql = str(mock_session.execute.call_args[0][0])
        assert "ORDER BY effective_at DESC" in sql
        assert "LIMIT 1" in sql

    async def test_rate_cast_to_float(self, mock_session):
        row = _make_fx_row(rate=1.234567)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_latest(mock_session, "USD", "EUR")
        assert isinstance(result.rate, float)

    async def test_pair_key_method(self, mock_session):
        row = _make_fx_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_latest(mock_session, "USD", "EUR")
        assert result.pair_key() == "USD/EUR"


# ---------------------------------------------------------------------------
# insert()
# ---------------------------------------------------------------------------

class TestInsert:
    async def test_success(self, mock_session):
        rate = FXRate(
            sell_currency="USD", buy_currency="EUR",
            rate=1.085, effective_at=datetime(2025, 6, 1),
        )
        await insert(mock_session, rate)

        mock_session.execute.assert_called_once()
        params = mock_session.execute.call_args[0][1]
        assert params["sell"] == "USD"
        assert params["buy"] == "EUR"
        assert params["rate"] == 1.085

    async def test_uses_insert_sql(self, mock_session):
        rate = FXRate(
            sell_currency="GBP", buy_currency="JPY",
            rate=180.5, effective_at=datetime(2025, 7, 1),
        )
        await insert(mock_session, rate)

        sql = str(mock_session.execute.call_args[0][0])
        assert "INSERT INTO exchange_rates" in sql

    async def test_sends_effective_at(self, mock_session):
        dt = datetime(2025, 3, 15, 12, 0, 0)
        rate = FXRate(
            sell_currency="EUR", buy_currency="GBP",
            rate=0.85, effective_at=dt,
        )
        await insert(mock_session, rate)

        params = mock_session.execute.call_args[0][1]
        assert params["effective_at"] == dt


# ---------------------------------------------------------------------------
# get_by_effective_at()
# ---------------------------------------------------------------------------

class TestGetByEffectiveAt:
    async def test_found(self, mock_session):
        at = datetime(2025, 6, 15)
        row = _make_fx_row(rate=1.09, effective_at=datetime(2025, 6, 1))
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_by_effective_at(mock_session, "USD", "EUR", at)

        assert isinstance(result, FXRate)
        assert result.rate == 1.09
        assert result.sell_currency == "USD"
        assert result.buy_currency == "EUR"

    async def test_not_found_raises_err_not_found(self, mock_session):
        at = datetime(2025, 6, 15)
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        with pytest.raises(Exception) as exc_info:
            await get_by_effective_at(mock_session, "USD", "EUR", at)
        assert exc_info.value is ErrNotFound

    async def test_sends_time_filter(self, mock_session):
        at = datetime(2025, 1, 1)
        row = _make_fx_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_by_effective_at(mock_session, "USD", "EUR", at)
        params = mock_session.execute.call_args[0][1]
        assert params["at"] == at

    async def test_uses_effective_at_le_filter(self, mock_session):
        at = datetime(2025, 6, 15)
        row = _make_fx_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_by_effective_at(mock_session, "USD", "EUR", at)
        sql = str(mock_session.execute.call_args[0][0])
        assert "effective_at <= :at" in sql

    async def test_uses_order_by_desc_limit(self, mock_session):
        at = datetime(2025, 6, 15)
        row = _make_fx_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_by_effective_at(mock_session, "USD", "EUR", at)
        sql = str(mock_session.execute.call_args[0][0])
        assert "ORDER BY effective_at DESC" in sql
        assert "LIMIT 1" in sql
