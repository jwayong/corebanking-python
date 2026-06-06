"""Unit tests for SystemAccountRepo (PostgreSQL system account storage).

Tests verify existence checks, lookups by code, and batch insertion
using mocked async sessions.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from cbs.store.postgres.system_account_repo import CreatedSystemAccount, SystemAccountRepo
from tests.unit.store.postgres.fixtures import make_mock_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sys_account(
    tb_account_id=b"\x01\x02", currency="USD", ledger=840,
    code=1, name="Liquidity Pool",
):
    return CreatedSystemAccount(
        tb_account_id=tb_account_id, currency=currency,
        ledger=ledger, code=code, name=name,
    )


# ---------------------------------------------------------------------------
# SystemAccountRepo.exists()
# ---------------------------------------------------------------------------

class TestSystemAccountRepoExists:
    async def test_true(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=3)

        result = await SystemAccountRepo.exists(mock_session, "USD")
        assert result is True

    async def test_false(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        result = await SystemAccountRepo.exists(mock_session, "EUR")
        assert result is False

    async def test_sends_currency_param(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        await SystemAccountRepo.exists(mock_session, "GBP")
        params = mock_session.execute.call_args[0][1]
        assert params["currency"] == "GBP"

    async def test_uses_count_query(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        await SystemAccountRepo.exists(mock_session, "USD")
        sql = str(mock_session.execute.call_args[0][0])
        assert "COUNT(*)" in sql
        assert "system_accounts" in sql


# ---------------------------------------------------------------------------
# SystemAccountRepo.get_by_code()
# ---------------------------------------------------------------------------

class TestSystemAccountRepoGetByCode:
    async def test_found(self, mock_session):
        tb_id = b"\xAA\xBB\xCC"
        row = MagicMock()
        row.tb_account_id = tb_id
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await SystemAccountRepo.get_by_code(mock_session, "USD", 1)
        assert result == tb_id

    async def test_not_found_returns_none(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        result = await SystemAccountRepo.get_by_code(mock_session, "USD", 99)
        assert result is None

    async def test_sends_currency_and_code(self, mock_session):
        row = MagicMock()
        row.tb_account_id = b"\x01"
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await SystemAccountRepo.get_by_code(mock_session, "EUR", 5)
        params = mock_session.execute.call_args[0][1]
        assert params["currency"] == "EUR"
        assert params["code"] == 5

    async def test_uses_correct_select(self, mock_session):
        row = MagicMock()
        row.tb_account_id = b"\x01"
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await SystemAccountRepo.get_by_code(mock_session, "USD", 1)
        sql = str(mock_session.execute.call_args[0][0])
        assert "SELECT tb_account_id" in sql
        assert "FROM system_accounts" in sql


# ---------------------------------------------------------------------------
# SystemAccountRepo.insert_batch()
# ---------------------------------------------------------------------------

class TestSystemAccountRepoInsertBatch:
    async def test_single_account(self, mock_session):
        accounts = [_make_sys_account(code=1, name="Liquidity")]

        await SystemAccountRepo.insert_batch(mock_session, accounts)

        assert mock_session.execute.call_count == 1
        params = mock_session.execute.call_args[0][1]
        assert params["code"] == 1
        assert params["name"] == "Liquidity"

    async def test_multiple_accounts(self, mock_session):
        accounts = [
            _make_sys_account(code=1, name="Liquidity"),
            _make_sys_account(code=2, name="Fee Income", tb_account_id=b"\x03"),
            _make_sys_account(code=3, name="Interest", tb_account_id=b"\x04"),
        ]

        await SystemAccountRepo.insert_batch(mock_session, accounts)

        assert mock_session.execute.call_count == 3
        for i, acct in enumerate(accounts):
            call_params = mock_session.execute.call_args_list[i][0][1]
            assert call_params["code"] == acct.code

    async def test_empty_list(self, mock_session):
        await SystemAccountRepo.insert_batch(mock_session, [])

        assert mock_session.execute.call_count == 0

    async def test_uses_transaction_begin(self, mock_session):
        accounts = [_make_sys_account()]

        await SystemAccountRepo.insert_batch(mock_session, accounts)
        mock_session.begin.assert_called_once()

    async def test_uses_on_conflict_do_nothing(self, mock_session):
        accounts = [_make_sys_account()]

        await SystemAccountRepo.insert_batch(mock_session, accounts)
        sql = str(mock_session.execute.call_args[0][0])
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    async def test_sends_all_fields(self, mock_session):
        acct = _make_sys_account(
            tb_account_id=b"\xFF\xEE", currency="EUR", ledger=978,
            code=10, name="Euro Pool",
        )

        await SystemAccountRepo.insert_batch(mock_session, [acct])
        params = mock_session.execute.call_args[0][1]
        assert params["tb_account_id"] == b"\xFF\xEE"
        assert params["currency"] == "EUR"
        assert params["ledger"] == 978
        assert params["code"] == 10
