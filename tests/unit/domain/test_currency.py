"""Tests for currency lookup and ledger mapping."""

import pytest

from cbs.domain.currency import (
    CURRENCIES,
    currency_scale_from_ledger,
    ledger_to_currency,
    lookup_currency,
    supported_currencies,
)


class TestCurrencyCount:
    def test_ten_currencies(self):
        """Verify all 10 currencies are defined."""
        assert len(CURRENCIES) == 10

    def test_supported_currencies_list(self):
        """Verify supported_currencies returns all codes."""
        codes = supported_currencies()
        assert len(codes) == 10
        assert "USD" in codes
        assert "EUR" in codes
        assert "JPY" in codes


class TestLookupCurrency:
    def test_lookup_usd(self):
        usd = lookup_currency("USD")
        assert usd.code == "USD"
        assert usd.ledger == 840
        assert usd.scale == 2
        assert usd.name == "US Dollar"

    def test_lookup_eur(self):
        eur = lookup_currency("EUR")
        assert eur.code == "EUR"
        assert eur.ledger == 978
        assert eur.scale == 2

    def test_lookup_jpy_zero_scale(self):
        """JPY has 0 decimal places."""
        jpy = lookup_currency("JPY")
        assert jpy.scale == 0

    def test_lookup_all_currencies(self):
        """Verify all currencies can be looked up."""
        expected = {
            "USD": (840, 2),
            "EUR": (978, 2),
            "GBP": (826, 2),
            "SGD": (702, 2),
            "MYR": (458, 2),
            "JPY": (392, 0),
            "THB": (764, 2),
            "IDR": (360, 2),
            "AUD": (36, 2),
            "CHF": (756, 2),
        }
        for code, (ledger, scale) in expected.items():
            info = lookup_currency(code)
            assert info.ledger == ledger
            assert info.scale == scale

    def test_lookup_unsupported_raises(self):
        """Unsupported currency should raise ValueError."""
        with pytest.raises(ValueError, match="unsupported currency"):
            lookup_currency("XYZ")

    def test_lookup_empty_string_raises(self):
        with pytest.raises(ValueError):
            lookup_currency("")


class TestLedgerToCurrency:
    def test_ledger_to_usd(self):
        assert ledger_to_currency(840) == "USD"

    def test_ledger_to_eur(self):
        assert ledger_to_currency(978) == "EUR"

    def test_ledger_to_jpy(self):
        assert ledger_to_currency(392) == "JPY"

    def test_ledger_to_all(self):
        """Verify all ledgers map back to their currency."""
        for code, info in CURRENCIES.items():
            assert ledger_to_currency(info.ledger) == code

    def test_unknown_ledger_returns_none(self):
        assert ledger_to_currency(9999) is None


class TestCurrencyScaleFromLedger:
    def test_usd_scale(self):
        assert currency_scale_from_ledger(840) == 2

    def test_jpy_scale(self):
        assert currency_scale_from_ledger(392) == 0

    def test_unknown_ledger_returns_zero(self):
        assert currency_scale_from_ledger(9999) == 0


class TestCurrencyInfoImmutability:
    def test_frozen_dataclass(self):
        """CurrencyInfo should be immutable."""
        from dataclasses import FrozenInstanceError

        info = lookup_currency("USD")
        with pytest.raises(FrozenInstanceError):
            info.code = "EUR"  # type: ignore[frozen-instantiation]
