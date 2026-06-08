"""Unit tests for shared TB error mapping helpers.

Tests verify that TB status codes and batch errors are correctly mapped
to domain errors used by all transfer-related services.
"""

from __future__ import annotations

import pytest

from cbs.domain.errors import (
    ErrAccountClosed,
    ErrHoldAlreadyResolved,
    ErrIdempotencyConflict,
    ErrInsufficientBalance,
    ErrInvalidAccount,
    ErrLinkedEventFailed,
    ErrSameAccount,
    ErrServiceUnavailable,
    TransferError,
    ValidationError,
)
from cbs.service.errors import (
    check_hold_result,
    check_transfer_result,
    find_linked_root_cause,
    map_hold_status,
    map_tb_error,
    map_tb_transfer_status,
)


# ---------------------------------------------------------------------------
# map_tb_error()
# ---------------------------------------------------------------------------

class TestMapTBError:
    """Tests for ``map_tb_error()``."""

    def test_account_not_found(self):
        """AccountNotFound in message maps to ErrInvalidAccount."""
        err = ValueError("TB error: AccountNotFound")
        result = map_tb_error(err)
        assert result is ErrInvalidAccount

    def test_transfer_debit_account_not_found(self):
        """TransferDebitAccountNotFound maps to ErrInvalidAccount."""
        err = ValueError("TB error: TransferDebitAccountNotFound")
        result = map_tb_error(err)
        assert result is ErrInvalidAccount

    def test_already_closed(self):
        """AlreadyClosed maps to ErrAccountClosed."""
        err = ValueError("TB error: AlreadyClosed")
        result = map_tb_error(err)
        assert result is ErrAccountClosed

    def test_exceeds_credits(self):
        """ExceedsCredits maps to ErrInsufficientBalance."""
        err = ValueError("TB error: ExceedsCredits")
        result = map_tb_error(err)
        assert result is ErrInsufficientBalance

    def test_same_ledger_mismatch(self):
        """AccountsMustHaveTheSameLedger maps to ValidationError."""
        err = ValueError("TB error: AccountsMustHaveTheSameLedger")
        result = map_tb_error(err)
        assert isinstance(result, ValidationError)

    def test_connection_refused(self):
        """Connection refused maps to ErrServiceUnavailable."""
        err = ValueError("TB error: connection refused")
        result = map_tb_error(err)
        assert result is ErrServiceUnavailable

    def test_unknown_error_passthrough(self):
        """Unknown error message is returned as-is."""
        err = ValueError("some unknown error")
        result = map_tb_error(err)
        assert result is err


# ---------------------------------------------------------------------------
# map_tb_transfer_status()
# ---------------------------------------------------------------------------

class TestMapTBTransferStatus:
    """Tests for ``map_tb_transfer_status()``."""

    def test_success_created(self):
        """TransferCreated (0) returns None."""
        assert map_tb_transfer_status(0) is None

    def test_success_exists(self):
        """TransferExists (1) returns None."""
        assert map_tb_transfer_status(1) is None

    def test_insufficient_balance(self):
        """ExceedsCredits (10) returns TransferError."""
        result = map_tb_transfer_status(10)
        assert isinstance(result, TransferError)
        assert result.code == "INSUFFICIENT_BALANCE"

    def test_account_not_found(self):
        """DebitAccountNotFound (20) returns TransferError."""
        result = map_tb_transfer_status(20)
        assert isinstance(result, TransferError)
        assert result.code == "INVALID_ACCOUNT"

    def test_account_closed(self):
        """DebitAccountAlreadyClosed (30) returns TransferError."""
        result = map_tb_transfer_status(30)
        assert isinstance(result, TransferError)
        assert result.code == "ACCOUNT_CLOSED"

    def test_same_account(self):
        """AccountsMustBeDifferent (80) returns ErrSameAccount."""
        result = map_tb_transfer_status(80)
        assert result is ErrSameAccount

    def test_linked_event_failed(self):
        """LinkedEventFailed (81) returns ErrLinkedEventFailed."""
        result = map_tb_transfer_status(81)
        assert result is ErrLinkedEventFailed

    def test_idempotency_conflict(self):
        """ExistsWithDifferentFlags (40) returns ErrIdempotencyConflict."""
        result = map_tb_transfer_status(40)
        assert result is ErrIdempotencyConflict

    def test_pending_not_found(self):
        """PendingTransferNotFound (60) returns ValidationError."""
        result = map_tb_transfer_status(60)
        assert isinstance(result, ValidationError)

    def test_unknown_status(self):
        """Unknown status returns ValueError."""
        result = map_tb_transfer_status(999)
        assert isinstance(result, ValueError)


# ---------------------------------------------------------------------------
# check_transfer_result()
# ---------------------------------------------------------------------------

class TestCheckTransferResult:
    """Tests for ``check_transfer_result()``."""

    def test_success(self):
        """Successful result returns None."""
        results = [{"status": 0}]
        assert check_transfer_result(results, None) is None

    def test_failure_in_results(self):
        """Failed status in results returns domain error."""
        results = [{"status": 10}]  # ExceedsCredits
        error = check_transfer_result(results, None)
        assert isinstance(error, TransferError)

    def test_failure_from_tb_error(self):
        """No results + tb_error returns mapped error."""
        err = ValueError("connection refused")
        result = check_transfer_result([], err)
        assert result is ErrServiceUnavailable

    def test_no_error(self):
        """No results and no error returns None."""
        assert check_transfer_result([], None) is None


# ---------------------------------------------------------------------------
# find_linked_root_cause()
# ---------------------------------------------------------------------------

class TestFindLinkedRootCause:
    """Tests for ``find_linked_root_cause()``."""

    def test_finds_root_cause(self):
        """Scans results for first non-LinkedEventFailed status."""
        results = [
            {"status": 81},  # LinkedEventFailed (dependent leg)
            {"status": 10},   # ExceedsCredits (root cause)
        ]
        error = find_linked_root_cause(results)
        assert isinstance(error, TransferError)

    def test_all_linked_failed(self):
        """All LinkedEventFailed returns ErrLinkedEventFailed."""
        results = [
            {"status": 81},
            {"status": 82},
        ]
        error = find_linked_root_cause(results)
        assert error is ErrLinkedEventFailed

    def test_all_success(self):
        """All success returns None."""
        results = [
            {"status": 0},
            {"status": 1},
        ]
        assert find_linked_root_cause(results) is None


# ---------------------------------------------------------------------------
# map_hold_status()
# ---------------------------------------------------------------------------

class TestMapHoldStatus:
    """Tests for ``map_hold_status()``."""

    def test_success(self):
        """TransferCreated (0) returns None."""
        assert map_hold_status(0) is None

    def test_pending_not_found(self):
        """PendingTransferNotFound (60) maps to ErrInsufficientBalance for holds."""
        result = map_hold_status(60)
        assert result is ErrInsufficientBalance

    def test_already_resolved(self):
        """PendingTransferNotPending (61) maps to ErrHoldAlreadyResolved."""
        result = map_hold_status(61)
        assert result is ErrHoldAlreadyResolved

    def test_insufficient_balance(self):
        """ExceedsCredits (10) maps to ErrInsufficientBalance."""
        result = map_hold_status(10)
        assert result is ErrInsufficientBalance


# ---------------------------------------------------------------------------
# check_hold_result()
# ---------------------------------------------------------------------------

class TestCheckHoldResult:
    """Tests for ``check_hold_result()``."""

    def test_success(self):
        """Successful result returns None."""
        results = [{"status": 0}]
        assert check_hold_result(results, None) is None

    def test_failure_in_results(self):
        """Failed status in results returns domain error."""
        results = [{"status": 61}]  # PendingTransferNotPending
        error = check_hold_result(results, None)
        assert error is ErrHoldAlreadyResolved
