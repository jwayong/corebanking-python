"""Account domain model — codes, balance computation, and request/response types."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import msgspec

from cbs.domain.errors import ValidationError
from cbs.domain.loans import LoanDetails, LoanRequest


# --- Account Code Enum ---

class AccountCode(enum.IntEnum):
    """Chart of accounts — mirrors the Go version exactly.

    Ranges:
        1000–1999: Assets (debit balance)
        2000–2999: Liabilities (credit balance)
        3000–3999: Equity
        4000–4999: Income
        5000–5999: Expenses (debit balance)
        6000–6999: Suspense / Clearing
    """

    # Assets (debit balance)
    CASH_VAULT = 1101
    CENTRAL_BANK_RESERVE = 1110
    CORRESPONDENT_NOSTRO = 1120
    SETTLEMENT_ACCOUNT = 1201
    SUSPENSE_ASSET = 1301
    LOAN_PERSONAL = 1401
    LOAN_MORTGAGE = 1410
    LOAN_AUTO = 1420
    LOAN_CREDIT_CARD = 1430
    LOAN_OVERDRAFT = 1440
    ACCRUED_INTEREST_RECEIVABLE = 1501
    LIQUIDITY_POOL = 1601

    # Liabilities (credit balance)
    DEPOSIT_CURRENT = 2101
    DEPOSIT_SAVINGS = 2110
    DEPOSIT_FIXED = 2120
    DEPOSIT_ESCROW = 2130
    ACCRUED_INTEREST_PAYABLE = 2201
    PAYABLE_CUSTOMER = 2301

    # Equity
    SHARE_CAPITAL = 3101
    RETAINED_EARNINGS = 3110
    GENERAL_RESERVE = 3120
    CURRENT_YEAR_PL = 3130

    # Income (credit balance)
    INC_INTEREST_LOAN = 4101
    INC_FEE_SERVICE = 4110
    INC_FEE_ACCOUNT = 4111
    INC_FEE_TRANSACTION = 4112
    INC_FX_GAIN = 4120
    INC_PENALTY = 4130

    # Expenses (debit balance)
    EXP_INTEREST_DEP = 5101
    EXP_OPERATIONS = 5110
    EXP_FX_LOSS = 5120
    EXP_LOAN_WRITE_OFF = 5130

    # Suspense / Clearing
    SUSPENSE_TXN = 6101
    CLEARING_OUTBOUND = 6201
    CLEARING_INBOUND = 6202


def is_debit_balance(code: int) -> bool:
    """Return True for asset/expense account codes (1000–1999, 5000–5999)."""
    return (1000 <= code < 2000) or (5000 <= code < 6000)


@dataclass
class ComputeBalanceResult:
    """Computed balance figures derived from TigerBeetle cumulative fields.

    Attributes:
        posted: Posted balance (settled transactions only).
        pending: Pending balance (posted - available; always non-negative for constrained accounts).
        available: Available balance (posted + pending movements).
    """

    posted: int
    pending: int
    available: int


def compute_balance(
    debits_posted: int,
    credits_posted: int,
    debits_pending: int,
    credits_pending: int,
    code: int,
) -> ComputeBalanceResult:
    """Derive human-readable balances from TigerBeetle cumulative fields.

    For debit-balance accounts (assets, expenses):
        posted = debits_posted - credits_posted
        available = posted + debits_pending - credits_pending

    For credit-balance accounts (liabilities, equity, income):
        posted = credits_posted - debits_posted
        available = posted - debits_pending + credits_pending

    Pending is always: posted - available
    """
    if is_debit_balance(code):
        posted = debits_posted - credits_posted
        available = posted + debits_pending - credits_pending
    else:
        posted = credits_posted - debits_posted
        available = posted - debits_pending + credits_pending

    return ComputeBalanceResult(
        posted=posted,
        pending=posted - available,
        available=available,
    )


# --- Request/Response Models (msgspec Structs) ---

class Balance(msgspec.Struct, frozen=True):
    """Monetary amount with currency info."""

    amount: int
    currency: str
    scale: int


class AccountOwner(msgspec.Struct, frozen=True):
    """Account ownership info."""

    customer_ref: str
    name: str
    ownership_type: str
    role: str


class CreateAccountRequest(msgspec.Struct):
    """Input for creating a deposit or loan account."""

    customer_ref: str
    product_code: str
    ownership_type: str = "sole"
    loan: LoanRequest | None = None

    def validate(self) -> None:
        """Validate the request fields."""
        if not self.customer_ref:
            raise ValidationError("customer_ref is required")
        if not _is_valid_uuid(self.customer_ref):
            raise ValidationError("customer_ref must be a valid UUID")
        if not self.product_code:
            raise ValidationError("product_code is required")
        if not self.ownership_type:
            raise ValidationError("ownership_type is required")
        if self.ownership_type not in ("sole", "joint"):
            raise ValidationError("ownership_type must be 'sole' or 'joint'")
        if self.loan is not None:
            self.loan.validate()


class AccountResponse(msgspec.Struct, frozen=True):
    """API response for an account."""

    id: str
    account_number: str
    product_code: str
    category: str
    currency: str
    scale: int
    status: str
    balance: Balance
    available_balance: Balance
    owners: list[AccountOwner]
    opened_at: datetime
    loan_details: LoanDetails | None = None


class AccountSummary(msgspec.Struct, frozen=True):
    """Compact account representation for listing endpoints."""

    id: str
    account_number: str
    product_code: str
    category: str
    currency: str
    scale: int
    status: str
    balance: Balance
    available_balance: Balance
    opened_at: str


class AccountListResponse(msgspec.Struct, frozen=True):
    """Paginated response for account listing."""

    data: list[AccountSummary]
    next_cursor: str = ""
    has_more: bool = False


class CloseAccountResponse(msgspec.Struct, frozen=True):
    """API response for a closed account."""

    id: str
    status: str
    closed_at: datetime


class CreateAccountResponse(msgspec.Struct, frozen=True):
    """API response for a newly created account."""

    id: str
    account_number: str
    product_code: str
    category: str
    currency: str
    scale: int
    status: str
    opened_at: datetime


class Account(msgspec.Struct, frozen=True):
    """Internal account representation."""

    id: str
    tb_account_id: bytes
    account_number: str
    product_id: int
    product_code: str
    category: str
    currency: str
    scale: int
    status: str
    opened_at: datetime
    closed_at: datetime | None = None


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
