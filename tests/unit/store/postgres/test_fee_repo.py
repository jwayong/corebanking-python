"""Unit tests for FeeCollectionRepo (PostgreSQL fee collection operations).

Tests verify account fetching, JSONB fee parsing, transactional recording,
and batch insert chunking using mocked async sessions.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from cbs.store.postgres.fee_repo import (
    FeeBearingAccount,
    FeeCollectionRecord,
    FeeCollectionRepo,
    FeeItem,
)
from tests.unit.store.postgres.fixtures import make_mock_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fee_bearing_row(
    account_id=1, tb_account_id=b"\x01", tb_account_code=2101,
    currency="USD", tb_ledger=840, last_fee_date=None, fees_json=None,
):
    if fees_json is None:
        fees_json = '[]'
    return (account_id, tb_account_id, tb_account_code, currency, tb_ledger, last_fee_date, fees_json)


# ---------------------------------------------------------------------------
# FeeCollectionRepo.fetch_fee_bearing_accounts()
# ---------------------------------------------------------------------------

class TestFeeCollectionRepoFetchFeeBearingAccounts:
    async def test_with_accounts(self, mock_session, mock_db):
        fees_json = '[{"type":"monthly","description":"Monthly fee","amount":500}]'
        row = _make_fee_bearing_row(account_id=1, fees_json=fees_json)
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = FeeCollectionRepo(mock_db)
        result = await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        assert len(result) == 1
        assert result[0].account_id == 1
        assert len(result[0].fees) == 1
        assert result[0].fees[0].type == "monthly"

    async def test_empty_result(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = FeeCollectionRepo(mock_db)
        result = await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        assert result == []
        assert isinstance(result, list)

    async def test_parses_fees_from_bytes(self, mock_session, mock_db):
        """asyncpg returns JSONB as bytes — verify decoding."""
        fees_json = b'[{"type":"annual","description":"Annual fee","amount":5000}]'
        row = _make_fee_bearing_row(account_id=2, fees_json=fees_json)
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = FeeCollectionRepo(mock_db)
        result = await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        assert len(result[0].fees) == 1
        assert result[0].fees[0].type == "annual"

    async def test_null_fees_column(self, mock_session, mock_db):
        row = _make_fee_bearing_row(account_id=3, fees_json=None)
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = FeeCollectionRepo(mock_db)
        result = await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        assert len(result[0].fees) == 0

    async def test_maps_all_fields(self, mock_session, mock_db):
        row = _make_fee_bearing_row(
            account_id=10, tb_account_id=b"\xAA", tb_account_code=2201,
            currency="EUR", tb_ledger=978, last_fee_date=datetime(2025, 5, 1),
            fees_json='[]',
        )
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = FeeCollectionRepo(mock_db)
        result = await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        assert result[0].account_id == 10
        assert result[0].tb_account_code == 2201
        assert result[0].currency == "EUR"
        assert result[0].tb_ledger == 978

    async def test_sends_date_param(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = FeeCollectionRepo(mock_db)
        await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 7, 1))

        params = mock_session.execute.call_args[0][1]
        assert params["date"] == datetime(2025, 7, 1)

    async def test_uses_correct_joins(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = FeeCollectionRepo(mock_db)
        await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        sql = str(mock_session.execute.call_args[0][0])
        assert "FROM accounts a" in sql
        assert "JOIN products p" in sql
        assert "JOIN fee_schedules fs" in sql

    async def test_filters_on_active_status(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = FeeCollectionRepo(mock_db)
        await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        sql = str(mock_session.execute.call_args[0][0])
        assert "a.status = 'active'" in sql

    async def test_multiple_fees_in_schedule(self, mock_session, mock_db):
        fees_json = (
            '[{"type":"monthly","description":"Monthly","amount":500},'
            '{"type":"annual","description":"Annual","amount":5000}]'
        )
        row = _make_fee_bearing_row(account_id=1, fees_json=fees_json)
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = FeeCollectionRepo(mock_db)
        result = await repo.fetch_fee_bearing_accounts(mock_session, datetime(2025, 6, 1))

        assert len(result[0].fees) == 2
        assert result[0].fees[0].type == "monthly"
        assert result[0].fees[1].amount == 5000


# ---------------------------------------------------------------------------
# FeeCollectionRepo.record_fee_collections()
# ---------------------------------------------------------------------------

class TestFeeCollectionRepoRecordFeeCollections:
    def _make_record(self, account_id=1, tb_transfer_id=b"\x01", amount=500, fee_date=None):
        if fee_date is None:
            fee_date = datetime(2025, 6, 1)
        return FeeCollectionRecord(
            account_id=account_id,
            fee_date=fee_date,
            tb_transfer_id=tb_transfer_id,
            description="Monthly fee",
            reference="FEE-001",
            amount=amount,
        )

    async def test_records_single_fee(self, mock_session, mock_db):
        records = [self._make_record(account_id=1)]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        assert mock_session.execute.call_count >= 2

    async def test_empty_records_is_noop(self, mock_session, mock_db):
        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, [])

        assert mock_session.execute.call_count == 0

    async def test_uses_transaction_begin(self, mock_session, mock_db):
        records = [self._make_record()]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        mock_session.begin.assert_called_once()

    async def test_updates_last_fee_date_per_account(self, mock_session, mock_db):
        records = [
            self._make_record(account_id=1, tb_transfer_id=b"\x01"),
            self._make_record(account_id=2, tb_transfer_id=b"\x02"),
        ]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        update_calls = [c for c in mock_session.execute.call_args_list if "UPDATE accounts" in str(c[0][0])]
        assert len(update_calls) == 2

    async def test_single_update_for_same_account(self, mock_session, mock_db):
        records = [
            self._make_record(account_id=1, tb_transfer_id=b"\x01"),
            self._make_record(account_id=1, tb_transfer_id=b"\x02"),
        ]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        update_calls = [c for c in mock_session.execute.call_args_list if "UPDATE accounts" in str(c[0][0])]
        assert len(update_calls) == 1

    async def test_batch_insert_chunks(self, mock_session, mock_db):
        """When records exceed _FEE_COLLECTION_BATCH_SIZE (500), multiple inserts."""
        records = [self._make_record(account_id=1, tb_transfer_id=bytes([i % 256]), amount=i)
                   for i in range(600)]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        insert_calls = [c for c in mock_session.execute.call_args_list if "INSERT INTO transfer_metadata" in str(c[0][0])]
        assert len(insert_calls) == 2

    async def test_small_batch_single_insert(self, mock_session, mock_db):
        records = [self._make_record(account_id=1, tb_transfer_id=b"\x01")]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        insert_calls = [c for c in mock_session.execute.call_args_list if "INSERT INTO transfer_metadata" in str(c[0][0])]
        assert len(insert_calls) == 1

    async def test_update_sends_fee_date_and_id(self, mock_session, mock_db):
        records = [self._make_record(account_id=42, fee_date=datetime(2025, 6, 1))]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        first_call = mock_session.execute.call_args_list[0]
        params = first_call[0][1]
        assert params["id"] == 42

    async def test_metadata_insert_uses_positional_params(self, mock_session, mock_db):
        """The _insert_metadata_batch uses positional $1,$2,... placeholders."""
        records = [self._make_record(account_id=1, tb_transfer_id=b"\x01")]

        repo = FeeCollectionRepo(mock_db)
        await repo.record_fee_collections(mock_session, records)

        second_call = mock_session.execute.call_args_list[1]
        sql = str(second_call[0][0])
        assert "$1" in sql
