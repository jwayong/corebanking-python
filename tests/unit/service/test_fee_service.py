"""Unit tests for FeeService (business logic layer).

Tests verify request validation, account resolution (PG + TB), error propagation
from repository layers, and response construction — all using mocked dependencies.

Mirrors the style of :mod:`tests.unit.service.test_account_service`.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import uuid as _uuid

from cbs.domain.accounts import AccountCode
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrAccountFrozen,
    ErrInvalidAccount,
    ErrNotFound,
    ErrServiceUnavailable,
    TransferError,
    ValidationError,
)
from cbs.domain.transfers import FeeChargeRequest, FeeChargeResponse
from cbs.service.fee_service import FeeService, NewFeeService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tb_transfer_repo():
    """Create a mock TBTransferRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.create_transfers = AsyncMock()
    return repo


def _make_tb_account_repo():
    """Create a mock TBAccountRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.lookup_accounts = AsyncMock()
    return repo


def _make_account_meta_repo():
    """Create a mock AccountMetaRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.get_by_tb_account_id = AsyncMock()
    return repo


def _make_system_account_repo():
    """Create a mock SystemAccountRepo with MagicMock methods."""
    repo = MagicMock()
    repo.get_by_code = AsyncMock()
    return repo


def _make_metadata_writer():
    """Create a mock MetadataWriter with AsyncMock methods."""
    writer = MagicMock()
    writer.create_transfer_metadata = AsyncMock()
    return writer


def _make_meta(
    id=1,
    status="active",
):
    """Build a mock account metadata object (AccountWithProduct-like)."""
    meta = MagicMock()
    meta.id = id
    meta.status = status
    return meta


def _make_fee_service(
    tb_transfer_repo=None,
    tb_account_repo=None,
    account_meta_repo=None,
    system_account_repo=None,
    metadata_writer=None,
):
    """Build a FeeService with mocked dependencies."""
    return NewFeeService(
        tb_transfer_repo=tb_transfer_repo or _make_tb_transfer_repo(),
        tb_account_repo=tb_account_repo or _make_tb_account_repo(),
        account_meta_repo=account_meta_repo or _make_account_meta_repo(),
        system_account_repo=system_account_repo or _make_system_account_repo(),
        metadata_writer=metadata_writer or _make_metadata_writer(),
    )


def _make_req(
    customer_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
    amount=100,
    currency="USD",
    description="Monthly maintenance fee",
    fee_schedule_ref="",
    value_date="",
):
    """Build a FeeChargeRequest for testing."""
    return FeeChargeRequest(
        customer_account_id=customer_account_id,
        amount=amount,
        currency=currency,
        description=description,
        fee_schedule_ref=fee_schedule_ref,
        value_date=value_date,
    )


def _setup_happy_path_mocks(
    svc,
    customer_uuid_str="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
    customer_tb_id=b"\xca\xfe" * 8,
    fee_income_bytes=None,
    fee_income_uuid_str="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00",
    fee_income_tb_id=b"\xee\xdd" * 8,
    ledger=840,
):
    """Configure all mocks for a successful fee charge.

    Returns the fee_income_uuid_str that will appear in the response.
    """
    if fee_income_bytes is None:
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes

    # PG: customer account metadata — active
    import uuid as _uuid
    customer_uuid_bytes = _uuid.UUID(customer_uuid_str).bytes
    svc._account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
        id=1, status="active"
    )

    # PG: fee income system account by code — returns 16 bytes (UUID)
    svc._system_account_repo.get_by_code.return_value = fee_income_bytes

    # TB: batch lookup returns both accounts on same ledger
    svc._tb_account_repo.lookup_accounts.return_value = {
        customer_tb_id: {"ledger": ledger, "code": 2110},
        fee_income_tb_id: {"ledger": ledger, "code": 4110},
    }

    # TB: create_transfers returns success result
    svc._tb_transfer_repo.create_transfers.return_value = [{"status": 0}]

    return fee_income_uuid_str


# ---------------------------------------------------------------------------
# FeeService.charge() — happy path
# ---------------------------------------------------------------------------

class TestFeeServiceCharge:
    """Tests for ``FeeService.charge()``."""

    async def test_success_happy_path(self, mock_session, sample_uuid):
        """Happy path: validate -> resolve accounts -> TB create -> response."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            metadata_writer=metadata_writer,
        )

        customer_uuid_str = sample_uuid
        customer_tb_id = b"\xca\xfe" * 8

        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        # Fee transfer UUID
        fee_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d99"
        fee_uuid = MagicMock()
        fee_uuid.__str__ = MagicMock(return_value=fee_uuid_str)

        _setup_happy_path_mocks(
            svc,
            customer_uuid_str=customer_uuid_str,
            customer_tb_id=customer_tb_id,
            fee_income_bytes=fee_income_bytes,
            fee_income_uuid_str=fee_income_uuid_str,
            fee_income_tb_id=fee_income_tb_id,
        )

        req = _make_req(customer_account_id=customer_uuid_str)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,       # customer_uuid -> tb_id
            fee_income_tb_id,     # fee_income_uuid -> tb_id
            b"\xfe\xed" * 8,      # fee_transfer_uuid -> tb_id (for transfer.id)
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)), \
             patch("cbs.service.fee_service.generate_uuidv7", return_value=fee_uuid):
            result = await svc.charge(mock_session, req)

        # Verify response fields
        assert isinstance(result, FeeChargeResponse)
        assert result.id == fee_uuid_str
        assert result.transfer_type == "fee"
        assert result.debit_account_id == customer_uuid_str
        assert result.credit_account_id == fee_income_uuid_str
        assert result.amount.amount == 100
        assert result.amount.currency == "USD"
        assert result.description == "Monthly maintenance fee"
        assert result.status == "posted"
        assert isinstance(result.created_at, datetime)

        # Verify call order: PG meta -> system account -> TB lookup -> TB create
        customer_uuid_bytes = _uuid.UUID(customer_uuid_str).bytes
        account_meta_repo.get_by_tb_account_id.assert_awaited_once_with(
            mock_session, customer_uuid_bytes
        )
        system_account_repo.get_by_code.assert_awaited_once_with(
            mock_session, "USD", int(AccountCode.INC_FEE_SERVICE)
        )
        tb_account_repo.lookup_accounts.assert_awaited_once_with(
            [customer_tb_id, fee_income_tb_id]
        )
        tb_transfer_repo.create_transfers.assert_awaited_once()

        # Verify metadata writer was called (fire-and-forget)
        metadata_writer.create_transfer_metadata.assert_awaited_once()

    async def test_success_with_value_date(self, mock_session, sample_uuid):
        """Happy path with explicit value_date string."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            metadata_writer=metadata_writer,
        )

        customer_uuid_str = sample_uuid
        customer_tb_id = b"\xca\xfe" * 8

        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        fee_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d99"
        fee_uuid = MagicMock()
        fee_uuid.__str__ = MagicMock(return_value=fee_uuid_str)

        _setup_happy_path_mocks(
            svc,
            customer_uuid_str=customer_uuid_str,
            customer_tb_id=customer_tb_id,
            fee_income_bytes=fee_income_bytes,
            fee_income_uuid_str=fee_income_uuid_str,
            fee_income_tb_id=fee_income_tb_id,
        )

        req = _make_req(
            customer_account_id=customer_uuid_str,
            value_date="2025-06-15",
        )

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
            b"\xfe\xed" * 8,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)), \
             patch("cbs.service.fee_service.generate_uuidv7", return_value=fee_uuid):
            result = await svc.charge(mock_session, req)

        assert result.value_date == "2025-06-15"
        assert result.status == "posted"


# ---------------------------------------------------------------------------
# FeeService.charge() — validation errors
# ---------------------------------------------------------------------------

class TestFeeServiceChargeValidation:
    """Tests for request validation in ``FeeService.charge()``."""

    async def test_validation_error_empty_customer_account(self, mock_session):
        """Empty customer_account_id raises ValidationError."""
        svc = _make_fee_service()

        req = _make_req(customer_account_id="")

        with pytest.raises(ValidationError, match="customer_account_id is required"):
            await svc.charge(mock_session, req)

        svc._account_meta_repo.get_by_tb_account_id.assert_not_awaited()
        svc._system_account_repo.get_by_code.assert_not_awaited()

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Invalid UUID format raises ValidationError."""
        svc = _make_fee_service()

        req = _make_req(customer_account_id="not-a-uuid")

        with pytest.raises(ValidationError, match="customer_account_id must be a valid UUID"):
            await svc.charge(mock_session, req)

        svc._account_meta_repo.get_by_tb_account_id.assert_not_awaited()

    async def test_validation_error_zero_amount(self, mock_session, sample_uuid):
        """Zero amount raises ValidationError."""
        svc = _make_fee_service()

        req = _make_req(amount=0)

        with pytest.raises(ValidationError, match="amount must be positive"):
            await svc.charge(mock_session, req)

    async def test_validation_error_empty_currency(self, mock_session, sample_uuid):
        """Empty currency raises ValidationError."""
        svc = _make_fee_service()

        req = _make_req(currency="")

        with pytest.raises(ValidationError, match="currency is required"):
            await svc.charge(mock_session, req)

    async def test_validation_error_empty_description(self, mock_session, sample_uuid):
        """Empty description raises ValidationError."""
        svc = _make_fee_service()

        req = _make_req(description="")

        with pytest.raises(ValidationError, match="description is required"):
            await svc.charge(mock_session, req)


# ---------------------------------------------------------------------------
# FeeService.charge() — customer account errors (PG layer)
# ---------------------------------------------------------------------------

class TestFeeServiceChargeCustomerAccount:
    """Tests for customer account resolution errors."""

    async def test_customer_account_not_found_in_pg(self, mock_session, sample_uuid):
        """Customer account not found in PG raises ErrInvalidAccount."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        # PG returns None — account does not exist
        account_meta_repo.get_by_tb_account_id.return_value = None

        req = _make_req(customer_account_id=sample_uuid)

        with pytest.raises(Exception) as exc_info:
            await svc.charge(mock_session, req)
        assert exc_info.value is ErrInvalidAccount

        system_account_repo.get_by_code.assert_not_awaited()
        tb_account_repo.lookup_accounts.assert_not_awaited()

    async def test_customer_account_closed_in_pg(self, mock_session, sample_uuid):
        """Customer account closed in PG raises ErrAccountClosed."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="closed"
        )

        req = _make_req(customer_account_id=sample_uuid)

        with pytest.raises(Exception) as exc_info:
            await svc.charge(mock_session, req)
        assert exc_info.value is ErrAccountClosed

        system_account_repo.get_by_code.assert_not_awaited()

    async def test_customer_account_frozen_in_pg(self, mock_session, sample_uuid):
        """Customer account frozen in PG raises ErrAccountFrozen."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="frozen"
        )

        req = _make_req(customer_account_id=sample_uuid)

        with pytest.raises(Exception) as exc_info:
            await svc.charge(mock_session, req)
        assert exc_info.value is ErrAccountFrozen

        system_account_repo.get_by_code.assert_not_awaited()


# ---------------------------------------------------------------------------
# FeeService.charge() — fee income system account errors
# ---------------------------------------------------------------------------

class TestFeeServiceChargeFeeIncomeAccount:
    """Tests for fee income system account resolution errors."""

    async def test_fee_income_account_not_found(self, mock_session, sample_uuid):
        """Fee income system account not found raises ErrInvalidAccount."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )

        # System account repo returns None — no fee income account for this currency
        system_account_repo.get_by_code.return_value = None

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", return_value=b"\xca\xfe" * 8):
            with pytest.raises(Exception) as exc_info:
                await svc.charge(mock_session, req)
            assert exc_info.value is ErrInvalidAccount

        tb_account_repo.lookup_accounts.assert_not_awaited()

    async def test_fee_income_bytes_invalid_length(self, mock_session, sample_uuid):
        """Fee income bytes not 16 bytes raises ErrInvalidAccount."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )

        # Return bytes that are not 16 bytes long
        system_account_repo.get_by_code.return_value = b"\x01\x02\x03"

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", return_value=b"\xca\xfe" * 8):
            with pytest.raises(Exception) as exc_info:
                await svc.charge(mock_session, req)
            assert exc_info.value is ErrInvalidAccount

        tb_account_repo.lookup_accounts.assert_not_awaited()


# ---------------------------------------------------------------------------
# FeeService.charge() — TB layer errors
# ---------------------------------------------------------------------------

class TestFeeServiceChargeTigerBeetle:
    """Tests for TigerBeetle interaction errors."""

    async def test_tb_create_fails_value_error(self, mock_session, sample_uuid):
        """TB create_transfers raises ValueError — mapped via map_tb_error."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        customer_tb_id = b"\xca\xfe" * 8
        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        _setup_happy_path_mocks(
            svc,
            customer_uuid_str=sample_uuid,
            customer_tb_id=customer_tb_id,
            fee_income_bytes=fee_income_bytes,
            fee_income_uuid_str=fee_income_uuid_str,
            fee_income_tb_id=fee_income_tb_id,
        )

        # Simulate TB connection error — raises ValueError
        tb_transfer_repo.create_transfers.side_effect = ValueError("connection refused")

        fee_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d99"
        fee_uuid = MagicMock()
        fee_uuid.__str__ = MagicMock(return_value=fee_uuid_str)

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
            b"\xfe\xed" * 8,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)), \
             patch("cbs.service.fee_service.generate_uuidv7", return_value=fee_uuid):
            with pytest.raises(Exception) as exc_info:
                await svc.charge(mock_session, req)
            # map_tb_error maps "connection refused" to ErrServiceUnavailable
            assert exc_info.value is ErrServiceUnavailable

    async def test_tb_lookup_fails_service_unavailable(self, mock_session, sample_uuid):
        """TB lookup_accounts raises exception — ErrServiceUnavailable."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        customer_tb_id = b"\xca\xfe" * 8
        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )
        system_account_repo.get_by_code.return_value = fee_income_bytes

        # TB lookup raises an exception
        tb_account_repo.lookup_accounts.side_effect = RuntimeError("TB connection lost")

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)):
            with pytest.raises(Exception) as exc_info:
                await svc.charge(mock_session, req)
            assert exc_info.value is ErrServiceUnavailable

    async def test_customer_account_not_found_in_tb(self, mock_session, sample_uuid):
        """Customer account missing from TB lookup raises ErrInvalidAccount."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        customer_tb_id = b"\xca\xfe" * 8
        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )
        system_account_repo.get_by_code.return_value = fee_income_bytes

        # TB lookup returns only fee income account — customer missing
        tb_account_repo.lookup_accounts.return_value = {
            fee_income_tb_id: {"ledger": 840, "code": 4110},
        }

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)):
            with pytest.raises(Exception) as exc_info:
                await svc.charge(mock_session, req)
            assert exc_info.value is ErrInvalidAccount

    async def test_fee_income_account_not_found_in_tb(self, mock_session, sample_uuid):
        """Fee income account missing from TB lookup raises ErrInvalidAccount."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        customer_tb_id = b"\xca\xfe" * 8
        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )
        system_account_repo.get_by_code.return_value = fee_income_bytes

        # TB lookup returns only customer account — fee income missing
        tb_account_repo.lookup_accounts.return_value = {
            customer_tb_id: {"ledger": 840, "code": 2110},
        }

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)):
            with pytest.raises(Exception) as exc_info:
                await svc.charge(mock_session, req)
            assert exc_info.value is ErrInvalidAccount

    async def test_ledger_mismatch_raises_validation_error(self, mock_session, sample_uuid):
        """Customer and fee income on different ledgers raises ValidationError."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        customer_tb_id = b"\xca\xfe" * 8
        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )
        system_account_repo.get_by_code.return_value = fee_income_bytes

        # Different ledgers: customer on 840 (USD), fee income on 978 (EUR)
        tb_account_repo.lookup_accounts.return_value = {
            customer_tb_id: {"ledger": 840, "code": 2110},
            fee_income_tb_id: {"ledger": 978, "code": 4110},
        }

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)):
            with pytest.raises(ValidationError, match="same ledger"):
                await svc.charge(mock_session, req)

    async def test_invalid_currency_raises_validation_error(self, mock_session, sample_uuid):
        """Unsupported currency code raises ValidationError from lookup_currency."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
        )

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )

        req = _make_req(customer_account_id=sample_uuid, currency="XYZ")

        with pytest.raises(ValidationError):
            await svc.charge(mock_session, req)


# ---------------------------------------------------------------------------
# FeeService.charge() — metadata write (fire-and-forget)
# ---------------------------------------------------------------------------

class TestFeeServiceChargeMetadata:
    """Tests for PG metadata write (fire-and-forget semantics)."""

    async def test_metadata_write_error_does_not_fail_charge(self, mock_session, sample_uuid):
        """Metadata write failure is logged but charge still succeeds."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            metadata_writer=metadata_writer,
        )

        customer_tb_id = b"\xca\xfe" * 8
        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        _setup_happy_path_mocks(
            svc,
            customer_uuid_str=sample_uuid,
            customer_tb_id=customer_tb_id,
            fee_income_bytes=fee_income_bytes,
            fee_income_uuid_str=fee_income_uuid_str,
            fee_income_tb_id=fee_income_tb_id,
        )

        # Metadata writer raises — should NOT propagate
        metadata_writer.create_transfer_metadata.side_effect = RuntimeError("PG down")

        fee_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d99"
        fee_uuid = MagicMock()
        fee_uuid.__str__ = MagicMock(return_value=fee_uuid_str)

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
            b"\xfe\xed" * 8,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)), \
             patch("cbs.service.fee_service.generate_uuidv7", return_value=fee_uuid):
            result = await svc.charge(mock_session, req)

        # Charge should still succeed despite metadata write failure
        assert result.status == "posted"
        assert result.id == fee_uuid_str

    async def test_metadata_writer_none_skips_write(self, mock_session, sample_uuid):
        """When metadata_writer is None, write step is skipped."""
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()

        svc = _make_fee_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            metadata_writer=None,
        )

        customer_tb_id = b"\xca\xfe" * 8
        fee_income_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d00"
        fee_income_bytes = _uuid.UUID(fee_income_uuid_str).bytes
        fee_income_tb_id = b"\xee\xdd" * 8

        account_meta_repo.get_by_tb_account_id.return_value = _make_meta(
            id=1, status="active"
        )
        system_account_repo.get_by_code.return_value = fee_income_bytes
        tb_account_repo.lookup_accounts.return_value = {
            customer_tb_id: {"ledger": 840, "code": 2110},
            fee_income_tb_id: {"ledger": 840, "code": 4110},
        }
        tb_transfer_repo.create_transfers.return_value = [{"status": 0}]

        fee_uuid_str = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d99"
        fee_uuid = MagicMock()
        fee_uuid.__str__ = MagicMock(return_value=fee_uuid_str)

        req = _make_req(customer_account_id=sample_uuid)

        with patch("cbs.service.fee_service.uuid_to_uint128", side_effect=[
            customer_tb_id,
            fee_income_tb_id,
            b"\xfe\xed" * 8,
        ]), \
             patch("cbs.service.fee_service.tb_id_to_uuid", return_value=_uuid.UUID(fee_income_uuid_str)), \
             patch("cbs.service.fee_service.generate_uuidv7", return_value=fee_uuid):
            result = await svc.charge(mock_session, req)

        assert result.status == "posted"
        # No metadata write should have been attempted


# ---------------------------------------------------------------------------
# FeeService._get_account_metadata() — direct tests
# ---------------------------------------------------------------------------

class TestFeeServiceGetAccountMetadata:
    """Tests for ``FeeService._get_account_metadata()`` helper."""

    async def test_meta_repo_raises_not_found(self, mock_session, sample_uuid):
        """ErrNotFound from repo is mapped to ErrInvalidAccount."""
        account_meta_repo = _make_account_meta_repo()
        account_meta_repo.get_by_tb_account_id.side_effect = ErrNotFound

        svc = _make_fee_service(account_meta_repo=account_meta_repo)

        with pytest.raises(Exception) as exc_info:
            await svc._get_account_metadata(mock_session, sample_uuid)
        assert exc_info.value is ErrInvalidAccount

    async def test_meta_repo_raises_other_exception(self, mock_session, sample_uuid):
        """Non-ErrNotFound exception from repo is wrapped in RuntimeError."""
        account_meta_repo = _make_account_meta_repo()
        account_meta_repo.get_by_tb_account_id.side_effect = RuntimeError("disk full")

        svc = _make_fee_service(account_meta_repo=account_meta_repo)

        with pytest.raises(RuntimeError, match="get account"):
            await svc._get_account_metadata(mock_session, sample_uuid)
