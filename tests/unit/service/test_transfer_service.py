"""Unit tests for TransferService (business logic layer).

Tests verify request validation, account resolution, TB transfer execution,
dual-write orchestration (TB-first / PG-second), error propagation from
repository layers, and transfer lookup — all using mocked dependencies.

Mirrors the style of :mod:`tests.unit.service.test_account_service`.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from cbs.domain.accounts import AccountCode, Balance
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrAccountFrozen,
    ErrInvalidAccount,
    ErrNotFound,
    ErrServiceUnavailable,
    TransferError,
    ValidationError,
)
from cbs.domain.errors import ErrInsufficientBalance  # noqa: F401
from cbs.domain.transfers import (
    TransferRequest,
    TransferResponse,
)
from cbs.service.transfer_service import NewTransferService


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_DEBIT = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
SAMPLE_CREDIT = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
SAMPLE_TRANSFER = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8daa"
CASH_VAULT_BYTES = b"\x00" * 16
MOCK_TB_ID = b"\xff" * 16
LEDGER_USD = 840


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tb_transfer_repo():
    """Create a mock TigerBeetleTransferRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.create_transfers = AsyncMock()
    repo.lookup_transfer = AsyncMock()
    return repo


def _make_tb_account_repo():
    """Create a mock TigerBeetleAccountRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.lookup_accounts = AsyncMock()
    return repo


def _make_account_meta_repo():
    """Create a mock AccountMetaRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.get_by_tb_account_id = AsyncMock()
    return repo


def _make_system_account_repo():
    """Create a mock SystemAccountRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.get_by_code = AsyncMock()
    return repo


def _make_metadata_writer():
    """Create a mock metadata writer with AsyncMock methods."""
    writer = MagicMock()
    writer.create_transfer_metadata = AsyncMock()
    return writer


def _make_account_meta(status="active", id=1):
    """Build a mock AccountWithProduct-like object for test fixtures."""
    meta = MagicMock()
    meta.id = id
    meta.status = status
    return meta


def _make_service(
    tb_transfer_repo=None,
    tb_account_repo=None,
    account_meta_repo=None,
    system_account_repo=None,
    metadata_writer=None,
):
    """Create a TransferService with mocked dependencies."""
    return NewTransferService(
        tb_transfer_repo=tb_transfer_repo or _make_tb_transfer_repo(),
        tb_account_repo=tb_account_repo or _make_tb_account_repo(),
        account_meta_repo=account_meta_repo or _make_account_meta_repo(),
        system_account_repo=system_account_repo or _make_system_account_repo(),
        metadata_writer=metadata_writer or _make_metadata_writer(),
    )


def _mock_tb_account_dict(ledger=LEDGER_USD):
    """Build a TB account dict with the given ledger."""
    return {
        "ledger": ledger,
        "code": 2110,
        "debits_posted": 0,
        "credits_posted": 100000,
        "debits_pending": 0,
        "credits_pending": 0,
    }


def _patch_execute_uuids(mock_uuid):
    """Context manager that patches UUID utilities for execute() tests."""
    return patch.multiple(
        "cbs.service.transfer_service",
        generate_uuidv7=MagicMock(return_value=mock_uuid),
        uuid_to_uint128=MagicMock(return_value=MOCK_TB_ID),
        tb_id_to_uuid=MagicMock(return_value=mock_uuid),
        map_transfer_code=MagicMock(return_value=3),
        int_to_uint128=lambda v: (v).to_bytes(16, "little"),
    )


# ---------------------------------------------------------------------------
# TransferService.execute()
# ---------------------------------------------------------------------------

class TestTransferServiceExecute:
    """Tests for ``TransferService.execute()``."""

    async def test_success_explicit_transfer(self, mock_session):
        """Happy path: explicit transfer with debit/credit accounts."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # PG metadata: both accounts active
        debit_meta = _make_account_meta(id=1)
        credit_meta = _make_account_meta(id=2)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # TB lookup: both accounts exist on same ledger
        tb_account_repo.lookup_accounts.return_value = {
            MOCK_TB_ID: _mock_tb_account_dict(),
        }

        # TB create: success result
        tb_transfer_repo.create_transfers.return_value = [{"status": 0}]

        mock_uuid = MagicMock()
        mock_uuid.__str__ = MagicMock(return_value=SAMPLE_TRANSFER)

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="transfer",
            amount=1000,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_CREDIT,
        )

        with _patch_execute_uuids(mock_uuid):
            result = await svc.execute(mock_session, req)

        assert isinstance(result, TransferResponse)
        assert result.id == SAMPLE_TRANSFER
        assert result.transfer_type == "transfer"
        assert result.debit_account_id == SAMPLE_DEBIT
        assert result.credit_account_id == SAMPLE_CREDIT
        assert result.amount.amount == 1000
        assert result.amount.currency == "USD"
        assert result.status == "posted"

        # Both accounts validated via PG metadata
        assert account_meta_repo.get_by_tb_account_id.await_count == 2

        # TB accounts looked up
        tb_account_repo.lookup_accounts.assert_awaited()

        # TB transfer created
        tb_transfer_repo.create_transfers.assert_awaited_once()

        # PG metadata written (dual-write)
        metadata_writer.create_transfer_metadata.assert_awaited_once()

    async def test_success_deposit(self, mock_session):
        """Deposit: Cash Vault (debit) -> customer account (credit)."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # Cash Vault system account from PG
        system_account_repo.get_by_code.return_value = CASH_VAULT_BYTES

        # Customer account metadata from PG
        cust_meta = _make_account_meta(id=10)
        account_meta_repo.get_by_tb_account_id.return_value = cust_meta

        # TB lookup: both accounts exist on same ledger
        tb_account_repo.lookup_accounts.return_value = {
            MOCK_TB_ID: _mock_tb_account_dict(),
        }

        # TB create: success
        tb_transfer_repo.create_transfers.return_value = [{"status": 0}]

        mock_uuid = MagicMock()
        mock_uuid.__str__ = MagicMock(return_value=SAMPLE_TRANSFER)

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="deposit",
            amount=500,
            currency="USD",
            credit_account_id=SAMPLE_CREDIT,
        )

        with _patch_execute_uuids(mock_uuid):
            result = await svc.execute(mock_session, req)

        assert result.transfer_type == "deposit"
        assert result.credit_account_id == SAMPLE_CREDIT
        assert result.amount.amount == 500
        assert result.status == "posted"

        # Cash Vault resolved from system accounts
        system_account_repo.get_by_code.assert_awaited_once()

        # Customer account validated via PG
        account_meta_repo.get_by_tb_account_id.assert_awaited_once()

        # Metadata written with customer account id
        metadata_writer.create_transfer_metadata.assert_awaited_once()

    async def test_success_withdrawal(self, mock_session):
        """Withdrawal: customer account (debit) -> Cash Vault (credit)."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # Customer account metadata from PG
        cust_meta = _make_account_meta(id=20)
        account_meta_repo.get_by_tb_account_id.return_value = cust_meta

        # Cash Vault system account from PG
        system_account_repo.get_by_code.return_value = CASH_VAULT_BYTES

        # TB lookup: both accounts exist on same ledger
        tb_account_repo.lookup_accounts.return_value = {
            MOCK_TB_ID: _mock_tb_account_dict(),
        }

        # TB create: success
        tb_transfer_repo.create_transfers.return_value = [{"status": 0}]

        mock_uuid = MagicMock()
        mock_uuid.__str__ = MagicMock(return_value=SAMPLE_TRANSFER)

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="withdrawal",
            amount=500,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
        )

        with _patch_execute_uuids(mock_uuid):
            result = await svc.execute(mock_session, req)

        assert result.transfer_type == "withdrawal"
        assert result.debit_account_id == SAMPLE_DEBIT
        assert result.amount.amount == 500
        assert result.status == "posted"

        # Cash Vault resolved from system accounts
        system_account_repo.get_by_code.assert_awaited_once()

        # Customer account validated via PG
        account_meta_repo.get_by_tb_account_id.assert_awaited_once()

    async def test_validation_error_empty_accounts(self, mock_session):
        """Empty debit/credit for transfer raises ValidationError."""
        svc = _make_service()

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="debit_account_id is required"):
            await svc.execute(mock_session, req)

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Invalid UUID format raises ValidationError."""
        svc = _make_service()

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id="not-a-uuid",
            credit_account_id=SAMPLE_CREDIT,
        )

        with pytest.raises(ValidationError, match="debit_account_id must be a valid UUID"):
            await svc.execute(mock_session, req)

    async def test_validation_error_same_account(self, mock_session):
        """Same debit and credit account raises ValidationError."""
        svc = _make_service()

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_DEBIT,
        )

        with pytest.raises(ValidationError, match="cannot be the same"):
            await svc.execute(mock_session, req)

        # No repo calls should happen
        svc._account_meta_repo.get_by_tb_account_id.assert_not_awaited()

    async def test_account_not_found_in_pg(self, mock_session):
        """Account not found in PG raises ErrInvalidAccount."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # Debit account not found in PG -> None
        account_meta_repo.get_by_tb_account_id.return_value = None

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_CREDIT,
        )

        with _patch_execute_uuids(MagicMock()):
            with pytest.raises(Exception) as exc_info:
                await svc.execute(mock_session, req)
            assert exc_info.value is ErrInvalidAccount

        # TB create should NOT be called
        tb_transfer_repo.create_transfers.assert_not_awaited()

    async def test_account_closed_in_pg(self, mock_session):
        """Closed account in PG raises ErrAccountClosed."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # Debit account is closed
        closed_meta = _make_account_meta(status="closed")
        account_meta_repo.get_by_tb_account_id.return_value = closed_meta

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_CREDIT,
        )

        with _patch_execute_uuids(MagicMock()):
            with pytest.raises(Exception) as exc_info:
                await svc.execute(mock_session, req)
            assert exc_info.value is ErrAccountClosed

    async def test_account_frozen_in_pg(self, mock_session):
        """Frozen account in PG raises ErrAccountFrozen."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # Debit account is frozen (checked before closed)
        frozen_meta = _make_account_meta(status="frozen")
        account_meta_repo.get_by_tb_account_id.return_value = frozen_meta

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_CREDIT,
        )

        with _patch_execute_uuids(MagicMock()):
            with pytest.raises(Exception) as exc_info:
                await svc.execute(mock_session, req)
            assert exc_info.value is ErrAccountFrozen

    async def test_tb_create_fails_value_error(self, mock_session):
        """TB create raises ValueError -> mapped to domain error via map_tb_error."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        debit_meta = _make_account_meta(id=1)
        credit_meta = _make_account_meta(id=2)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        tb_account_repo.lookup_accounts.return_value = {
            MOCK_TB_ID: _mock_tb_account_dict(),
        }

        # TB create raises ValueError (e.g., insufficient balance)
        tb_transfer_repo.create_transfers.side_effect = ValueError(
            "TransferExceedsCredits"
        )

        mock_uuid = MagicMock()
        mock_uuid.__str__ = MagicMock(return_value=SAMPLE_TRANSFER)

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_CREDIT,
        )

        with _patch_execute_uuids(mock_uuid), \
             patch("cbs.service.transfer_service.map_tb_error", return_value=ErrInsufficientBalance):
            with pytest.raises(Exception) as exc_info:
                await svc.execute(mock_session, req)
            assert exc_info.value is ErrInsufficientBalance

        # Metadata should NOT be written on failure
        metadata_writer.create_transfer_metadata.assert_not_awaited()

    async def test_tb_account_lookup_fails(self, mock_session):
        """TB account lookup exception raises ErrServiceUnavailable."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        debit_meta = _make_account_meta(id=1)
        credit_meta = _make_account_meta(id=2)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # TB lookup raises exception
        tb_account_repo.lookup_accounts.side_effect = RuntimeError("connection lost")

        mock_uuid = MagicMock()
        mock_uuid.__str__ = MagicMock(return_value=SAMPLE_TRANSFER)

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_CREDIT,
        )

        with _patch_execute_uuids(mock_uuid):
            with pytest.raises(Exception) as exc_info:
                await svc.execute(mock_session, req)
            assert exc_info.value is ErrServiceUnavailable

    async def test_ledger_mismatch(self, mock_session):
        """Debit and credit on different ledgers raises ValidationError."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        debit_meta = _make_account_meta(id=1)
        credit_meta = _make_account_meta(id=2)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # Different TB IDs for debit vs credit
        debit_tb_id = b"\xaa" * 16
        credit_tb_id = b"\xbb" * 16
        transfer_tb_id = b"\xcc" * 16

        # First lookup_accounts call (in _resolve_explicit): same ledger — passes
        # Second lookup_accounts call (in execute): different ledgers — fails
        tb_account_repo.lookup_accounts.side_effect = [
            {debit_tb_id: _mock_tb_account_dict(ledger=840), credit_tb_id: _mock_tb_account_dict(ledger=840)},
            {debit_tb_id: _mock_tb_account_dict(ledger=840), credit_tb_id: _mock_tb_account_dict(ledger=978)},
        ]

        mock_uuid = MagicMock()
        mock_uuid.__str__ = MagicMock(return_value=SAMPLE_TRANSFER)

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        req = TransferRequest(
            transfer_type="transfer",
            amount=100,
            currency="USD",
            debit_account_id=SAMPLE_DEBIT,
            credit_account_id=SAMPLE_CREDIT,
        )

        # uuid_to_uint128 calls: 2 in _resolve_explicit, then transfer+debit+credit in execute
        with patch("cbs.service.transfer_service.generate_uuidv7", return_value=mock_uuid), \
             patch("cbs.service.transfer_service.uuid_to_uint128", side_effect=[debit_tb_id, credit_tb_id, transfer_tb_id, debit_tb_id, credit_tb_id]), \
             patch("cbs.service.transfer_service.map_transfer_code", return_value=3), \
             patch("cbs.service.transfer_service.int_to_uint128", lambda v: (v).to_bytes(16, "little")):
            with pytest.raises(ValidationError, match="same ledger"):
                await svc.execute(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_awaited()


# ---------------------------------------------------------------------------
# TransferService.get()
# ---------------------------------------------------------------------------

class TestTransferServiceGet:
    """Tests for ``TransferService.get()``."""

    async def test_success(self, mock_session):
        """Transfer found in TB: amount extracted, accounts converted to UUIDs."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # TB transfer record with all fields
        amount_bytes = (1000).to_bytes(16, "little")
        debit_id_bytes = b"\xde\xad" * 8
        credit_id_bytes = b"\xbe\xef" * 8
        user_data_64 = int(datetime(2025, 6, 15).timestamp() * 1_000_000_000)

        tb_transfer = {
            "amount": amount_bytes,
            "debit_account_id": debit_id_bytes,
            "credit_account_id": credit_id_bytes,
            "ledger": LEDGER_USD,
            "code": 3,  # TRANSFER
            "user_data_64": user_data_64,
        }
        tb_transfer_repo.lookup_transfer.return_value = tb_transfer

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        mock_debit_uuid = MagicMock()
        mock_debit_uuid.__str__ = MagicMock(return_value=SAMPLE_DEBIT)
        mock_credit_uuid = MagicMock()
        mock_credit_uuid.__str__ = MagicMock(return_value=SAMPLE_CREDIT)

        with patch("cbs.service.transfer_service.uuid_to_uint128", return_value=MOCK_TB_ID), \
             patch("cbs.service.transfer_service.uint128_to_uuid", side_effect=[mock_debit_uuid, mock_credit_uuid]), \
             patch("cbs.service.transfer_service.uint128_to_int", return_value=1000), \
             patch("cbs.service.transfer_service._currency_scale_from_ledger", return_value=2):
            result = await svc.get(mock_session, SAMPLE_TRANSFER)

        assert isinstance(result, TransferResponse)
        assert result.id == SAMPLE_TRANSFER
        assert result.transfer_type == "transfer"
        assert result.debit_account_id == SAMPLE_DEBIT
        assert result.credit_account_id == SAMPLE_CREDIT
        assert result.amount.amount == 1000
        assert result.amount.currency == "USD"
        assert result.value_date == "2025-06-15"
        assert result.status == "posted"

        tb_transfer_repo.lookup_transfer.assert_awaited_once_with(MOCK_TB_ID)

    async def test_not_found(self, mock_session):
        """Transfer not found in TB raises ErrNotFound."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        tb_transfer_repo.lookup_transfer.return_value = None

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        with patch("cbs.service.transfer_service.uuid_to_uint128", return_value=MOCK_TB_ID):
            with pytest.raises(Exception) as exc_info:
                await svc.get(mock_session, SAMPLE_TRANSFER)
            assert exc_info.value is ErrNotFound

    async def test_validation_error_empty_id(self, mock_session):
        """Empty transfer id raises ValidationError."""
        svc = _make_service()

        with pytest.raises(ValidationError, match="transfer id is required"):
            await svc.get(mock_session, "")

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Invalid UUID format raises ValidationError."""
        svc = _make_service()

        with pytest.raises(ValidationError, match="invalid transfer id format"):
            await svc.get(mock_session, "not-a-uuid")

    async def test_lookup_exception(self, mock_session):
        """TB lookup exception raises RuntimeError."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        tb_transfer_repo.lookup_transfer.side_effect = RuntimeError("TB down")

        svc = _make_service(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            metadata_writer,
        )

        with patch("cbs.service.transfer_service.uuid_to_uint128", return_value=MOCK_TB_ID):
            with pytest.raises(RuntimeError, match="lookup transfer"):
                await svc.get(mock_session, SAMPLE_TRANSFER)
