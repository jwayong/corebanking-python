"""Domain exceptions — errors with HTTP status code mapping for API responses."""

from __future__ import annotations

from http import HTTPStatus


# Sentinel errors — mirror Go domain errors for errors.Is-style matching.
ErrNotFound = Exception("not found")
ErrAlreadyExists = Exception("already exists")
ErrInvalidRequest = Exception("invalid request")
ErrInvalidAmount = Exception("invalid amount")
ErrInvalidCurrency = Exception("invalid currency")
ErrInvalidAccount = Exception("invalid account")
ErrSameAccount = Exception("debit and credit accounts are the same")
ErrInsufficientBalance = Exception("insufficient balance")
ErrAccountClosed = Exception("account is closed")
ErrAccountFrozen = Exception("account is frozen")
ErrNonZeroBalance = Exception("account has non-zero balance")
ErrPendingHolds = Exception("account has pending holds")
ErrIdempotencyKeyExists = Exception("idempotency key exists")
ErrHoldExpired = Exception("hold expired")
ErrHoldAlreadyResolved = Exception("hold already resolved")
ErrProductInactive = Exception("product inactive")
ErrFXRateStale = Exception("fx rate stale")
ErrLoanNotActive = Exception("loan not active")
ErrRepaymentExceedsOutstanding = Exception("repayment exceeds outstanding balance")
ErrLiquidityPoolUnavailable = Exception("liquidity pool unavailable")
ErrServiceUnavailable = Exception("service unavailable")
ErrLinkedEventFailed = Exception("linked transfer event failed")
ErrIdempotencyConflict = Exception("idempotency key conflict")
ErrNotImplemented = Exception("not implemented")


class ValidationError(Exception):
    """Field-level validation error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class TransferError(Exception):
    """Carries TigerBeetle transfer failure context.

    Attributes:
        code: Machine-readable error code (e.g., "INSUFFICIENT_BALANCE").
        tb_status: Raw TigerBeetle status string.
        message: Human-readable message.
        details: Optional context (available balance, required amount, leg index).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        tb_status: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.tb_status = tb_status
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def __reduce__(self):
        return (type(self), (self.code, self.message), {"tb_status": self.tb_status, "details": self.details})

    def __getattr__(self, name: str) -> object:
        if name in self.details:
            return self.details[name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def is_target(self, target: Exception) -> bool:
        """Mirror Go's errors.Is behaviour for TransferError."""
        mapping = {
            "INSUFFICIENT_BALANCE": ErrInsufficientBalance,
            "INVALID_ACCOUNT": ErrInvalidAccount,
            "ACCOUNT_CLOSED": ErrAccountClosed,
        }
        return mapping.get(self.code) is target


# --- HTTP-status-aware domain exceptions ---

class DomainError(Exception):
    """Base class for domain exceptions that map to HTTP status codes."""

    status_code: HTTPStatus = HTTPStatus.BAD_REQUEST
    error_code: str = "DOMAIN_ERROR"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class NotFoundError(DomainError):
    """Resource not found — maps to 404."""

    status_code = HTTPStatus.NOT_FOUND
    error_code = "NOT_FOUND"


class AccountNotFoundError(NotFoundError):
    """Account not found in either TigerBeetle or PostgreSQL."""

    error_code = "ACCOUNT_NOT_FOUND"


class AccountClosedError(DomainError):
    """Account is closed — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "ACCOUNT_CLOSED"


class AccountFrozenError(DomainError):
    """Account is frozen — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "ACCOUNT_FROZEN"


class InsufficientBalanceError(TransferError):
    """Insufficient balance for the requested transfer — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "INSUFFICIENT_BALANCE"

    def __init__(  # type: ignore[override]
        self,
        message: str = "insufficient balance",
        *,
        available: int = 0,
        required: int = 0,
    ) -> None:
        details: dict[str, object] = {}
        if available:
            details["available"] = available
        if required:
            details["required"] = required
        super().__init__(
            code="INSUFFICIENT_BALANCE",
            message=message,
            details=details if details else None,
        )
        self.message = message


class IdempotencyConflictError(DomainError):
    """Idempotency key conflict — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "IDEMPOTENCY_CONFLICT"


class ProductInactiveError(DomainError):
    """Product is not active — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "PRODUCT_INACTIVE"


class HoldExpiredError(DomainError):
    """Hold has expired — maps to 410."""

    status_code = HTTPStatus.GONE
    error_code = "HOLD_EXPIRED"


class HoldAlreadyResolvedError(DomainError):
    """Hold already captured or voided — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "HOLD_ALREADY_RESOLVED"


class NonZeroBalanceError(DomainError):
    """Account has non-zero balance — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "NON_ZERO_BALANCE"


class PendingHoldsError(DomainError):
    """Account has pending holds — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "PENDING_HOLDS"


class LoanNotActiveError(DomainError):
    """Loan is not in active state — maps to 409."""

    status_code = HTTPStatus.CONFLICT
    error_code = "LOAN_NOT_ACTIVE"


class RepaymentExceedsOutstandingError(DomainError):
    """Repayment amount exceeds outstanding balance — maps to 400."""

    status_code = HTTPStatus.BAD_REQUEST
    error_code = "REPAYMENT_EXCEEDS_OUTSTANDING"


class FXRateStaleError(DomainError):
    """FX rate is stale — maps to 503."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    error_code = "FX_RATE_STALE"


class LiquidityPoolUnavailableError(DomainError):
    """Liquidity pool unavailable — maps to 503."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    error_code = "LIQUIDITY_POOL_UNAVAILABLE"


class ServiceUnavailableError(DomainError):
    """Downstream service unavailable — maps to 503."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    error_code = "SERVICE_UNAVAILABLE"
