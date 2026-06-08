"""Shared TigerBeetle error mapping for service layer.

Provides functions to map TB status codes and batch errors to domain
errors used by all transfer-related services.

Mirrors corebanking/internal/service/transfer_service.go error mapping functions.
"""

from __future__ import annotations

import structlog

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

log = structlog.get_logger()


# TB status codes (from tigerbeetle library)
_TRANSFER_CREATED = 0
_TRANSFER_EXISTS = 1

# Insufficient funds statuses
_INSUFFICIENT_BALANCE_STATUSES = frozenset({
    10,   # TransferExceedsCredits
    11,   # TransferExceedsDebits
    12,   # TransferOverflowsDebitsPending
    13,   # TransferOverflowsCreditsPending
    14,   # TransferOverflowsDebitsPosted
    15,   # TransferOverflowsCreditsPosted
    16,   # TransferOverflowsDebits
    17,   # TransferOverflowsCredits
})

# Account not found statuses
_ACCOUNT_NOT_FOUND_STATUSES = frozenset({
    20,   # TransferDebitAccountNotFound
    21,   # TransferCreditAccountNotFound
})

# Account closed statuses
_ACCOUNT_CLOSED_STATUSES = frozenset({
    30,   # TransferDebitAccountAlreadyClosed
    31,   # TransferCreditAccountAlreadyClosed
})

# Idempotency conflict statuses
_IDEMPOTENCY_CONFLICT_STATUSES = frozenset({
    40,   # TransferExistsWithDifferentFlags
    41,   # TransferExistsWithDifferentDebitAccountID
    42,   # TransferExistsWithDifferentCreditAccountID
    43,   # TransferExistsWithDifferentAmount
    44,   # TransferExistsWithDifferentUserData128
    45,   # TransferExistsWithDifferentUserData64
    46,   # TransferExistsWithDifferentUserData32
    47,   # TransferExistsWithDifferentPendingID
    48,   # TransferExistsWithDifferentTimeout
    49,   # TransferExistsWithDifferentLedger
    50,   # TransferExistsWithDifferentCode
})

# Pending transfer error statuses with specific messages
_PENDING_TRANSFER_ERRORS: dict[int, str] = {
    60: "pending transfer not found",                          # TransferPendingTransferNotFound
    61: "referenced transfer is not pending",                  # TransferPendingTransferNotPending
    62: "pending transfer has different debit account",        # TransferPendingTransferHasDifferentDebitAccountID
    63: "pending transfer has different credit account",       # TransferPendingTransferHasDifferentCreditAccountID
    64: "pending transfer has different ledger",               # TransferPendingTransferHasDifferentLedger
    65: "pending transfer has different code",                 # TransferPendingTransferHasDifferentCode
    66: "amount exceeds pending transfer amount",              # TransferExceedsPendingTransferAmount
    67: "pending transfer has different amount",               # TransferPendingTransferHasDifferentAmount
    68: "pending transfer already posted",                     # TransferPendingTransferAlreadyPosted
    69: "pending transfer already voided",                     # TransferPendingTransferAlreadyVoided
    70: "pending transfer expired",                            # TransferPendingTransferExpired
}


def map_tb_error(err: Exception) -> Exception:
    """Map a TigerBeetle batch error to a domain error.

    Uses string matching on the error message, which mirrors the Go
    implementation's approach for connection-level errors.

    Args:
        err: The exception from the TB client.

    Returns:
        A domain error (sentinel or ValidationError).
    """
    if err is None:
        return ErrServiceUnavailable

    msg = str(err)
    if (
        "AccountNotFound" in msg
        or "TransferDebitAccountNotFound" in msg
        or "TransferCreditAccountNotFound" in msg
    ):
        return ErrInvalidAccount
    if (
        "AlreadyClosed" in msg
        or "TransferDebitAccountAlreadyClosed" in msg
        or "TransferCreditAccountAlreadyClosed" in msg
    ):
        return ErrAccountClosed
    if "ExceedsCredits" in msg or "ExceedsDebits" in msg:
        return ErrInsufficientBalance
    if "AccountsMustHaveTheSameLedger" in msg:
        return ValidationError("accounts must be on the same ledger")
    if "TransferMustHaveTheSameLedgerAsAccounts" in msg:
        return ValidationError("transfer ledger does not match account ledgers")
    if "connection refused" in msg or "network" in msg:
        return ErrServiceUnavailable

    return err


def map_tb_transfer_status(status: int) -> Exception | None:
    """Map a single TB transfer result status to a domain error.

    Args:
        status: Integer status code from TB create result.

    Returns:
        None on success, or a domain error for failure statuses.
    """
    # Success codes
    if status in (_TRANSFER_CREATED, _TRANSFER_EXISTS):
        return None

    # Insufficient funds
    if status in _INSUFFICIENT_BALANCE_STATUSES:
        return TransferError(
            code="INSUFFICIENT_BALANCE",
            message="account has insufficient available balance",
        )

    # Account not found
    if status in _ACCOUNT_NOT_FOUND_STATUSES:
        return TransferError(
            code="INVALID_ACCOUNT",
            message="account does not exist or is inactive",
        )

    # Account closed
    if status in _ACCOUNT_CLOSED_STATUSES:
        return TransferError(
            code="ACCOUNT_CLOSED",
            message="account is closed",
        )

    # Ledger mismatch
    if status == 72:  # TransferAccountsMustHaveTheSameLedger
        return ValidationError("accounts must be on the same ledger")
    if status == 73:  # TransferTransferMustHaveTheSameLedgerAsAccounts
        return ValidationError("transfer ledger does not match account ledgers")

    # Same account
    if status == 80:  # TransferAccountsMustBeDifferent
        return ErrSameAccount

    # Linked chain failure
    if status in (81, 82):  # TransferLinkedEventFailed, TransferLinkedEventChainOpen
        return ErrLinkedEventFailed

    # Idempotency conflict
    if status in _IDEMPOTENCY_CONFLICT_STATUSES:
        return ErrIdempotencyConflict

    # Pending transfer errors
    if status in _PENDING_TRANSFER_ERRORS:
        return ValidationError(_PENDING_TRANSFER_ERRORS[status])

    # Overflow timeout
    if status == 71:  # TransferOverflowsTimeout
        return ErrServiceUnavailable

    # Default — unknown status
    return ValueError(f"transfer error: status={status}")


def check_transfer_result(
    results: list[dict], tb_error: Exception | None, map_fn=None
) -> Exception | None:
    """Inspect TB create results and return domain error if any transfer failed.

    Mirrors the Go M4 pattern: check results slice first, then fall back
    to error. This handles the case where TB returns both results and an error.

    Args:
        results: List of result dicts from TB create call.
        tb_error: Exception from the TB client (may be None even with errors).
        map_fn: Optional custom status mapper. Defaults to ``map_tb_transfer_status``.

    Returns:
        Domain error if a transfer failed, None on success.
    """
    if map_fn is None:
        map_fn = map_tb_transfer_status

    # Check results first (M4 pattern).
    if results:
        for i, result in enumerate(results):
            status = result.get("status")
            if status not in (0, 1):  # TransferCreated=0, TransferExists=1
                return map_fn(status)

    # Fall back to error if no results.
    if not results and tb_error is not None:
        return map_tb_error(tb_error)

    return None


def find_linked_root_cause(
    results: list[dict], map_fn=None
) -> Exception | None:
    """Find the root cause status from a failed linked transfer chain.

    When TB rejects a linked batch, it marks dependent legs as
    LinkedEventFailed and puts the real cause on the failing leg.
    This scans results for the first non-LinkedEventFailed status.

    Args:
        results: List of result dicts from TB create call.
        map_fn: Optional custom status mapper.

    Returns:
        Domain error for the root cause, or None if all succeeded.
    """
    if map_fn is None:
        map_fn = map_tb_transfer_status

    has_failure = False
    for result in results:
        status = result.get("status")
        if status not in (0, 1):  # not success
            has_failure = True
            if status not in (81, 82):  # not LinkedEventFailed/ChainOpen
                return map_fn(status)

    # All were LinkedEventFailed (no success, no root cause found).
    if has_failure:
        return ErrLinkedEventFailed

    # All succeeded.
    return None


# --- Hold-specific status mapping ---

_HOLD_SPECIFIC_ERRORS: dict[int, Exception] = {
    60: ErrInsufficientBalance,       # TransferPendingTransferNotFound -> treat as not found
    61: ErrHoldAlreadyResolved,       # TransferPendingTransferNotPending
}


def map_hold_status(status: int) -> Exception | None:
    """Map TB status codes for hold operations (create/capture/void).

    Extends the generic transfer mapper with hold-specific mappings.

    Args:
        status: Integer status code from TB create result.

    Returns:
        None on success, or a domain error for failure statuses.
    """
    # Success codes
    if status in (_TRANSFER_CREATED, _TRANSFER_EXISTS):
        return None

    # Hold-specific mappings
    if status in _HOLD_SPECIFIC_ERRORS:
        return _HOLD_SPECIFIC_ERRORS[status]

    # Insufficient funds for hold creation
    if status in _INSUFFICIENT_BALANCE_STATUSES:
        return ErrInsufficientBalance

    # Account errors
    if status in _ACCOUNT_NOT_FOUND_STATUSES:
        return ErrInvalidAccount
    if status in _ACCOUNT_CLOSED_STATUSES:
        return ErrAccountClosed

    # Timeout reserved for pending transfers — capture/void without timeout
    if status == 74:  # TransferTimeoutReservedForPendingTransfer
        return ValidationError("timeout is reserved for pending transfers")

    # Fall through to generic mapper
    return map_tb_transfer_status(status)


def check_hold_result(
    results: list[dict], tb_error: Exception | None
) -> Exception | None:
    """Inspect TB create results for hold operations.

    Args:
        results: List of result dicts from TB create call.
        tb_error: Exception from the TB client (may be None).

    Returns:
        Domain error if a transfer failed, None on success.
    """
    return check_transfer_result(results, tb_error, map_hold_status)
