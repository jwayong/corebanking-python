"""Unit tests for HoldService (two-phase transfer operations).

Tests verify request validation, TB/PG orchestration, error propagation
from repository layers, and response construction — all using mocked
dependencies.

Mirrors the style of :mod:`tests.unit.service.test_account_service`.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import uuid as _uuid

from cbs.domain.accounts import Balance
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrAccountFrozen,
    ErrHoldAlreadyResolved,
    ErrInsufficientBalance,
    ErrInvalidAccount,
    ErrNotFound,
    ValidationError,
)
from cbs.domain.transfers import (
    CaptureRequest,
    HoldRequest,
    HOLD_STATUS_CAPTURED,
    HOLD_STATUS_PENDING,
    HOLD_STATUS_VOIDED,
)
from cbs.service.hold_service import NewHoldService


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


def _make_metadata_writer():
    """Create a mock MetadataWriter with AsyncMock methods."""
    writer = MagicMock()
    writer.create_transfer_metadata = AsyncMock()
    return writer


def _make_hold_service():
    """Create a HoldService with all mocked dependencies."""
    tb_transfer_repo = _make_tb_transfer_repo()
    tb_account_repo = _make_tb_account_repo()
    account_meta_repo = _make_account_meta_repo()
    metadata_writer = _make_metadata_writer()

    return (
        NewHoldService(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            metadata_writer,
        ),
        tb_transfer_repo,
        tb_account_repo,
        account_meta_repo,
        metadata_writer,
    )


def _make_account_meta(
    id=1,
    tb_account_id=None,
    status="active",
    product_code="SAVINGS",
):
    """Build a mock AccountWithProduct-like object for test fixtures."""
    meta = MagicMock()
    meta.id = id
    meta.tb_account_id = tb_account_id or b"\x01" * 16
    meta.status = status
    meta.product_code = product_code
    return meta


def _make_mock_uuid(hex_str="0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"):
    """Build a mock UUID object with predictable attributes."""
    mock_uuid = MagicMock()
    mock_uuid.hex = hex_str
    mock_uuid.bytes = bytes.fromhex(hex_str)
    mock_uuid.__str__ = MagicMock(return_value=hex_str[:8] + "-" + hex_str[8:12] + "-" + hex_str[12:16] + "-" + hex_str[16:20] + "-" + hex_str[20:])
    return mock_uuid


def _make_tb_hold(
    hold_id=b"\x10" * 16,
    debit_account_id=b"\x01" * 16,
    credit_account_id=b"\x02" * 16,
    amount=50000,
    ledger=840,
):
    """Build a TB hold transfer dict for lookup_transfer mock."""
    from cbs.util.tb_types import int_to_uint128

    return {
        "id": hold_id,
        "debit_account_id": debit_account_id,
        "credit_account_id": credit_account_id,
        "amount": int_to_uint128(amount),
        "ledger": ledger,
    }


def _make_tb_account(
    tb_id=None,
    ledger=840,
    code=2110,
    account_flags=0,
):
    """Build a TB account dict for lookup_accounts mock."""
    return {
        "account_id": tb_id or b"\x01" * 16,
        "ledger": ledger,
        "code": code,
        "account_flags": account_flags,
        "debits_posted": 0,
        "credits_posted": 100000,
    }


def _success_result(transfer_id):
    """Build a TB create result dict indicating success."""
    return {"id": transfer_id, "status": 0}


def _error_result(transfer_id, status):
    """Build a TB create result dict indicating failure."""
    return {"id": transfer_id, "status": status}


# ---------------------------------------------------------------------------
# HoldService.create() — pending hold (phase 1)
# ---------------------------------------------------------------------------

class TestHoldServiceCreate:
    """Tests for ``HoldService.create()``."""

    async def test_success_happy_path(self, mock_session, sample_uuid):
        """Happy path: validate -> resolve accounts -> TB create -> response with status=pending."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_str = sample_uuid
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        hold_tb_id = b"\x10" * 16

        # Mock _uuid_parse for debit and credit accounts
        def mock_uuid_parse(s):
            if s == debit_uuid_str:
                return debit_uuid_obj
            return credit_uuid_obj

        # Mock uuid_to_uint128: called for debit, credit, then hold
        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return hold_tb_id

        # TB batch lookup returns both accounts
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        # PG metadata lookup for both accounts — active status
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        credit_meta = _make_account_meta(id=2, tb_account_id=credit_tb_id)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # TB create succeeds
        tb_transfer_repo.create_transfers.return_value = [_success_result(hold_tb_id)]

        req = HoldRequest(
            debit_account_id=debit_uuid_str,
            credit_account_id=credit_uuid_str,
            amount=50000,
            currency="USD",
            timeout_seconds=86400,
            reference="test-hold-ref",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=hold_uuid_obj), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            result = await svc.create(mock_session, req)

        assert result.id == str(hold_uuid_obj)
        assert result.transfer_type == "hold"
        assert result.debit_account_id == debit_uuid_str
        assert result.credit_account_id == credit_uuid_str
        assert result.amount.amount == 50000
        assert result.amount.currency == "USD"
        assert result.status == HOLD_STATUS_PENDING
        assert result.reference == "test-hold-ref"
        assert result.expires_at is not None

        # Verify dual-write order: TB first, then PG
        tb_account_repo.lookup_accounts.assert_awaited_once()
        tb_transfer_repo.create_transfers.assert_awaited_once()

        # Verify PG metadata write was attempted
        metadata_writer.create_transfer_metadata.assert_awaited_once()

    async def test_validation_error_empty_debit_account(self, mock_session):
        """Empty debit_account_id raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id="",
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
            amount=50000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="debit_account_id is required"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_validation_error_invalid_debit_uuid(self, mock_session):
        """Invalid debit UUID format raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id="not-a-uuid",
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
            amount=50000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="debit_account_id must be a valid UUID"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_validation_error_empty_credit_account(self, mock_session):
        """Empty credit_account_id raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
            credit_account_id="",
            amount=50000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="credit_account_id is required"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_validation_error_invalid_credit_uuid(self, mock_session):
        """Invalid credit UUID format raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
            credit_account_id="not-a-uuid",
            amount=50000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="credit_account_id must be a valid UUID"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_validation_error_same_debit_credit(self, mock_session, sample_uuid):
        """Same debit and credit account raises ValidationError."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id=sample_uuid,
            amount=50000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="debit and credit accounts cannot be the same"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_validation_error_invalid_currency(self, mock_session, sample_uuid):
        """Invalid currency code raises ValidationError."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="XXX",
        )

        with pytest.raises(ValidationError, match="unsupported currency"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_validation_error_amount_zero(self, mock_session, sample_uuid):
        """Zero amount raises ValidationError."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=0,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="amount must be positive"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_validation_error_timeout_exceeds_max(self, mock_session, sample_uuid):
        """Timeout exceeding 7 days raises ValidationError."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="USD",
            timeout_seconds=604801,  # exceeds max of 604800 (7 days)
        )

        with pytest.raises(ValidationError, match="timeout_seconds must not exceed"):
            await svc.create(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()
        tb_account_repo.lookup_accounts.assert_not_called()

    async def test_account_not_found_in_tb(self, mock_session, sample_uuid):
        """Account not found in TB batch lookup raises ErrInvalidAccount."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        def mock_uuid_parse(s):
            if s == sample_uuid:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return b"\x10" * 16

        # TB batch lookup returns only debit account — credit missing
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
        }

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            with pytest.raises(Exception) as exc_info:
                await svc.create(mock_session, req)
            assert exc_info.value is ErrInvalidAccount

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_account_closed_in_tb(self, mock_session, sample_uuid):
        """Account with Closed flag in TB raises ErrAccountClosed."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        def mock_uuid_parse(s):
            if s == sample_uuid:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return b"\x10" * 16

        # Debit account has Closed flag (0x08)
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840, account_flags=0x08),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            with pytest.raises(Exception) as exc_info:
                await svc.create(mock_session, req)
            assert exc_info.value is ErrAccountClosed

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_pg_account_status_closed(self, mock_session, sample_uuid):
        """PG account status 'closed' raises ErrAccountClosed."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        def mock_uuid_parse(s):
            if s == sample_uuid:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return b"\x10" * 16

        # TB batch lookup returns both accounts
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        # PG metadata: debit is closed
        closed_meta = _make_account_meta(status="closed")
        active_meta = _make_account_meta(status="active")
        account_meta_repo.get_by_tb_account_id.side_effect = [closed_meta, active_meta]

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            with pytest.raises(Exception) as exc_info:
                await svc.create(mock_session, req)
            assert exc_info.value is ErrAccountClosed

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_pg_account_status_frozen(self, mock_session, sample_uuid):
        """PG account status 'frozen' raises ErrAccountFrozen."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        def mock_uuid_parse(s):
            if s == sample_uuid:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return b"\x10" * 16

        # TB batch lookup returns both accounts
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        # PG metadata: debit active, credit frozen
        active_meta = _make_account_meta(status="active")
        frozen_meta = _make_account_meta(status="frozen")
        account_meta_repo.get_by_tb_account_id.side_effect = [active_meta, frozen_meta]

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            with pytest.raises(Exception) as exc_info:
                await svc.create(mock_session, req)
            assert exc_info.value is ErrAccountFrozen

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_tb_create_fails_value_error(self, mock_session, sample_uuid):
        """TB create raises ValueError -> mapped via map_tb_error."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        hold_tb_id = b"\x10" * 16

        def mock_uuid_parse(s):
            if s == sample_uuid:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return hold_tb_id

        # TB batch lookup returns both accounts
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        # PG metadata: both active
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        credit_meta = _make_account_meta(id=2, tb_account_id=credit_tb_id)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # TB create raises ValueError (e.g., connection error)
        tb_transfer_repo.create_transfers.side_effect = ValueError("connection refused")

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=hold_uuid_obj), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            with pytest.raises(Exception) as exc_info:
                await svc.create(mock_session, req)
            # map_tb_error maps "connection refused" to ErrServiceUnavailable
            from cbs.domain.errors import ErrServiceUnavailable
            assert exc_info.value is ErrServiceUnavailable

        metadata_writer.create_transfer_metadata.assert_not_called()

    async def test_tb_create_result_error_insufficient_balance(self, mock_session, sample_uuid):
        """TB create returns error result -> ErrInsufficientBalance raised."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        hold_tb_id = b"\x10" * 16

        def mock_uuid_parse(s):
            if s == sample_uuid:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return hold_tb_id

        # TB batch lookup returns both accounts
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        # PG metadata: both active
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        credit_meta = _make_account_meta(id=2, tb_account_id=credit_tb_id)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # TB create returns error result — status 60 = TransferPendingTransferNotFound
        # which maps to ErrInsufficientBalance in map_hold_status
        tb_transfer_repo.create_transfers.return_value = [_error_result(hold_tb_id, 60)]

        req = HoldRequest(
            debit_account_id=sample_uuid,
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=hold_uuid_obj), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            with pytest.raises(Exception) as exc_info:
                await svc.create(mock_session, req)
            assert exc_info.value is ErrInsufficientBalance

        metadata_writer.create_transfer_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# HoldService.capture() — capture hold (phase 2a)
# ---------------------------------------------------------------------------

class TestHoldServiceCapture:
    """Tests for ``HoldService.capture()``."""

    async def test_success_full_capture(self, mock_session):
        """Full capture: lookup hold -> create TB transfer with PostPendingTransfer flag."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        debit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        capture_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d01")

        hold_tb_id = b"\x10" * 16
        capture_tb_id = b"\x20" * 16

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        # Mock hold lookup
        tb_hold = _make_tb_hold(
            hold_id=hold_tb_id,
            debit_account_id=debit_tb_id,
            credit_account_id=credit_tb_id,
            amount=50000,
            ledger=840,
        )
        tb_transfer_repo.lookup_transfer.return_value = tb_hold

        # TB create succeeds for capture transfer
        tb_transfer_repo.create_transfers.return_value = [_success_result(capture_tb_id)]

        # PG metadata lookup for debit account
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        account_meta_repo.get_by_tb_account_id.return_value = debit_meta

        req = CaptureRequest(amount=0)  # zero amount -> full capture

        def mock_uuid_parse(s):
            return hold_uuid_obj

        # uuid_to_uint128 called twice: once for hold lookup, once for capture ID
        def mock_uuid_to_uint128(u):
            if u is hold_uuid_obj:
                return hold_tb_id
            return capture_tb_id

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=capture_uuid_obj), \
             patch("cbs.service.hold_service.uint128_to_uuid", side_effect=[
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"),  # debit
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f"),  # credit
             ]), \
             patch("cbs.service.hold_service.ledger_to_currency", return_value="USD"), \
             patch("cbs.service.hold_service.currency_scale_from_ledger", return_value=2):
            result = await svc.capture(mock_session, hold_uuid_str, req)

        assert result.transfer_type == "capture"
        assert result.debit_account_id == debit_uuid_str
        assert result.credit_account_id == credit_uuid_str
        assert result.amount.amount == 50000  # full hold amount
        assert result.status == HOLD_STATUS_CAPTURED

        tb_transfer_repo.lookup_transfer.assert_awaited_once()
        tb_transfer_repo.create_transfers.assert_awaited_once()

        # Verify capture transfer has PostPendingTransfer flag
        call_args = tb_transfer_repo.create_transfers.call_args[0][0][0]
        assert call_args["flags"] == 0x04  # PostPendingTransfer
        assert call_args["pending_id"] == hold_tb_id

    async def test_success_partial_capture(self, mock_session):
        """Partial capture: amount < hold amount."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        debit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        capture_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d01")

        hold_tb_id = b"\x10" * 16
        capture_tb_id = b"\x20" * 16

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        # Mock hold lookup with amount 50000
        tb_hold = _make_tb_hold(
            hold_id=hold_tb_id,
            debit_account_id=debit_tb_id,
            credit_account_id=credit_tb_id,
            amount=50000,
            ledger=840,
        )
        tb_transfer_repo.lookup_transfer.return_value = tb_hold

        # TB create succeeds for capture transfer
        tb_transfer_repo.create_transfers.return_value = [_success_result(capture_tb_id)]

        # PG metadata lookup for debit account
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        account_meta_repo.get_by_tb_account_id.return_value = debit_meta

        req = CaptureRequest(amount=30000)  # partial capture < hold amount

        def mock_uuid_parse(s):
            return hold_uuid_obj

        # uuid_to_uint128 called twice: once for hold lookup, once for capture ID
        def mock_uuid_to_uint128(u):
            if u is hold_uuid_obj:
                return hold_tb_id
            return capture_tb_id

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=capture_uuid_obj), \
             patch("cbs.service.hold_service.uint128_to_uuid", side_effect=[
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"),  # debit
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f"),  # credit
             ]), \
             patch("cbs.service.hold_service.ledger_to_currency", return_value="USD"), \
             patch("cbs.service.hold_service.currency_scale_from_ledger", return_value=2):
            result = await svc.capture(mock_session, hold_uuid_str, req)

        assert result.amount.amount == 30000  # partial capture amount
        assert result.status == HOLD_STATUS_CAPTURED

        # Verify capture transfer has correct partial amount
        call_args = tb_transfer_repo.create_transfers.call_args[0][0][0]
        from cbs.util.tb_types import uint128_to_int
        assert uint128_to_int(call_args["amount"]) == 30000

    async def test_hold_not_found(self, mock_session):
        """ErrNotFound when hold not found (lookup returns None)."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")

        hold_tb_id = b"\x10" * 16

        # Lookup returns None
        tb_transfer_repo.lookup_transfer.return_value = None

        req = CaptureRequest(amount=0)

        def mock_uuid_parse(s):
            return hold_uuid_obj

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", return_value=hold_tb_id):
            with pytest.raises(Exception) as exc_info:
                await svc.capture(mock_session, hold_uuid_str, req)
            assert exc_info.value is ErrNotFound

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_validation_error_empty_hold_id(self, mock_session):
        """Empty hold_id raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = CaptureRequest(amount=0)

        with pytest.raises(ValidationError, match="invalid hold id format"):
            await svc.capture(mock_session, "", req)

        tb_transfer_repo.lookup_transfer.assert_not_called()
        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_validation_error_invalid_hold_id_uuid(self, mock_session):
        """Invalid UUID for hold_id raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = CaptureRequest(amount=0)

        with pytest.raises(ValidationError, match="invalid hold id format"):
            await svc.capture(mock_session, "not-a-uuid", req)

        tb_transfer_repo.lookup_transfer.assert_not_called()
        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_validation_error_capture_amount_exceeds_hold(self, mock_session):
        """Capture amount exceeding hold amount raises ValidationError."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")

        hold_tb_id = b"\x10" * 16
        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        # Mock hold lookup with amount 50000
        tb_hold = _make_tb_hold(
            hold_id=hold_tb_id,
            debit_account_id=debit_tb_id,
            credit_account_id=credit_tb_id,
            amount=50000,
            ledger=840,
        )
        tb_transfer_repo.lookup_transfer.return_value = tb_hold

        req = CaptureRequest(amount=60000)  # exceeds hold amount

        def mock_uuid_parse(s):
            return hold_uuid_obj

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", return_value=hold_tb_id):
            with pytest.raises(ValidationError, match="capture amount exceeds hold amount"):
                await svc.capture(mock_session, hold_uuid_str, req)

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_validation_error_capture_amount_negative(self, mock_session):
        """Negative capture amount raises ValidationError from req.validate()."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        req = CaptureRequest(amount=-100)

        with pytest.raises(ValidationError, match="amount must be zero or positive"):
            await svc.capture(mock_session, "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00", req)

        tb_transfer_repo.lookup_transfer.assert_not_called()


# ---------------------------------------------------------------------------
# HoldService.void() — void hold (phase 2b)
# ---------------------------------------------------------------------------

class TestHoldServiceVoid:
    """Tests for ``HoldService.void()``."""

    async def test_success_void(self, mock_session):
        """Void: lookup hold -> create TB transfer with VoidPendingTransfer flag, zero amount."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        debit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        void_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d02")

        hold_tb_id = b"\x10" * 16
        void_tb_id = b"\x30" * 16

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        # Mock hold lookup
        tb_hold = _make_tb_hold(
            hold_id=hold_tb_id,
            debit_account_id=debit_tb_id,
            credit_account_id=credit_tb_id,
            amount=50000,
            ledger=840,
        )
        tb_transfer_repo.lookup_transfer.return_value = tb_hold

        # TB create succeeds for void transfer
        tb_transfer_repo.create_transfers.return_value = [_success_result(void_tb_id)]

        # PG metadata lookup for debit account
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        account_meta_repo.get_by_tb_account_id.return_value = debit_meta

        def mock_uuid_parse(s):
            return hold_uuid_obj

        # uuid_to_uint128 called twice: once for hold lookup, once for void ID
        def mock_uuid_to_uint128(u):
            if u is hold_uuid_obj:
                return hold_tb_id
            return void_tb_id

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=void_uuid_obj), \
             patch("cbs.service.hold_service.uint128_to_uuid", side_effect=[
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"),  # debit
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f"),  # credit
             ]), \
             patch("cbs.service.hold_service.ledger_to_currency", return_value="USD"), \
             patch("cbs.service.hold_service.currency_scale_from_ledger", return_value=2):
            result = await svc.void(mock_session, hold_uuid_str)

        assert result.id == str(void_uuid_obj)
        assert result.transfer_type == "void"
        assert result.debit_account_id == debit_uuid_str
        assert result.credit_account_id == credit_uuid_str
        assert result.amount.amount == 50000  # original hold amount
        assert result.status == HOLD_STATUS_VOIDED

        tb_transfer_repo.lookup_transfer.assert_awaited_once()
        tb_transfer_repo.create_transfers.assert_awaited_once()

        # Verify void transfer has VoidPendingTransfer flag and zero amount
        call_args = tb_transfer_repo.create_transfers.call_args[0][0][0]
        assert call_args["flags"] == 0x08  # VoidPendingTransfer
        assert call_args["amount"] == b"\x00" * 16  # zero amount
        assert call_args["pending_id"] == hold_tb_id

    async def test_hold_not_found(self, mock_session):
        """ErrNotFound when hold not found."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")

        hold_tb_id = b"\x10" * 16

        # Lookup returns None
        tb_transfer_repo.lookup_transfer.return_value = None

        def mock_uuid_parse(s):
            return hold_uuid_obj

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", return_value=hold_tb_id):
            with pytest.raises(Exception) as exc_info:
                await svc.void(mock_session, hold_uuid_str)
            assert exc_info.value is ErrNotFound

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_validation_error_empty_hold_id(self, mock_session):
        """Empty hold_id raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        with pytest.raises(ValidationError, match="invalid hold id format"):
            await svc.void(mock_session, "")

        tb_transfer_repo.lookup_transfer.assert_not_called()
        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_validation_error_invalid_hold_id_uuid(self, mock_session):
        """Invalid UUID for hold_id raises ValidationError before touching repos."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        with pytest.raises(ValidationError, match="invalid hold id format"):
            await svc.void(mock_session, "not-a-uuid")

        tb_transfer_repo.lookup_transfer.assert_not_called()
        tb_transfer_repo.create_transfers.assert_not_called()


# ---------------------------------------------------------------------------
# HoldService — metadata write error tolerance (fire-and-forget)
# ---------------------------------------------------------------------------

class TestHoldServiceMetadataTolerance:
    """Tests for fire-and-forget metadata writes that should not fail the operation."""

    async def test_create_metadata_write_error_does_not_fail(self, mock_session, sample_uuid):
        """PG metadata write failure during create does not raise — hold still succeeds."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        debit_uuid_str = sample_uuid
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        hold_tb_id = b"\x10" * 16

        def mock_uuid_parse(s):
            if s == debit_uuid_str:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return hold_tb_id

        # TB batch lookup returns both accounts
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        # PG metadata lookup for both accounts — active status
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        credit_meta = _make_account_meta(id=2, tb_account_id=credit_tb_id)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # TB create succeeds
        tb_transfer_repo.create_transfers.return_value = [_success_result(hold_tb_id)]

        # PG metadata write fails — should NOT propagate
        metadata_writer.create_transfer_metadata.side_effect = RuntimeError("db connection lost")

        req = HoldRequest(
            debit_account_id=debit_uuid_str,
            credit_account_id=credit_uuid_str,
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=hold_uuid_obj), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            result = await svc.create(mock_session, req)

        # Hold still succeeds despite metadata write failure
        assert result.status == HOLD_STATUS_PENDING

    async def test_capture_metadata_write_error_does_not_fail(self, mock_session):
        """PG metadata write failure during capture does not raise — capture still succeeds."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        debit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        capture_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d01")

        hold_tb_id = b"\x10" * 16
        capture_tb_id = b"\x20" * 16

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        # Mock hold lookup
        tb_hold = _make_tb_hold(
            hold_id=hold_tb_id,
            debit_account_id=debit_tb_id,
            credit_account_id=credit_tb_id,
            amount=50000,
            ledger=840,
        )
        tb_transfer_repo.lookup_transfer.return_value = tb_hold

        # TB create succeeds for capture transfer
        tb_transfer_repo.create_transfers.return_value = [_success_result(capture_tb_id)]

        # PG metadata lookup for debit account
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        account_meta_repo.get_by_tb_account_id.return_value = debit_meta

        # PG metadata write fails — should NOT propagate
        metadata_writer.create_transfer_metadata.side_effect = RuntimeError("db connection lost")

        req = CaptureRequest(amount=0)

        def mock_uuid_parse(s):
            return hold_uuid_obj

        # uuid_to_uint128 called twice: once for hold lookup, once for capture ID
        def mock_uuid_to_uint128(u):
            if u is hold_uuid_obj:
                return hold_tb_id
            return capture_tb_id

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=capture_uuid_obj), \
             patch("cbs.service.hold_service.uint128_to_uuid", side_effect=[
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"),  # debit
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f"),  # credit
             ]), \
             patch("cbs.service.hold_service.ledger_to_currency", return_value="USD"), \
             patch("cbs.service.hold_service.currency_scale_from_ledger", return_value=2):
            result = await svc.capture(mock_session, hold_uuid_str, req)

        # Capture still succeeds despite metadata write failure
        assert result.status == HOLD_STATUS_CAPTURED

    async def test_void_metadata_write_error_does_not_fail(self, mock_session):
        """PG metadata write failure during void does not raise — void still succeeds."""
        (svc, tb_transfer_repo, tb_account_repo,
         account_meta_repo, metadata_writer) = _make_hold_service()

        hold_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        debit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        void_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d02")

        hold_tb_id = b"\x10" * 16
        void_tb_id = b"\x30" * 16

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        # Mock hold lookup
        tb_hold = _make_tb_hold(
            hold_id=hold_tb_id,
            debit_account_id=debit_tb_id,
            credit_account_id=credit_tb_id,
            amount=50000,
            ledger=840,
        )
        tb_transfer_repo.lookup_transfer.return_value = tb_hold

        # TB create succeeds for void transfer
        tb_transfer_repo.create_transfers.return_value = [_success_result(void_tb_id)]

        # PG metadata lookup for debit account
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        account_meta_repo.get_by_tb_account_id.return_value = debit_meta

        # PG metadata write fails — should NOT propagate
        metadata_writer.create_transfer_metadata.side_effect = RuntimeError("db connection lost")

        def mock_uuid_parse(s):
            return hold_uuid_obj

        # uuid_to_uint128 called twice: once for hold lookup, once for void ID
        def mock_uuid_to_uint128(u):
            if u is hold_uuid_obj:
                return hold_tb_id
            return void_tb_id

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=void_uuid_obj), \
             patch("cbs.service.hold_service.uint128_to_uuid", side_effect=[
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"),  # debit
                 _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f"),  # credit
             ]), \
             patch("cbs.service.hold_service.ledger_to_currency", return_value="USD"), \
             patch("cbs.service.hold_service.currency_scale_from_ledger", return_value=2):
            result = await svc.void(mock_session, hold_uuid_str)

        # Void still succeeds despite metadata write failure
        assert result.status == HOLD_STATUS_VOIDED


# ---------------------------------------------------------------------------
# HoldService — nil metadata_writer tolerance
# ---------------------------------------------------------------------------

class TestHoldServiceNilMetadataWriter:
    """Tests that operations succeed when metadata_writer is None."""

    async def test_create_with_nil_metadata_writer(self, mock_session, sample_uuid):
        """create() succeeds when metadata_writer is None."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()

        svc = NewHoldService(
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            metadata_writer=None,  # nil writer
        )

        debit_uuid_str = sample_uuid
        credit_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

        debit_uuid_obj = _make_mock_uuid()
        credit_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d9f")

        debit_tb_id = b"\x01" * 16
        credit_tb_id = b"\x02" * 16

        hold_uuid_obj = _make_mock_uuid("0194e7c38f4a7b2d9c1e4f5a6b7c8d00")
        hold_tb_id = b"\x10" * 16

        def mock_uuid_parse(s):
            if s == debit_uuid_str:
                return debit_uuid_obj
            return credit_uuid_obj

        def mock_uuid_to_uint128(u):
            if u is debit_uuid_obj:
                return debit_tb_id
            if u is credit_uuid_obj:
                return credit_tb_id
            return hold_tb_id

        # TB batch lookup returns both accounts
        tb_account_repo.lookup_accounts.return_value = {
            debit_tb_id: _make_tb_account(tb_id=debit_tb_id, ledger=840),
            credit_tb_id: _make_tb_account(tb_id=credit_tb_id, ledger=840),
        }

        # PG metadata lookup for both accounts — active status
        debit_meta = _make_account_meta(id=1, tb_account_id=debit_tb_id)
        credit_meta = _make_account_meta(id=2, tb_account_id=credit_tb_id)
        account_meta_repo.get_by_tb_account_id.side_effect = [debit_meta, credit_meta]

        # TB create succeeds
        tb_transfer_repo.create_transfers.return_value = [_success_result(hold_tb_id)]

        req = HoldRequest(
            debit_account_id=debit_uuid_str,
            credit_account_id=credit_uuid_str,
            amount=50000,
            currency="USD",
        )

        with patch("cbs.service.hold_service._uuid_parse", side_effect=mock_uuid_parse), \
             patch("cbs.service.hold_service.uuid_to_uint128", side_effect=mock_uuid_to_uint128), \
             patch("cbs.service.hold_service.generate_uuidv7", return_value=hold_uuid_obj), \
             patch("cbs.service.hold_service.lookup_currency") as mock_lookup:
            mock_lookup.return_value = MagicMock(ledger=840, scale=2)

            result = await svc.create(mock_session, req)

        assert result.status == HOLD_STATUS_PENDING
        # metadata_writer is None — create_transfer_metadata should never be called
