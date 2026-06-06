"""Transfer domain model — codes, request/response types for transfers, FX, and holds."""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Final

import msgspec

from cbs.domain.accounts import Balance
from cbs.domain.currency import lookup_currency
from cbs.domain.errors import ErrInvalidAmount, ErrInvalidCurrency, ValidationError

# --- Transfer Code Enum ---

class TransferCode(enum.IntEnum):
    """TigerBeetle transfer codes — stored in Transfer.code field (uint16).

    These match the registry defined in DOMAIN-RETAIL.md §6.
    """

    DEPOSIT = 1
    WITHDRAWAL = 2
    TRANSFER = 3
    FX_DEBIT = 4
    FX_CREDIT = 5
    PAYMENT_OUT = 6
    PAYMENT_IN = 7
    HOLD = 8
    CAPTURE = 9
    VOID = 10
    FEE = 11
    INTEREST_CREDIT = 12
    INTEREST_DEBIT = 13
    CORRECTION = 14
    SETTLEMENT = 15
    LOAN_DISBURSEMENT = 16
    LOAN_REPAYMENT = 17
    WRITE_OFF = 18
    PENALTY = 19
    INTEREST_CAPITALISATION = 20


# Mapping from string transfer_type to TransferCode
_TRANSFER_TYPE_MAP: dict[str, TransferCode] = {
    "deposit": TransferCode.DEPOSIT,
    "withdrawal": TransferCode.WITHDRAWAL,
    "transfer": TransferCode.TRANSFER,
    "payment_out": TransferCode.PAYMENT_OUT,
    "payment_in": TransferCode.PAYMENT_IN,
    "fee": TransferCode.FEE,
    "correction": TransferCode.CORRECTION,
    "settlement": TransferCode.SETTLEMENT,
    "disbursement": TransferCode.LOAN_DISBURSEMENT,
    "repayment": TransferCode.LOAN_REPAYMENT,
    "write_off": TransferCode.WRITE_OFF,
    "penalty": TransferCode.PENALTY,
}

# Reverse mapping: TransferCode -> string name
_TRANSFER_CODE_NAMES: dict[TransferCode, str] = {
    TransferCode.DEPOSIT: "deposit",
    TransferCode.WITHDRAWAL: "withdrawal",
    TransferCode.TRANSFER: "transfer",
    TransferCode.FX_DEBIT: "fx_debit",
    TransferCode.FX_CREDIT: "fx_credit",
    TransferCode.PAYMENT_OUT: "payment_out",
    TransferCode.PAYMENT_IN: "payment_in",
    TransferCode.HOLD: "hold",
    TransferCode.CAPTURE: "capture",
    TransferCode.VOID: "void",
    TransferCode.FEE: "fee",
    TransferCode.INTEREST_CREDIT: "interest_credit",
    TransferCode.INTEREST_DEBIT: "interest_debit",
    TransferCode.CORRECTION: "correction",
    TransferCode.SETTLEMENT: "settlement",
    TransferCode.LOAN_DISBURSEMENT: "disbursement",
    TransferCode.LOAN_REPAYMENT: "repayment",
    TransferCode.WRITE_OFF: "write_off",
    TransferCode.PENALTY: "penalty",
    TransferCode.INTEREST_CAPITALISATION: "interest_capitalisation",
}

# Valid transfer types for the standard transfer endpoint
_VALID_TRANSFER_TYPES: Final[set[str]] = {
    "deposit",
    "withdrawal",
    "transfer",
    "payment_out",
    "payment_in",
    "fee",
    "correction",
    "settlement",
}


def map_transfer_code(transfer_type: str) -> TransferCode:
    """Map a transfer_type string to the corresponding TigerBeetle transfer code.

    Raises:
        ValidationError: If the transfer_type is not supported.
    """
    code = _TRANSFER_TYPE_MAP.get(transfer_type)
    if code is None:
        raise ValidationError(f"unsupported transfer_type: {transfer_type}")
    return code


def transfer_code_to_string(code: int) -> str:
    """Map a TigerBeetle transfer code to its string representation."""
    try:
        tc = TransferCode(code)
    except ValueError:
        return f"unknown_{code}"
    return _TRANSFER_CODE_NAMES.get(tc, f"unknown_{code}")


# --- Transfer Request/Response ---

class TransferRequest(msgspec.Struct):
    """Input for executing a transfer.

    For 'transfer': both debit_account_id and credit_account_id are required.
    For 'deposit': customer_account_id or credit_account_id is sufficient (Cash Vault auto-resolved).
    For 'withdrawal': customer_account_id or debit_account_id is sufficient (Cash Vault auto-resolved).
    """

    transfer_type: str
    amount: int
    currency: str
    customer_account_id: str = ""
    debit_account_id: str = ""
    credit_account_id: str = ""
    reference: str = ""
    description: str = ""
    value_date: str = ""

    def validate(self) -> None:
        """Validate the transfer request fields."""
        if not self.transfer_type:
            raise ValidationError("transfer_type is required")
        if self.transfer_type not in _VALID_TRANSFER_TYPES:
            raise ValidationError(
                "invalid transfer_type: must be one of deposit, withdrawal, transfer, "
                "payment_out, payment_in, fee, correction, settlement"
            )

        match self.transfer_type:
            case "deposit":
                if not self.customer_account_id and not self.credit_account_id:
                    raise ValidationError("credit_account_id is required for deposit")
                if self.customer_account_id and not _is_valid_uuid(self.customer_account_id):
                    raise ValidationError("customer_account_id must be a valid UUID")
                if self.credit_account_id and not _is_valid_uuid(self.credit_account_id):
                    raise ValidationError("credit_account_id must be a valid UUID")
            case "withdrawal":
                if not self.customer_account_id and not self.debit_account_id:
                    raise ValidationError("debit_account_id is required for withdrawal")
                if self.customer_account_id and not _is_valid_uuid(self.customer_account_id):
                    raise ValidationError("customer_account_id must be a valid UUID")
                if self.debit_account_id and not _is_valid_uuid(self.debit_account_id):
                    raise ValidationError("debit_account_id must be a valid UUID")
            case "transfer":
                if not self.debit_account_id:
                    raise ValidationError("debit_account_id is required for transfer")
                if not _is_valid_uuid(self.debit_account_id):
                    raise ValidationError("debit_account_id must be a valid UUID")
                if not self.credit_account_id:
                    raise ValidationError("credit_account_id is required for transfer")
                if not _is_valid_uuid(self.credit_account_id):
                    raise ValidationError("credit_account_id must be a valid UUID")
                if self.debit_account_id == self.credit_account_id:
                    raise ValidationError("debit and credit accounts cannot be the same")
            case _:
                if not self.debit_account_id:
                    raise ValidationError("debit_account_id is required")
                if not _is_valid_uuid(self.debit_account_id):
                    raise ValidationError("debit_account_id must be a valid UUID")
                if not self.credit_account_id:
                    raise ValidationError("credit_account_id is required")
                if not _is_valid_uuid(self.credit_account_id):
                    raise ValidationError("credit_account_id must be a valid UUID")

        if self.amount <= 0:
            raise ValidationError("amount must be positive") from ErrInvalidAmount
        if not self.currency:
            raise ValidationError("currency is required") from ErrInvalidCurrency

        if self.value_date:
            _validate_date_format(self.value_date)


class TransferResponse(msgspec.Struct, frozen=True):
    """API response for a completed transfer."""

    id: str
    transfer_type: str
    debit_account_id: str
    credit_account_id: str
    amount: Balance
    value_date: str
    status: str
    created_at: datetime
    idempotency_key: str = ""
    reference: str = ""
    description: str = ""


class TransferAccountInfo(msgspec.Struct, frozen=True):
    """Account-level detail for a transfer leg."""

    account_number: str
    account_holder: str = ""


class TransferDetailResponse(msgspec.Struct, frozen=True):
    """Enriched API response for a transfer with account details."""

    id: str
    transfer_type: str
    debit_account_id: str
    credit_account_id: str
    amount: Balance
    value_date: str
    status: str
    created_at: datetime
    idempotency_key: str = ""
    description: str = ""
    reference: str = ""
    counterparty: str = ""
    debit_account: TransferAccountInfo | None = None
    credit_account: TransferAccountInfo | None = None
    direction: str = ""  # "incoming", "outgoing", or empty


# --- Fee Charge Types ---

class FeeChargeRequest(msgspec.Struct):
    """Input for charging a fee to a customer account."""

    customer_account_id: str
    amount: int
    currency: str
    description: str
    fee_schedule_ref: str = ""
    value_date: str = ""

    def validate(self) -> None:
        """Validate the fee charge request."""
        if not self.customer_account_id:
            raise ValidationError("customer_account_id is required")
        if not _is_valid_uuid(self.customer_account_id):
            raise ValidationError("customer_account_id must be a valid UUID")
        if self.amount <= 0:
            raise ValidationError("amount must be positive") from ErrInvalidAmount
        if not self.currency:
            raise ValidationError("currency is required") from ErrInvalidCurrency
        try:
            lookup_currency(self.currency)
        except ValueError as e:
            raise ValidationError(str(e)) from ErrInvalidCurrency
        if not self.description:
            raise ValidationError("description is required")
        if self.value_date:
            _validate_date_format(self.value_date)


class FeeChargeResponse(msgspec.Struct, frozen=True):
    """API response for a completed fee charge."""

    id: str
    transfer_type: str  # "fee"
    debit_account_id: str
    credit_account_id: str  # Fee Income system account
    amount: Balance
    description: str
    value_date: str
    status: str  # "posted"
    created_at: datetime
    idempotency_key: str = ""
    fee_schedule_ref: str = ""


# --- FX Transfer Types ---

class FXRequest(msgspec.Struct):
    """Input for executing an FX (cross-currency) transfer."""

    debit_account_id: str  # Customer account in sell currency
    credit_account_id: str  # Customer account in buy currency
    sell_amount: int
    sell_currency: str
    buy_currency: str
    rate: float
    reference: str = ""
    value_date: str = ""

    def validate(self) -> None:
        """Validate the FX request fields."""
        if not self.debit_account_id:
            raise ValidationError("debit_account_id is required")
        if not _is_valid_uuid(self.debit_account_id):
            raise ValidationError("debit_account_id must be a valid UUID")
        if not self.credit_account_id:
            raise ValidationError("credit_account_id is required")
        if not _is_valid_uuid(self.credit_account_id):
            raise ValidationError("credit_account_id must be a valid UUID")
        if self.debit_account_id == self.credit_account_id:
            raise ValidationError("debit and credit accounts cannot be the same")
        if self.sell_amount <= 0:
            raise ValidationError("sell_amount must be positive") from ErrInvalidAmount
        if not self.sell_currency:
            raise ValidationError("sell_currency is required") from ErrInvalidCurrency
        try:
            lookup_currency(self.sell_currency)
        except ValueError as e:
            raise ValidationError(str(e)) from ErrInvalidCurrency
        if not self.buy_currency:
            raise ValidationError("buy_currency is required")
        try:
            lookup_currency(self.buy_currency)
        except ValueError as e:
            raise ValidationError(f"unsupported buy_currency: {self.buy_currency}") from e
        if self.sell_currency == self.buy_currency:
            raise ValidationError("sell_currency and buy_currency must differ")
        if self.rate < 0:
            raise ValidationError("rate must be zero or positive")
        if self.value_date:
            _validate_date_format(self.value_date)


class FXLeg(msgspec.Struct, frozen=True):
    """One leg of an FX transfer (debit or credit)."""

    id: str
    debit_account_id: str
    credit_account_id: str
    amount: Balance
    code: str  # "fx_debit" or "fx_credit"


class FXResponse(msgspec.Struct, frozen=True):
    """API response for a completed FX transfer."""

    id: str  # Shared correlation ID
    transfer_type: str  # Always "fx"
    legs: list[FXLeg]
    rate: float
    sell_amount: Balance
    buy_amount: Balance
    value_date: str
    status: str  # "posted" or "failed"
    created_at: datetime
    idempotency_key: str = ""


class FXRate(msgspec.Struct, frozen=True):
    """Exchange rate record."""

    sell_currency: str  # Base currency (e.g., "USD")
    buy_currency: str   # Target currency (e.g., "EUR")
    rate: float         # 1 unit of sell = rate units of buy
    effective_at: datetime

    def pair_key(self) -> str:
        """Return a unique key for the currency pair."""
        return f"{self.sell_currency}/{self.buy_currency}"


# --- Hold Types (Two-Phase Transfers) ---

# Default timeout for a hold: 24 hours
DEFAULT_HOLD_TIMEOUT_SECONDS: Final[int] = 86_400

# Maximum allowed timeout for a hold: 7 days
MAX_HOLD_TIMEOUT_SECONDS: Final[int] = 604_800

# Hold status constants
HOLD_STATUS_PENDING: Final[str] = "pending"
HOLD_STATUS_CAPTURED: Final[str] = "captured"
HOLD_STATUS_VOIDED: Final[str] = "voided"


class HoldRequest(msgspec.Struct):
    """Input for creating a pending hold (two-phase transfer, phase 1)."""

    debit_account_id: str
    credit_account_id: str
    amount: int
    currency: str
    timeout_seconds: int = DEFAULT_HOLD_TIMEOUT_SECONDS
    reference: str = ""

    def validate(self) -> None:
        """Validate the hold request fields."""
        if not self.debit_account_id:
            raise ValidationError("debit_account_id is required")
        if not _is_valid_uuid(self.debit_account_id):
            raise ValidationError("debit_account_id must be a valid UUID")
        if not self.credit_account_id:
            raise ValidationError("credit_account_id is required")
        if not _is_valid_uuid(self.credit_account_id):
            raise ValidationError("credit_account_id must be a valid UUID")
        if self.debit_account_id == self.credit_account_id:
            raise ValidationError("debit and credit accounts cannot be the same")
        if self.amount <= 0:
            raise ValidationError("amount must be positive") from ErrInvalidAmount
        if not self.currency:
            raise ValidationError("currency is required") from ErrInvalidCurrency
        try:
            lookup_currency(self.currency)
        except ValueError as e:
            raise ValidationError(str(e)) from ErrInvalidCurrency
        if self.timeout_seconds > MAX_HOLD_TIMEOUT_SECONDS:
            raise ValidationError("timeout_seconds must not exceed 604800 (7 days)")


class CaptureRequest(msgspec.Struct):
    """Input for capturing a pending hold (two-phase transfer, phase 2a)."""

    amount: int = 0  # Optional: partial capture amount (must be <= hold amount)

    def validate(self) -> None:
        """Validate the capture request fields."""
        if self.amount < 0:
            raise ValidationError("amount must be zero or positive")


class HoldResponse(msgspec.Struct, frozen=True):
    """API response for a hold operation."""

    id: str
    transfer_type: str  # "hold", "capture", or "void"
    debit_account_id: str
    credit_account_id: str
    amount: Balance
    status: str  # "pending", "captured", or "voided"
    created_at: datetime
    idempotency_key: str = ""
    expires_at: datetime | None = None  # Only set for holds
    reference: str = ""


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
