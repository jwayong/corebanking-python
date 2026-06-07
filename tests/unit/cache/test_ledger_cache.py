"""Tests for ledger cache — immutable currency/ledger mappings."""

import pytest

from cbs.cache.ledger_cache import LedgerCache
from cbs.domain.currency import CURRENCIES


class TestLedgerCacheInit:
    """Test ledger cache initialization."""

    def test_loads_all_currencies(self):
        cache = LedgerCache()

        assert len(cache.currencies) == len(CURRENCIES)
        for code in CURRENCIES:
            assert cache.is_supported(code)

    def test_currencies_list_matches_domain(self):
        cache = LedgerCache()

        assert set(cache.currencies) == set(CURRENCIES.keys())


class TestLedgerCacheLedgerFor:
    """Test ledger_for lookups."""

    def test_usd_ledger(self):
        cache = LedgerCache()

        assert cache.ledger_for("USD") == 840

    def test_eur_ledger(self):
        cache = LedgerCache()

        assert cache.ledger_for("EUR") == 978

    def test_jpy_ledger(self):
        cache = LedgerCache()

        assert cache.ledger_for("JPY") == 392

    def test_unknown_currency_returns_none(self):
        cache = LedgerCache()

        assert cache.ledger_for("XYZ") is None


class TestLedgerCacheScaleFor:
    """Test scale_for lookups."""

    def test_usd_scale(self):
        cache = LedgerCache()

        assert cache.scale_for("USD") == 2

    def test_jpy_scale(self):
        """JPY has scale 0 (no decimal places)."""
        cache = LedgerCache()

        assert cache.scale_for("JPY") == 0

    def test_eur_scale(self):
        cache = LedgerCache()

        assert cache.scale_for("EUR") == 2

    def test_unknown_currency_returns_none(self):
        cache = LedgerCache()

        assert cache.scale_for("XYZ") is None


class TestLedgerCacheInfoFor:
    """Test info_for lookups."""

    def test_usd_info(self):
        cache = LedgerCache()

        info = cache.info_for("USD")
        assert info is not None
        assert info.ledger == 840
        assert info.scale == 2

    def test_unknown_currency_returns_none(self):
        cache = LedgerCache()

        assert cache.info_for("XYZ") is None


class TestLedgerCacheIsSupported:
    """Test is_supported checks."""

    def test_known_currency(self):
        cache = LedgerCache()

        assert cache.is_supported("USD") is True
        assert cache.is_supported("EUR") is True

    def test_unknown_currency(self):
        cache = LedgerCache()

        assert cache.is_supported("XYZ") is False


class TestLedgerCacheImmutability:
    """Test that cache is read-only after construction."""

    def test_no_mutation_methods(self):
        """Cache should not have set/delete/clear methods."""
        cache = LedgerCache()

        assert not hasattr(cache, "set")
        assert not hasattr(cache, "delete")
        assert not hasattr(cache, "clear")

    def test_lookups_are_consistent(self):
        """Multiple lookups should return the same values."""
        cache = LedgerCache()

        for _ in range(100):
            assert cache.ledger_for("USD") == 840
            assert cache.scale_for("JPY") == 0
