"""Unit tests for AuditRepo (PostgreSQL audit log and transfer metadata).

Tests verify INSERT/SELECT SQL, batch fetch with chunking, UUID key
formatting, and edge cases using mocked async sessions.
"""

from __future__ import annotations

import pytest
import uuid as _uuid
from datetime import date, datetime
from unittest.mock import MagicMock

from sqlalchemy.exc import NoResultFound

from cbs.store.postgres.audit_repo import (
    AuditRepo,
    TransferMetadataRecord,
)
from tests.unit.store.postgres.fixtures import make_mock_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transfer_id(hex_str="0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"):
    """Create a 16-byte TigerBeetle transfer ID from a hex string."""
    return bytes.fromhex(hex_str)


def _make_metadata_row(
    tb_transfer_id=None, tb_correlation=b"\x01", account_id=42,
    counterparty="external", description="Payment", reference="REF-001",
    value_date=None,
):
    if tb_transfer_id is None:
        tb_transfer_id = _make_transfer_id()
    if value_date is None:
        value_date = date(2025, 6, 1)
    return (tb_transfer_id, tb_correlation, account_id, counterparty, description, reference, value_date)


# ---------------------------------------------------------------------------
# AuditRepo.create_transfer_metadata()
# ---------------------------------------------------------------------------

class TestAuditRepoCreateTransferMetadata:
    async def test_success(self, mock_session, mock_db):
        repo = AuditRepo(mock_db)
        rec = TransferMetadataRecord(
            tb_transfer_id=b"\xAA\xBB",
            tb_correlation=b"\xCC",
            account_id=42,
            counterparty="external",
            description="Payment",
            reference="REF-001",
            value_date=date(2025, 6, 1),
        )
        await repo.create_transfer_metadata(mock_session, rec)

        mock_session.execute.assert_called_once()

    async def test_sends_all_fields(self, mock_session, mock_db):
        repo = AuditRepo(mock_db)
        rec = TransferMetadataRecord(
            tb_transfer_id=b"\x01\x02",
            tb_correlation=None,  # nullable field
            account_id=99,
            counterparty="internal",
            description="Fee collection",
            reference="FEE-123",
            value_date=date(2025, 7, 1),
        )
        await repo.create_transfer_metadata(mock_session, rec)

        params = mock_session.execute.call_args[0][1]
        assert params["tb_transfer_id"] == b"\x01\x02"
        assert params["tb_correlation"] is None
        assert params["account_id"] == 99

    async def test_uses_insert_sql(self, mock_session, mock_db):
        repo = AuditRepo(mock_db)
        rec = TransferMetadataRecord(tb_transfer_id=b"\x01")
        await repo.create_transfer_metadata(mock_session, rec)

        sql = str(mock_session.execute.call_args[0][0])
        assert "INSERT INTO transfer_metadata" in sql


# ---------------------------------------------------------------------------
# AuditRepo.get_by_tb_transfer_id()
# ---------------------------------------------------------------------------

class TestAuditRepoGetByTbTransferId:
    async def test_found(self, mock_session, mock_db):
        row = _make_metadata_row(
            tb_transfer_id=b"\xAA\xBB", account_id=42, counterparty="external",
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_id(mock_session, b"\xAA\xBB")

        assert result is not None
        assert result.tb_transfer_id == b"\xAA\xBB"
        assert result.account_id == 42
        assert result.counterparty == "external"

    async def test_not_found_returns_none(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_id(mock_session, b"\xFF")

        assert result is None

    async def test_noresultfound_returns_none(self, mock_session, mock_db):
        mock_session.execute.side_effect = NoResultFound()

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_id(mock_session, b"\xFF")

        assert result is None

    async def test_sends_tb_transfer_id_param(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AuditRepo(mock_db)
        test_id = b"\x01\x02\x03"
        await repo.get_by_tb_transfer_id(mock_session, test_id)

        params = mock_session.execute.call_args[0][1]
        assert params["tb_transfer_id"] == test_id

    async def test_uses_correct_select(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AuditRepo(mock_db)
        await repo.get_by_tb_transfer_id(mock_session, b"\x01")

        sql = str(mock_session.execute.call_args[0][0])
        assert "FROM transfer_metadata" in sql

    async def test_maps_all_fields(self, mock_session, mock_db):
        row = _make_metadata_row(
            tb_transfer_id=b"\xAA", tb_correlation=b"\xBB", account_id=7,
            counterparty="bank_x", description="Wire transfer",
            reference="WIRE-042", value_date=date(2025, 3, 15),
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_id(mock_session, b"\xAA")

        assert result.tb_correlation == b"\xBB"
        assert result.description == "Wire transfer"
        assert result.reference == "WIRE-042"
        assert result.value_date == date(2025, 3, 15)


# ---------------------------------------------------------------------------
# AuditRepo.get_by_tb_transfer_ids()
# ---------------------------------------------------------------------------

class TestAuditRepoGetByTbTransferIds:
    def _make_valid_transfer_id(self, hex_str):
        return bytes.fromhex(hex_str)

    async def test_empty_input_returns_empty_dict(self, mock_session, mock_db):
        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_ids(mock_session, [])

        assert result == {}
        mock_session.execute.assert_not_called()

    async def test_single_id_found(self, mock_session, mock_db):
        tb_id = self._make_valid_transfer_id("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e")
        row = _make_metadata_row(tb_transfer_id=tb_id, account_id=42)
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_ids(mock_session, [tb_id])

        expected_key = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        assert expected_key in result
        assert result[expected_key].account_id == 42

    async def test_multiple_ids_single_batch(self, mock_session, mock_db):
        id1 = self._make_valid_transfer_id("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e")
        id2 = self._make_valid_transfer_id("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")

        rows = [
            _make_metadata_row(tb_transfer_id=id1, account_id=1),
            _make_metadata_row(tb_transfer_id=id2, account_id=2),
        ]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_ids(mock_session, [id1, id2])

        assert len(result) == 2
        key1 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        key2 = "a1b2c3d4-e5f6-a7b8-c9d0-e1f2a3b4c5d6"
        assert key1 in result
        assert key2 in result

    async def test_chunking_for_large_batch(self, mock_session, mock_db):
        """IDs exceeding _MAX_BATCH_SIZE (100) are split into chunks."""
        ids = [self._make_valid_transfer_id(f"{i:032x}") for i in range(150)]

        # Create rows with unique tb_transfer_ids matching the input ids list
        batch1_rows = [_make_metadata_row(tb_transfer_id=ids[i], account_id=i) for i in range(100)]
        batch2_rows = [_make_metadata_row(tb_transfer_id=ids[i], account_id=i) for i in range(100, 150)]

        mock_session.execute.side_effect = [
            make_mock_result(fetchall_val=batch1_rows),
            make_mock_result(fetchall_val=batch2_rows),
        ]

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_ids(mock_session, ids)

        assert len(result) == 150
        assert mock_session.execute.call_count == 2

    async def test_noresultfound_in_batch_continues(self, mock_session, mock_db):
        """If one batch raises NoResultFound, the next batch still runs."""
        ids = [self._make_valid_transfer_id(f"{i:032x}") for i in range(150)]

        # Create rows with unique tb_transfer_ids from the second batch
        batch2_rows = [_make_metadata_row(tb_transfer_id=ids[i], account_id=i) for i in range(100, 150)]
        mock_session.execute.side_effect = [
            NoResultFound(),
            make_mock_result(fetchall_val=batch2_rows),
        ]

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_ids(mock_session, ids)

        assert len(result) == 50

    async def test_sends_ids_as_list(self, mock_session, mock_db):
        id1 = self._make_valid_transfer_id("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e")
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AuditRepo(mock_db)
        await repo.get_by_tb_transfer_ids(mock_session, [id1])

        params = mock_session.execute.call_args[0][1]
        assert "ids" in params
        assert id1 in params["ids"]

    async def test_uses_any_operator(self, mock_session, mock_db):
        id1 = self._make_valid_transfer_id("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e")
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AuditRepo(mock_db)
        await repo.get_by_tb_transfer_ids(mock_session, [id1])

        sql = str(mock_session.execute.call_args[0][0])
        assert "= ANY" in sql

    async def test_uuid_key_formatting(self, mock_session, mock_db):
        """Verify UUID hex is formatted with hyphens: 8-4-4-4-12."""
        tb_id = self._make_valid_transfer_id("550e8400e29b41d4a716446655440000")
        row = _make_metadata_row(tb_transfer_id=tb_id, account_id=1)
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_ids(mock_session, [tb_id])

        expected_key = "550e8400-e29b-41d4-a716-446655440000"
        assert expected_key in result

    async def test_partial_match_not_all_ids_found(self, mock_session, mock_db):
        """Only IDs that have metadata rows appear in the result dict."""
        id1 = self._make_valid_transfer_id("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e")
        id2 = self._make_valid_transfer_id("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")

        rows = [_make_metadata_row(tb_transfer_id=id1, account_id=42)]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = AuditRepo(mock_db)
        result = await repo.get_by_tb_transfer_ids(mock_session, [id1, id2])

        assert len(result) == 1
        key1 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        assert key1 in result
