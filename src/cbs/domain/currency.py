"""Currency definitions — ISO 4217 codes, ledger mapping, and scale."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrencyInfo:
    """Immutable currency metadata.

    Attributes:
        code: ISO 4217 alpha-3 code (e.g., "USD").
        ledger: ISO 4217 numeric code, used as TigerBeetle ledger ID.
        scale: Number of decimal places (minor unit precision).
        name: Human-readable currency name.
    """

    code: str
    ledger: int
    scale: int
    name: str


# Ledger IDs use ISO 4217 numeric codes.
_LEDGER_USD = 840
_LEDGER_EUR = 978
_LEDGER_GBP = 826
_LEDGER_SGD = 702
_LEDGER_JPY = 392
_LEDGER_MYR = 458
_LEDGER_THB = 764
_LEDGER_IDR = 360
_LEDGER_AUD = 36
_LEDGER_CHF = 756

from cbs.domain.errors import ErrInvalidCurrency


# Supported currencies — ordered for deterministic iteration.
CURRENCIES: dict[str, CurrencyInfo] = {
    "USD": CurrencyInfo("USD", _LEDGER_USD, 2, "US Dollar"),
    "EUR": CurrencyInfo("EUR", _LEDGER_EUR, 2, "Euro"),
    "GBP": CurrencyInfo("GBP", _LEDGER_GBP, 2, "British Pound"),
    "SGD": CurrencyInfo("SGD", _LEDGER_SGD, 2, "Singapore Dollar"),
    "MYR": CurrencyInfo("MYR", _LEDGER_MYR, 2, "Malaysian Ringgit"),
    "JPY": CurrencyInfo("JPY", _LEDGER_JPY, 0, "Japanese Yen"),
    "THB": CurrencyInfo("THB", _LEDGER_THB, 2, "Thai Baht"),
    "IDR": CurrencyInfo("IDR", _LEDGER_IDR, 2, "Indonesian Rupiah"),
    "AUD": CurrencyInfo("AUD", _LEDGER_AUD, 2, "Australian Dollar"),
    "CHF": CurrencyInfo("CHF", _LEDGER_CHF, 2, "Swiss Franc"),
}


def lookup_currency(code: str) -> CurrencyInfo:
    """Return the CurrencyInfo for an ISO 4217 alpha code.

    Raises:
        ValueError: If the currency code is not supported.
    """
    info = CURRENCIES.get(code)
    if info is None:
        raise ValueError(f"unsupported currency: {code}") from ErrInvalidCurrency
    return info


def ledger_to_currency(ledger: int) -> str | None:
    """Map a TigerBeetle ledger number to an ISO 4217 currency code.

    Returns:
        The currency code, or None if no match.
    """
    for info in CURRENCIES.values():
        if info.ledger == ledger:
            return info.code
    return None


def currency_scale_from_ledger(ledger: int) -> int:
    """Return the scale (decimal places) for a given ledger number.

    Returns:
        The scale value, or 0 if no match.
    """
    for info in CURRENCIES.values():
        if info.ledger == ledger:
            return info.scale
    return 0


def supported_currencies() -> list[str]:
    """Return all supported currency codes in deterministic order."""
    return list(CURRENCIES.keys())
