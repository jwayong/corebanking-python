"""Loan domain model — lifecycle, EMI calculation, and request/response types."""

from __future__ import annotations

import math
from datetime import date, datetime

import msgspec

from cbs.domain.currency import lookup_currency
from cbs.domain.errors import (
    ErrInvalidAmount,
    ErrInvalidCurrency,
    ErrSameAccount,
    ValidationError,
)

# TYPE_CHECKING import to avoid circular dependency with accounts.py
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from cbs.domain.accounts import Balance

# Runtime alias for msgspec Struct fields (string annotations via __future__)
Balance: type = None  # type: ignore[misc,assignment]  # resolved at runtime by msgspec


class LoanRequest(msgspec.Struct):
    """Loan-specific fields for creating a loan account."""

    principal: int
    term_months: int

    def validate(self) -> None:
        """Validate the loan request fields."""
        if self.principal <= 0:
            raise ValidationError("loan.principal must be positive")
        if self.term_months <= 0:
            raise ValidationError("loan.term_months must be positive")


class LoanDetails(msgspec.Struct, frozen=True):
    """Loan-specific data for the API response."""

    principal: int
    outstanding: int
    interest_rate: float
    term_months: int
    maturity_date: str  # ISO date string YYYY-MM-DD
    next_payment_due: str  # ISO date string YYYY-MM-DD
    payment_amount: int
    status: str


def calculate_emi(principal: int, annual_rate: float, term_months: int) -> int:
    """Compute the Equated Monthly Instalment using standard amortisation formula.

    EMI = P × r × (1+r)^n / ((1+r)^n - 1)

    where P = principal, r = monthly interest rate, n = term in months.

    Returns the payment amount in the same minor-unit precision as principal.
    For zero or very small rates, uses simple division.
    """
    if principal <= 0 or term_months <= 0:
        return principal

    monthly_rate = annual_rate / 12.0

    # For zero or very small rates, use simple division
    if monthly_rate < 1e-10:
        return principal // term_months

    pow_val = math.pow(1 + monthly_rate, term_months)
    emi = float(principal) * monthly_rate * pow_val / (pow_val - 1)

    # Round to nearest integer (banker's rounding for amounts)
    return int(round(emi))


def calculate_maturity_date(from_date: date, term_months: int) -> date:
    """Return the maturity date given a start date and term in months."""
    # Use simple month addition (handles year rollover)
    year = from_date.year + (from_date.month + term_months - 1) // 12
    month = (from_date.month + term_months - 1) % 12 + 1
    # Clamp day to max days in target month
    import calendar  # noqa: PLC0414

    day = min(from_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def calculate_next_payment_due(from_date: date) -> date:
    """Return the date of the next monthly payment (1 month from start)."""
    return calculate_maturity_date(from_date, 1)


# --- Loan status constants ---

LOAN_STATUS_ACTIVE: str = "active"
LOAN_STATUS_CLOSED: str = "closed"
LOAN_STATUS_ARREARS: str = "arrears"
LOAN_STATUS_WRITTEN_OFF: str = "written_off"


class LoanDisbursementRequest(msgspec.Struct):
    """Input for disbursing a loan to a customer account."""

    loan_account_id: str
    credit_account_id: str  # Customer deposit account to receive funds
    amount: int = 0
    currency: str = ""
    reference: str = ""
    value_date: str = ""

    def validate(self) -> None:
        """Validate the disbursement request."""
        if not self.loan_account_id:
            raise ValidationError("loan_account_id is required")
        if not _is_valid_uuid(self.loan_account_id):
            raise ValidationError("loan_account_id must be a valid UUID")
        if not self.credit_account_id:
            raise ValidationError("credit_account_id is required")
        if not _is_valid_uuid(self.credit_account_id):
            raise ValidationError("credit_account_id must be a valid UUID")
        if self.loan_account_id == self.credit_account_id:
            raise ErrSameAccount
        if self.amount <= 0:
            raise ValidationError("amount must be positive") from ErrInvalidAmount
        if not self.currency:
            raise ValidationError("currency is required") from ErrInvalidCurrency
        try:
            lookup_currency(self.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from ErrInvalidCurrency
        if self.value_date:
            _validate_date_format(self.value_date)


class LoanRepaymentRequest(msgspec.Struct):
    """Input for repaying a loan."""

    debit_account_id: str  # Customer account to debit from
    loan_account_id: str
    amount: int
    currency: str = ""
    reference: str = ""
    value_date: str = ""

    def validate(self) -> None:
        """Validate the repayment request."""
        if not self.debit_account_id:
            raise ValidationError("debit_account_id is required")
        if not _is_valid_uuid(self.debit_account_id):
            raise ValidationError("debit_account_id must be a valid UUID")
        if not self.loan_account_id:
            raise ValidationError("loan_account_id is required")
        if not _is_valid_uuid(self.loan_account_id):
            raise ValidationError("loan_account_id must be a valid UUID")
        if self.debit_account_id == self.loan_account_id:
            raise ErrSameAccount
        if self.amount <= 0:
            raise ValidationError("amount must be positive") from ErrInvalidAmount
        if not self.currency:
            raise ValidationError("currency is required") from ErrInvalidCurrency
        try:
            lookup_currency(self.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from ErrInvalidCurrency
        if self.value_date:
            _validate_date_format(self.value_date)


class LoanDisbursementResponse(msgspec.Struct, frozen=True):
    """API response for a loan disbursement."""

    id: str
    transfer_type: str  # "disbursement"
    loan_account_id: str
    credit_account_id: str
    amount: Balance
    currency: str
    value_date: str  # YYYY-MM-DD
    status: str  # "posted"
    created_at: datetime


class LoanRepaymentResponse(msgspec.Struct, frozen=True):
    """API response for a loan repayment."""

    id: str
    transfer_type: str  # "repayment"
    debit_account_id: str
    loan_account_id: str
    amount: Balance
    currency: str
    value_date: str  # YYYY-MM-DD
    status: str  # "posted"
    created_at: datetime


class LoanRepaymentWithFeeRequest(msgspec.Struct):
    """Input for repaying a loan with interest and fee components.

    Executed as up to three linked TB transfers in one atomic batch:
    principal repayment, interest payment, and fee charge. Legs with zero
    amounts are omitted from the batch.
    """

    loan_account_id: str
    debit_account_id: str  # Customer deposit account (debit side)
    principal: int = 0
    interest_amount: int = 0
    fee_amount: int = 0
    currency: str = ""
    value_date: str = ""
    reference: str = ""

    def validate(self) -> None:
        """Validate the repay-with-fee request."""
        if not self.loan_account_id:
            raise ValidationError("loan_account_id is required")
        if not _is_valid_uuid(self.loan_account_id):
            raise ValidationError("loan_account_id must be a valid UUID")
        if not self.debit_account_id:
            raise ValidationError("debit_account_id is required")
        if not _is_valid_uuid(self.debit_account_id):
            raise ValidationError("debit_account_id must be a valid UUID")
        if self.loan_account_id == self.debit_account_id:
            raise ErrSameAccount
        if self.principal <= 0:
            raise ValidationError("principal must be positive") from ErrInvalidAmount
        if self.interest_amount < 0:
            raise ValidationError("interest_amount must be zero or positive")
        if self.fee_amount < 0:
            raise ValidationError("fee_amount must be zero or positive")
        if not self.currency:
            raise ValidationError("currency is required") from ErrInvalidCurrency
        try:
            lookup_currency(self.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from ErrInvalidCurrency
        if self.value_date:
            _validate_date_format(self.value_date)


class RepayWithFeeLeg(msgspec.Struct, frozen=True):
    """One leg of a repay-with-fee operation."""

    id: str
    debit_account_id: str
    credit_account_id: str
    amount: Balance
    code: str  # "repayment", "interest", or "fee"


class LoanRepaymentWithFeeResponse(msgspec.Struct, frozen=True, kw_only=True):
    """API response for a completed repay-with-fee operation."""

    id: str  # Shared correlation ID
    transfer_type: str  # "repay_with_fee"
    legs: list[RepayWithFeeLeg]
    loan_account_id: str
    debit_account_id: str
    principal: Balance | None = None
    interest: Balance | None = None
    fee: Balance | None = None
    currency: str = ""
    value_date: str  # YYYY-MM-DD
    status: str  # "posted"
    created_at: datetime


# --- Helpers ---

def _is_valid_uuid(s: str) -> bool:
    """Check that s is a valid UUID format (hex + dash positions)."""
    if len(s) != 36:
        return False
    for i, c in enumerate(s):
        if i in (8, 13, 18, 23):
            if c != "-":
                return False
            continue
        if not ((c >= "0" and c <= "9") or (c >= "a" and c <= "f") or (c >= "A" and c <= "F")):
            return False
    return True


def _validate_date_format(date_str: str) -> None:
    """Validate that a date string is in YYYY-MM-DD format."""
    try:
        date.fromisoformat(date_str)
    except ValueError:
        raise ValidationError("value_date must be in YYYY-MM-DD format")
