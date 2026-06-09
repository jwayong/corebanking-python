"""Unit tests for LoanService (business logic layer).

Tests verify request validation, dual-write orchestration (TB-first / PG-second),
error propagation from repository layers, and linked transfer handling — all using
mocked dependencies.

Mirrors the style of :mod:`tests.unit.service.test_account_service`.
"""

from __future__ import annotations

import asyncio
import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import uuid as _uuid

from cbs.domain.errors import (
    ErrAccountClosed,
    ErrInvalidAccount,
    ErrInsufficientBalance,
    ErrLiquidityPoolUnavailable,
    ErrNotFound,
    ErrRepaymentExceedsOutstanding,
    ErrServiceUnavailable,
    TransferError,
    ValidationError,
)
from cbs.domain.loans import (
    LoanDisbursementRequest,
    LoanRepaymentRequest,
    LoanRepaymentWithFeeRequest,
)
from cbs.service.loan_service import NewLoanService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tb_transfer_repo():
    """Create a mock TBTransferRepo with AsyncMock create_transfers."""
    repo = MagicMock()
    repo.create_transfers = AsyncMock()
    return repo


def _make_tb_account_repo():
    """Create a mock TBAccountRepo with AsyncMock lookup_accounts."""
    repo = MagicMock()
    repo.lookup_accounts = AsyncMock()
    return repo


def _make_account_meta_repo():
    """Create a mock AccountMetaRepo with AsyncMock get_by_tb_account_id."""
    repo = MagicMock()
    repo.get_by_tb_account_id = AsyncMock()
    return repo


def _make_system_account_repo():
    """Create a mock SystemAccountRepo with AsyncMock get_by_code."""
    repo = MagicMock()
    repo.get_by_code = AsyncMock()
    return repo


def _make_metadata_writer():
    """Create a mock MetadataWriter with AsyncMock create_transfer_metadata."""
    writer = MagicMock()
    writer.create_transfer_metadata = AsyncMock()
    return writer


def _make_loan_repo():
    """Create a mock LoanRepo with AsyncMock set_disbursed_at and reduce_outstanding."""
    repo = MagicMock()
    repo.set_disbursed_at = AsyncMock()
    repo.reduce_outstanding = AsyncMock()
    return repo


def _make_account_meta(
    id=1,
    category="loan",
    status="active",
):
    """Build a mock AccountWithProduct-like object for PG metadata."""
    meta = MagicMock()
    meta.id = id
    meta.category = category
    meta.status = status
    return meta


def _make_tb_account(
    ledger=840,
    closed=False,
):
    """Build a TB account dict for lookup_accounts results."""
    return {
        "ledger": ledger,
        "closed": closed,
    }


def _make_tb_result(status=0):
    """Build a TB create result dict. status=0 means success."""
    return {"status": status}


def _make_loan_service(
    tb_transfer_repo=None,
    tb_account_repo=None,
    account_meta_repo=None,
    system_account_repo=None,
    metadata_writer=None,
    loan_repo=None,
):
    """Create a LoanService with mocked dependencies."""
    return NewLoanService(
        tb_transfer_repo=tb_transfer_repo or _make_tb_transfer_repo(),
        tb_account_repo=tb_account_repo or _make_tb_account_repo(),
        account_meta_repo=account_meta_repo or _make_account_meta_repo(),
        system_account_repo=system_account_repo or _make_system_account_repo(),
        metadata_writer=metadata_writer or _make_metadata_writer(),
        loan_repo=loan_repo or _make_loan_repo(),
    )


def _get_valid_uuids():
    """Return a pair of valid UUID strings for loan and deposit accounts."""
    return (
        "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
        "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
    )


def _identity_bytes(u):
    """Identity function for uuid_to_uint128: returns UUID.bytes as-is.

    This avoids the real function's byte-order reversal so our mock dict
    keys (UUID.bytes) match what lookup_accounts receives.

    When called with a MagicMock (e.g., from patched generate_uuidv7),
    returns deterministic bytes based on call context.
    """
    if hasattr(u, "bytes") and isinstance(getattr(u, "bytes", None), bytes):
        return u.bytes
    # Fallback for MagicMock inputs: return deterministic bytes
    return b"\x00" * 16


def _identity_uuid(raw):
    """Identity function for uint128_to_uuid: wraps raw bytes in a UUID.

    When called with UUID.bytes (from _identity_bytes), the resulting
    UUID.bytes is identical to the input, so get_by_tb_account_id lookups
    match our mock dict keys.

    When called with a MagicMock, returns a deterministic mock UUID.
    """
    if isinstance(raw, bytes):
        return _uuid.UUID(bytes=raw)
    # Fallback for MagicMock inputs
    mock = MagicMock()
    mock.__str__ = MagicMock(return_value="0195aabb-ccdd-eeff-0000-111122223333")
    return mock


def _setup_validate_loan_accounts(
    tb_account_repo, account_meta_repo, loan_bytes, deposit_bytes
):
    """Configure mocks for a successful _validate_loan_accounts call.

    loan_bytes and deposit_bytes are the byte values returned by our
    patched uuid_to_uint128 (i.e., UUID.bytes). These serve as keys in
    both the lookup_accounts dict and the get_by_tb_account_id matcher.

    Returns the loan_meta mock object.
    """
    loan_meta = _make_account_meta(id=1, category="loan", status="active")
    deposit_meta = _make_account_meta(id=2, category="deposit", status="active")

    tb_map = {
        loan_bytes: _make_tb_account(ledger=840),
        deposit_bytes: _make_tb_account(ledger=840),
    }
    tb_account_repo.lookup_accounts.return_value = tb_map

    # uint128_to_uuid(loan_bytes).bytes == loan_bytes (round-trip with identity)
    def get_by_tb_account_id_side_effect(session, tb_id_bytes):
        if tb_id_bytes == loan_bytes:
            return loan_meta
        if tb_id_bytes == deposit_bytes:
            return deposit_meta
        return None

    account_meta_repo.get_by_tb_account_id.side_effect = (
        get_by_tb_account_id_side_effect
    )

    return loan_meta


def _patch_uuid_and_transfer(loan_uuid_str, deposit_uuid_str):
    """Return context managers for patching uuid + transfer utilities.

    Patches:
    - uuid_to_uint128 -> identity (returns UUID.bytes, no byte reversal)
    - uint128_to_uuid -> identity (wraps bytes in UUID, no byte reversal)
    - generate_uuidv7 -> deterministic mock UUID

    This ensures all byte conversions are round-trip identity so our
    mock dict keys (UUID.bytes) match throughout the call chain.

    Returns (patches_list, mock_transfer_uuid).
    """
    loan_uuid = _uuid.UUID(loan_uuid_str)
    deposit_uuid = _uuid.UUID(deposit_uuid_str)

    mock_transfer_uuid = MagicMock()
    mock_transfer_uuid.__str__ = MagicMock(
        return_value="0195aabb-ccdd-eeff-0000-111122223333"
    )

    patches = [
        patch("cbs.service.loan_service.uuid_to_uint128", side_effect=[
            _identity_bytes(loan_uuid),
            _identity_bytes(deposit_uuid),
            # generate_uuidv7 calls (transfer IDs) — return mock bytes
            _identity_bytes(mock_transfer_uuid) if hasattr(mock_transfer_uuid, "bytes") else b"\x00" * 16,
        ]),
        patch("cbs.service.loan_service.uint128_to_uuid", side_effect=[
            _identity_uuid(_identity_bytes(loan_uuid)),
            _identity_uuid(_identity_bytes(deposit_uuid)),
        ]),
        patch(
            "cbs.service.loan_service.generate_uuidv7",
            return_value=mock_transfer_uuid,
        ),
    ]

    return patches, mock_transfer_uuid


def _patch_uuid_and_transfer_repay(loan_uuid_str, debit_uuid_str):
    """Patch uuid + transfer utilities for repay flow.

    In repay, uuid_to_uint128 is called: loan, debit (account IDs), then transfer ID.
    uint128_to_uuid is called: loan, debit (for _get_account_meta).
    """
    loan_uuid = _uuid.UUID(loan_uuid_str)
    debit_uuid = _uuid.UUID(debit_uuid_str)

    mock_transfer_uuid = MagicMock()
    mock_transfer_uuid.__str__ = MagicMock(
        return_value="0195aabb-ccdd-eeff-0000-111122223334"
    )

    patches = [
        patch("cbs.service.loan_service.uuid_to_uint128", side_effect=[
            _identity_bytes(loan_uuid),
            _identity_bytes(debit_uuid),
            # transfer ID from generate_uuidv7
            b"\x00" * 16,
        ]),
        patch("cbs.service.loan_service.uint128_to_uuid", side_effect=[
            _identity_uuid(_identity_bytes(loan_uuid)),
            _identity_uuid(_identity_bytes(debit_uuid)),
        ]),
        patch(
            "cbs.service.loan_service.generate_uuidv7",
            return_value=mock_transfer_uuid,
        ),
    ]

    return patches, mock_transfer_uuid


def _patch_uuid_and_transfer_repay_with_fee(
    loan_uuid_str, debit_uuid_str, num_legs=3
):
    """Patch uuid + transfer utilities for repay_with_fee flow.

    All side_effect values are provided upfront so the coroutine can
    consume them without running out mid-flight.

    Args:
        num_legs: Number of non-zero legs (1, 2, or 3). Determines how
            many extra uuid_to_uint128/uint128_to_uuid calls are needed.

    Returns (patches_list, mock_corr_uuid).
    """
    loan_uuid = _uuid.UUID(loan_uuid_str)
    debit_uuid = _uuid.UUID(debit_uuid_str)

    mock_corr_uuid = MagicMock()
    mock_corr_uuid.__str__ = MagicMock(
        return_value="0195aabb-ccdd-eeff-0000-111122223336"
    )

    # uuid_to_uint128 call sequence:
    # 1. loan account, 2. debit account (before _validate_loan_accounts)
    # 3. correlation ID (after validation, from generate_uuidv7)
    # For each leg with principal>0: 1x transfer ID (always, leg 1)
    # For interest leg (leg 2): 1x transfer ID + 1x system acct conversion
    # For fee leg (leg 3): 1x transfer ID + 1x system acct conversion
    uuid_side_effect = [
        _identity_bytes(loan_uuid),   # loan account
        _identity_bytes(debit_uuid),  # debit account
    ]

    # uint128_to_uuid call sequence:
    # 1-2. loan + debit account meta (in _validate_loan_accounts)
    # For interest leg: 1x system acct conversion
    # For fee leg: 1x system acct conversion
    # Response building loop (2 calls per leg):
    #   - leg UUID from transfer id
    #   - credit account UUID from leg_infos[i][2]
    uint_side_effect = [
        _identity_uuid(_identity_bytes(loan_uuid)),   # loan account meta
        _identity_uuid(_identity_bytes(debit_uuid)),  # debit account meta
    ]

    # Correlation ID (always called after validation)
    corr_bytes = b"\xcc" * 16
    uuid_side_effect.append(corr_bytes)

    # Leg transfer IDs (one per leg, always)
    for i in range(num_legs):
        uuid_side_effect.append(bytes([0xdd + i]) * 16)

    # System account conversions: interest (if leg >= 2), fee (if leg == 3)
    # Each uses: uuid_to_uint128(tb_id_to_uuid(system_bytes))
    if num_legs >= 2:
        interest_sys_bytes = b"\xaa" * 16
        uuid_side_effect.append(b"\xaa" * 16)  # interest system acct tb_id
        uint_side_effect.append(_identity_uuid(interest_sys_bytes))
    if num_legs == 3:
        fee_sys_bytes = b"\xbb" * 16
        uuid_side_effect.append(b"\xbb" * 16)  # fee system acct tb_id
        uint_side_effect.append(_identity_uuid(fee_sys_bytes))

    # Response building: 2 calls per leg (leg UUID + credit account UUID)
    for i in range(num_legs):
        uint_side_effect.append(mock_corr_uuid)       # leg UUID from t["id"]
        uint_side_effect.append(_identity_uuid(bytes([0xdd + i]) * 16))  # credit acct UUID

    patches = [
        patch("cbs.service.loan_service.uuid_to_uint128", side_effect=uuid_side_effect),
        patch("cbs.service.loan_service.uint128_to_uuid", side_effect=uint_side_effect),
        patch(
            "cbs.service.loan_service.generate_uuidv7",
            return_value=mock_corr_uuid,
        ),
    ]

    return patches, mock_corr_uuid


# ---------------------------------------------------------------------------
# LoanService.disburse()
# ---------------------------------------------------------------------------


class TestLoanServiceDisburse:
    """Tests for ``LoanService.disburse()``."""

    async def test_success_happy_path(self, mock_session):
        """Happy path: validate -> resolve accounts -> TB create -> set_disbursed_at."""
        loan_uuid_str, credit_uuid_str = _get_valid_uuids()

        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        loan_repo = _make_loan_repo()

        loan_uuid = _uuid.UUID(loan_uuid_str)
        credit_uuid = _uuid.UUID(credit_uuid_str)

        loan_meta = _setup_validate_loan_accounts(
            tb_account_repo, account_meta_repo, loan_uuid.bytes, credit_uuid.bytes
        )

        tb_transfer_repo.create_transfers.return_value = [_make_tb_result(status=0)]

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            loan_repo=loan_repo,
        )

        req = LoanDisbursementRequest(
            loan_account_id=loan_uuid_str,
            credit_account_id=credit_uuid_str,
            amount=100000,
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer(loan_uuid_str, credit_uuid_str)
        with patches[0], patches[1], patches[2]:
            result = await svc.disburse(mock_session, req)

        assert result.transfer_type == "disbursement"
        assert result.loan_account_id == loan_uuid_str
        assert result.credit_account_id == credit_uuid_str
        assert result.amount.amount == 100000
        assert result.status == "posted"

        # Verify dual-write order: TB first, then PG
        tb_transfer_repo.create_transfers.assert_awaited_once()
        loan_repo.set_disbursed_at.assert_awaited_once()

    async def test_validation_error_empty_accounts(self, mock_session):
        """Empty loan_account_id raises ValidationError before touching repos."""
        svc = _make_loan_service()

        req = LoanDisbursementRequest(
            loan_account_id="",
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=100000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="loan_account_id is required"):
            await svc.disburse(mock_session, req)

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Invalid UUID format raises ValidationError before touching repos."""
        svc = _make_loan_service()

        req = LoanDisbursementRequest(
            loan_account_id="not-a-uuid",
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=100000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="loan_account_id must be a valid UUID"):
            await svc.disburse(mock_session, req)

    async def test_loan_account_wrong_category(self, mock_session):
        """Loan account with category != 'loan' raises ValidationError."""
        loan_uuid_str, credit_uuid_str = _get_valid_uuids()

        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()

        loan_uuid = _uuid.UUID(loan_uuid_str)
        credit_uuid = _uuid.UUID(credit_uuid_str)

        loan_bytes = loan_uuid.bytes
        credit_bytes = credit_uuid.bytes

        # Loan account has wrong category
        loan_meta = _make_account_meta(id=1, category="deposit", status="active")
        deposit_meta = _make_account_meta(id=2, category="deposit", status="active")

        tb_map = {
            loan_bytes: _make_tb_account(ledger=840),
            credit_bytes: _make_tb_account(ledger=840),
        }
        tb_account_repo.lookup_accounts.return_value = tb_map

        def get_meta_side_effect(session, tb_id_bytes):
            if tb_id_bytes == loan_bytes:
                return loan_meta
            if tb_id_bytes == credit_bytes:
                return deposit_meta
            return None

        account_meta_repo.get_by_tb_account_id.side_effect = get_meta_side_effect

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
        )

        req = LoanDisbursementRequest(
            loan_account_id=loan_uuid_str,
            credit_account_id=credit_uuid_str,
            amount=100000,
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer(loan_uuid_str, credit_uuid_str)
        with patches[0], patches[1], patches[2]:
            with pytest.raises(ValidationError, match="loan account must be a loan product"):
                await svc.disburse(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_deposit_account_wrong_category(self, mock_session):
        """Credit account with category != 'deposit' raises ValidationError."""
        loan_uuid_str, credit_uuid_str = _get_valid_uuids()

        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()

        loan_uuid = _uuid.UUID(loan_uuid_str)
        credit_uuid = _uuid.UUID(credit_uuid_str)

        loan_bytes = loan_uuid.bytes
        credit_bytes = credit_uuid.bytes

        loan_meta = _make_account_meta(id=1, category="loan", status="active")
        deposit_meta = _make_account_meta(id=2, category="loan", status="active")

        tb_map = {
            loan_bytes: _make_tb_account(ledger=840),
            credit_bytes: _make_tb_account(ledger=840),
        }
        tb_account_repo.lookup_accounts.return_value = tb_map

        def get_meta_side_effect(session, tb_id_bytes):
            if tb_id_bytes == loan_bytes:
                return loan_meta
            if tb_id_bytes == credit_bytes:
                return deposit_meta
            return None

        account_meta_repo.get_by_tb_account_id.side_effect = get_meta_side_effect

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
        )

        req = LoanDisbursementRequest(
            loan_account_id=loan_uuid_str,
            credit_account_id=credit_uuid_str,
            amount=100000,
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer(loan_uuid_str, credit_uuid_str)
        with patches[0], patches[1], patches[2]:
            with pytest.raises(ValidationError, match="credit account must be a deposit product"):
                await svc.disburse(mock_session, req)

        tb_transfer_repo.create_transfers.assert_not_called()

    async def test_tb_create_fails_mapped_error(self, mock_session):
        """TB create raises ValueError -> mapped via map_tb_error."""
        loan_uuid_str, credit_uuid_str = _get_valid_uuids()

        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        loan_repo = _make_loan_repo()

        loan_uuid = _uuid.UUID(loan_uuid_str)
        credit_uuid = _uuid.UUID(credit_uuid_str)

        _setup_validate_loan_accounts(
            tb_account_repo, account_meta_repo, loan_uuid.bytes, credit_uuid.bytes
        )

        # TB raises ValueError with "ExceedsCredits" -> maps to ErrInsufficientBalance
        tb_transfer_repo.create_transfers.side_effect = ValueError("ExceedsCredits")

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            loan_repo=loan_repo,
        )

        req = LoanDisbursementRequest(
            loan_account_id=loan_uuid_str,
            credit_account_id=credit_uuid_str,
            amount=100000,
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer(loan_uuid_str, credit_uuid_str)
        with patches[0], patches[1], patches[2]:
            with pytest.raises(Exception) as exc_info:
                await svc.disburse(mock_session, req)
            assert exc_info.value is ErrInsufficientBalance

        # PG step should NOT be reached
        loan_repo.set_disbursed_at.assert_not_called()


# ---------------------------------------------------------------------------
# LoanService.repay()
# ---------------------------------------------------------------------------


class TestLoanServiceRepay:
    """Tests for ``LoanService.repay()``."""

    async def test_success_happy_path(self, mock_session):
        """Happy path: validate -> resolve accounts -> TB create -> reduce_outstanding."""
        loan_uuid_str, debit_uuid_str = _get_valid_uuids()

        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        loan_repo = _make_loan_repo()

        loan_uuid = _uuid.UUID(loan_uuid_str)
        debit_uuid = _uuid.UUID(debit_uuid_str)

        loan_meta = _setup_validate_loan_accounts(
            tb_account_repo, account_meta_repo, loan_uuid.bytes, debit_uuid.bytes
        )

        tb_transfer_repo.create_transfers.return_value = [_make_tb_result(status=0)]

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            loan_repo=loan_repo,
        )

        req = LoanRepaymentRequest(
            debit_account_id=debit_uuid_str,
            loan_account_id=loan_uuid_str,
            amount=50000,
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer_repay(loan_uuid_str, debit_uuid_str)
        with patches[0], patches[1], patches[2]:
            result = await svc.repay(mock_session, req)

        assert result.transfer_type == "repayment"
        assert result.debit_account_id == debit_uuid_str
        assert result.loan_account_id == loan_uuid_str
        assert result.amount.amount == 50000
        assert result.status == "posted"

        tb_transfer_repo.create_transfers.assert_awaited_once()
        loan_repo.reduce_outstanding.assert_awaited_once()

    async def test_repayment_exceeds_outstanding(self, mock_session):
        """reduce_outstanding raises ErrNotFound -> mapped to ErrRepaymentExceedsOutstanding."""
        loan_uuid_str, debit_uuid_str = _get_valid_uuids()

        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        loan_repo = _make_loan_repo()

        loan_uuid = _uuid.UUID(loan_uuid_str)
        debit_uuid = _uuid.UUID(debit_uuid_str)

        _setup_validate_loan_accounts(
            tb_account_repo, account_meta_repo, loan_uuid.bytes, debit_uuid.bytes
        )

        tb_transfer_repo.create_transfers.return_value = [_make_tb_result(status=0)]
        loan_repo.reduce_outstanding.side_effect = ErrNotFound

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            loan_repo=loan_repo,
        )

        req = LoanRepaymentRequest(
            debit_account_id=debit_uuid_str,
            loan_account_id=loan_uuid_str,
            amount=999000,
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer_repay(loan_uuid_str, debit_uuid_str)
        with patches[0], patches[1], patches[2]:
            with pytest.raises(Exception) as exc_info:
                await svc.repay(mock_session, req)
            assert exc_info.value is ErrRepaymentExceedsOutstanding

    async def test_validation_error(self, mock_session):
        """Empty debit_account_id raises ValidationError before touching repos."""
        svc = _make_loan_service()

        req = LoanRepaymentRequest(
            debit_account_id="",
            loan_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
            amount=50000,
            currency="USD",
        )

        with pytest.raises(ValidationError, match="debit_account_id is required"):
            await svc.repay(mock_session, req)


# ---------------------------------------------------------------------------
# LoanService.repay_with_fee()
# ---------------------------------------------------------------------------


class TestLoanServiceRepayWithFee:
    """Tests for ``LoanService.repay_with_fee()``."""

    def _setup_common_mocks(self, loan_uuid_str, debit_uuid_str):
        """Set up common mocks for repay_with_fee tests.

        Returns (tb_transfer_repo, tb_account_repo, account_meta_repo,
                 system_account_repo, loan_repo).
        """
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        loan_repo = _make_loan_repo()

        loan_uuid = _uuid.UUID(loan_uuid_str)
        debit_uuid = _uuid.UUID(debit_uuid_str)

        _setup_validate_loan_accounts(
            tb_account_repo, account_meta_repo, loan_uuid.bytes, debit_uuid.bytes
        )

        return (
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            loan_repo,
        )

    async def test_success_three_legs(self, mock_session):
        """All 3 legs (principal + interest + fee): linked TB transfers -> reduce_outstanding."""
        loan_uuid_str, debit_uuid_str = _get_valid_uuids()

        (
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            loan_repo,
        ) = self._setup_common_mocks(loan_uuid_str, debit_uuid_str)

        # System accounts for interest and fee
        interest_acct_bytes = b"\xaa" * 16
        fee_acct_bytes = b"\xbb" * 16
        system_account_repo.get_by_code.side_effect = [interest_acct_bytes, fee_acct_bytes]

        # 3 transfer results (all success)
        tb_transfer_repo.create_transfers.return_value = [
            _make_tb_result(status=0),
            _make_tb_result(status=0),
            _make_tb_result(status=0),
        ]

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            loan_repo=loan_repo,
        )

        req = LoanRepaymentWithFeeRequest(
            loan_account_id=loan_uuid_str,
            debit_account_id=debit_uuid_str,
            principal=50000,
            interest_amount=5000,
            fee_amount=1000,
            currency="USD",
        )

        patches, mock_corr_uuid = _patch_uuid_and_transfer_repay_with_fee(
            loan_uuid_str, debit_uuid_str, num_legs=3
        )

        with patches[0], patches[1], patches[2]:
            with patch(
                "cbs.service.loan_service.tb_id_to_uuid",
                side_effect=[
                    _uuid.UUID(bytes=interest_acct_bytes),
                    _uuid.UUID(bytes=fee_acct_bytes),
                ],
            ):
                result = await svc.repay_with_fee(mock_session, req)

        assert result.transfer_type == "repay_with_fee"
        assert result.status == "posted"
        assert len(result.legs) == 3

        # Verify leg codes: repayment, interest, fee
        assert result.legs[0].code == "repayment"
        assert result.legs[1].code == "interest"
        assert result.legs[2].code == "fee"

        # Verify amounts
        assert result.legs[0].amount.amount == 50000
        assert result.legs[1].amount.amount == 5000
        assert result.legs[2].amount.amount == 1000

        # System accounts looked up for interest and fee
        assert system_account_repo.get_by_code.await_count == 2

        # reduce_outstanding called with principal only
        loan_repo.reduce_outstanding.assert_awaited_once()
        call_args = loan_repo.reduce_outstanding.call_args
        assert call_args[0][1] == 50000

    async def test_success_two_legs_principal_interest(self, mock_session):
        """2 legs (principal + interest, no fee): linked TB transfers."""
        loan_uuid_str, debit_uuid_str = _get_valid_uuids()

        (
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            loan_repo,
        ) = self._setup_common_mocks(loan_uuid_str, debit_uuid_str)

        interest_acct_bytes = b"\xaa" * 16
        system_account_repo.get_by_code.return_value = interest_acct_bytes

        tb_transfer_repo.create_transfers.return_value = [
            _make_tb_result(status=0),
            _make_tb_result(status=0),
        ]

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            loan_repo=loan_repo,
        )

        req = LoanRepaymentWithFeeRequest(
            loan_account_id=loan_uuid_str,
            debit_account_id=debit_uuid_str,
            principal=50000,
            interest_amount=5000,
            fee_amount=0,  # no fee leg
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer_repay_with_fee(
            loan_uuid_str, debit_uuid_str, num_legs=2
        )

        with patches[0], patches[1], patches[2]:
            with patch(
                "cbs.service.loan_service.tb_id_to_uuid",
                return_value=_uuid.UUID(bytes=interest_acct_bytes),
            ):
                result = await svc.repay_with_fee(mock_session, req)

        assert len(result.legs) == 2
        assert result.legs[0].code == "repayment"
        assert result.legs[1].code == "interest"

        # Only interest system account looked up (no fee)
        assert system_account_repo.get_by_code.await_count == 1

    async def test_success_one_leg_principal_only(self, mock_session):
        """1 leg only (principal, no interest/fee)."""
        loan_uuid_str, debit_uuid_str = _get_valid_uuids()

        (
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            loan_repo,
        ) = self._setup_common_mocks(loan_uuid_str, debit_uuid_str)

        tb_transfer_repo.create_transfers.return_value = [_make_tb_result(status=0)]

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            loan_repo=loan_repo,
        )

        req = LoanRepaymentWithFeeRequest(
            loan_account_id=loan_uuid_str,
            debit_account_id=debit_uuid_str,
            principal=50000,
            interest_amount=0,
            fee_amount=0,
            currency="USD",
        )

        patches, _ = _patch_uuid_and_transfer_repay_with_fee(
            loan_uuid_str, debit_uuid_str, num_legs=1
        )

        with patches[0], patches[1], patches[2]:
            result = await svc.repay_with_fee(mock_session, req)

        assert len(result.legs) == 1
        assert result.legs[0].code == "repayment"

        # No system account lookups needed
        system_account_repo.get_by_code.assert_not_called()

    async def test_system_account_not_found(self, mock_session):
        """Interest system account not found -> ErrLiquidityPoolUnavailable."""
        loan_uuid_str, debit_uuid_str = _get_valid_uuids()

        (
            tb_transfer_repo,
            tb_account_repo,
            account_meta_repo,
            system_account_repo,
            loan_repo,
        ) = self._setup_common_mocks(loan_uuid_str, debit_uuid_str)

        # Interest system account not found
        system_account_repo.get_by_code.return_value = None

        svc = _make_loan_service(
            tb_transfer_repo=tb_transfer_repo,
            tb_account_repo=tb_account_repo,
            account_meta_repo=account_meta_repo,
            system_account_repo=system_account_repo,
            loan_repo=loan_repo,
        )

        req = LoanRepaymentWithFeeRequest(
            loan_account_id=loan_uuid_str,
            debit_account_id=debit_uuid_str,
            principal=50000,
            interest_amount=5000,
            fee_amount=1000,
            currency="USD",
        )

        # For this test: error occurs after 2 account lookups + correlation ID
        # + interest system account lookup. uuid_to_uint128 is called 3 times
        # (loan, debit, corr) before the system account lookup.
        patches, _ = _patch_uuid_and_transfer_repay_with_fee(
            loan_uuid_str, debit_uuid_str, num_legs=3
        )

        with patches[0], patches[1], patches[2]:
            with pytest.raises(Exception) as exc_info:
                await svc.repay_with_fee(mock_session, req)
            assert exc_info.value is ErrLiquidityPoolUnavailable

        # TB create should NOT be called
        tb_transfer_repo.create_transfers.assert_not_called()
