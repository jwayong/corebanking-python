"""Unit tests for loan_repo module-level functions.

Tests verify INSERT/SELECT SQL, CTE-based repayment logic, arrears
status updates, and ErrNotFound handling using mocked async sessions.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy.exc import NoResultFound

from cbs.domain.errors import ErrNotFound
from cbs.store.postgres.loan_repo import (
    LoanDetailRecord,
    create,
    get_by_account_id,
    reduce_outstanding,
    set_disbursed_at,
    update_arrears_status,
)
from tests.unit.store.postgres.fixtures import make_mock_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_loan_row(
    id=1, account_id=42, principal=100_000, outstanding=100_000,
    interest_rate=0.05, term_months=12, disbursed_at=None,
    maturity_date=None, next_payment_due=None, payment_amount=8_500,
    arrears_amount=0, status="active",
):
    return (
        id, account_id, principal, outstanding, interest_rate, term_months,
        disbursed_at, maturity_date, next_payment_due, payment_amount,
        arrears_amount, status,
    )


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

class TestLoanCreate:
    async def test_success(self, mock_session):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(5, now))

        rec = LoanDetailRecord(
            account_id=42, principal=50_000, outstanding=50_000,
            interest_rate=0.06, term_months=24, status="active",
        )
        result = await create(mock_session, rec)

        assert result.id == 5
        assert result.created_at == now
        assert rec.id == 5

    async def test_no_rows_raises_runtime_error(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        rec = LoanDetailRecord(account_id=1, principal=1000, outstanding=1000, status="active")
        with pytest.raises(RuntimeError, match="loan_details insert returned no rows"):
            await create(mock_session, rec)

    async def test_uses_insert_sql(self, mock_session):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1, now))

        rec = LoanDetailRecord(account_id=1, principal=100, outstanding=100, status="active")
        await create(mock_session, rec)

        sql = str(mock_session.execute.call_args[0][0])
        assert "INSERT INTO loan_details" in sql

    async def test_sends_all_fields(self, mock_session):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1, now))

        rec = LoanDetailRecord(
            account_id=99, principal=200_000, outstanding=200_000,
            interest_rate=0.075, term_months=36, disbursed_at=None,
            maturity_date=datetime(2030, 1, 1), next_payment_due=datetime(2025, 8, 1),
            payment_amount=6_500, status="active",
        )
        await create(mock_session, rec)

        params = mock_session.execute.call_args[0][1]
        assert params["account_id"] == 99
        assert params["principal"] == 200_000
        assert params["interest_rate"] == 0.075

    async def test_returning_id_and_created_at(self, mock_session):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1, now))

        rec = LoanDetailRecord(account_id=1, principal=100, outstanding=100, status="active")
        await create(mock_session, rec)

        sql = str(mock_session.execute.call_args[0][0])
        assert "RETURNING id, created_at" in sql


# ---------------------------------------------------------------------------
# get_by_account_id()
# ---------------------------------------------------------------------------

class TestLoanGetByAccountId:
    async def test_found(self, mock_session):
        row = _make_loan_row(id=3, account_id=42, outstanding=75_000)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_by_account_id(mock_session, 42)

        assert result is not None
        assert result.id == 3
        assert result.account_id == 42
        assert result.outstanding == 75_000

    async def test_not_found_returns_none(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        result = await get_by_account_id(mock_session, 999)
        assert result is None

    async def test_sends_account_id_param(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        await get_by_account_id(mock_session, 77)
        params = mock_session.execute.call_args[0][1]
        assert params["account_id"] == 77

    async def test_uses_correct_select(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        await get_by_account_id(mock_session, 1)
        sql = str(mock_session.execute.call_args[0][0])
        assert "FROM loan_details" in sql

    async def test_maps_all_fields(self, mock_session):
        now = datetime.now()
        row = _make_loan_row(
            id=10, account_id=55, principal=200_000, outstanding=150_000,
            interest_rate=0.08, term_months=48, disbursed_at=now,
            maturity_date=datetime(2030, 6, 1), next_payment_due=now,
            payment_amount=5_000, arrears_amount=1_000, status="in_arrears",
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_by_account_id(mock_session, 55)
        assert result.interest_rate == 0.08
        assert result.term_months == 48
        assert result.arrears_amount == 1_000
        assert result.status == "in_arrears"


# ---------------------------------------------------------------------------
# reduce_outstanding()
# ---------------------------------------------------------------------------

class TestLoanReduceOutstanding:
    async def test_normal_repayment(self, mock_session):
        now = datetime.now()
        row = _make_loan_row(id=1, account_id=42, outstanding=90_000)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await reduce_outstanding(mock_session, 10_000, 42, now)

        assert result is not None
        assert result.outstanding == 90_000

    async def test_auto_close_at_zero(self, mock_session):
        now = datetime.now()
        row = _make_loan_row(id=1, account_id=42, outstanding=0, status="closed")
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await reduce_outstanding(mock_session, 10_000, 42, now)

        assert result.status == "closed"
        assert result.outstanding == 0

    async def test_insufficient_balance_raises_err_not_found(self, mock_session):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        with pytest.raises(Exception) as exc_info:
            await reduce_outstanding(mock_session, 999_999, 42, now)
        assert exc_info.value is ErrNotFound

    async def test_noresultfound_raises_err_not_found(self, mock_session):
        now = datetime.now()
        mock_session.execute.side_effect = NoResultFound("no result")

        with pytest.raises(Exception) as exc_info:
            await reduce_outstanding(mock_session, 10_000, 42, now)
        assert exc_info.value is ErrNotFound

    async def test_sends_correct_params(self, mock_session):
        now = datetime.now()
        row = _make_loan_row(id=1, account_id=42, outstanding=90_000)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await reduce_outstanding(mock_session, 5_000, 42, now)
        params = mock_session.execute.call_args[0][1]
        assert params["repayment_amount"] == 5_000
        assert params["account_id"] == 42
        assert params["payment_date"] == now

    async def test_uses_cte_pattern(self, mock_session):
        now = datetime.now()
        row = _make_loan_row(id=1, account_id=42, outstanding=90_000)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await reduce_outstanding(mock_session, 5_000, 42, now)
        sql = str(mock_session.execute.call_args[0][0])
        assert "WITH updated AS" in sql or "WITH updated as" in sql.lower()

    async def test_uses_outstanding_guard(self, mock_session):
        now = datetime.now()
        row = _make_loan_row(id=1, account_id=42, outstanding=90_000)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await reduce_outstanding(mock_session, 5_000, 42, now)
        sql = str(mock_session.execute.call_args[0][0])
        assert "outstanding >= :repayment_amount" in sql


# ---------------------------------------------------------------------------
# update_arrears_status()
# ---------------------------------------------------------------------------

class TestLoanUpdateArrearsStatus:
    async def test_updates_rows(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=3)

        count = await update_arrears_status(mock_session)
        assert count == 3

    async def test_no_rows_updated(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=0)

        count = await update_arrears_status(mock_session)
        assert count == 0

    async def test_none_rowcount_defaults_to_zero(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=None)

        count = await update_arrears_status(mock_session)
        assert count == 0

    async def test_uses_update_sql(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=0)

        await update_arrears_status(mock_session)
        sql = str(mock_session.execute.call_args[0][0])
        assert "UPDATE loan_details" in sql
        assert "status = 'in_arrears'" in sql

    async def test_filters_on_payment_due_and_outstanding(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=0)

        await update_arrears_status(mock_session)
        sql = str(mock_session.execute.call_args[0][0])
        assert "next_payment_due < CURRENT_DATE" in sql
        assert "outstanding > 0" in sql


# ---------------------------------------------------------------------------
# set_disbursed_at()
# ---------------------------------------------------------------------------

class TestLoanSetDisbursedAt:
    async def test_success(self, mock_session):
        now = datetime.now()
        await set_disbursed_at(mock_session, 42, now)

        mock_session.execute.assert_called_once()
        params = mock_session.execute.call_args[0][1]
        assert params["disbursed_at"] == now
        assert params["account_id"] == 42

    async def test_uses_update_sql(self, mock_session):
        now = datetime.now()
        await set_disbursed_at(mock_session, 42, now)

        sql = str(mock_session.execute.call_args[0][0])
        assert "UPDATE loan_details" in sql
        assert "SET disbursed_at = :disbursed_at" in sql
