"""Tests for FX rate cache — TTL, stampede protection, and purge."""

import asyncio
from datetime import datetime, timezone

import pytest

from cbs.cache.fx_cache import FXCache


class MockFXRate:
    """Mock FX rate for testing."""

    def __init__(self, sell_currency: str, buy_currency: str, rate: float):
        self.sell_currency = sell_currency
        self.buy_currency = buy_currency
        self.rate = rate
        self.effective_at = datetime.now(timezone.utc)


class TestFXCacheGetSet:
    """Test basic get/set operations."""

    def test_set_and_get(self):
        cache = FXCache(default_ttl=60)
        now = datetime.now(timezone.utc)

        cache.set("USD", "EUR", 0.92, now)
        result = cache.get("USD", "EUR")

        assert result is not None
        assert result["rate"] == 0.92
        assert result["effective_at"] == now.isoformat()

    def test_get_missing_returns_none(self):
        cache = FXCache(default_ttl=60)
        result = cache.get("USD", "EUR")

        assert result is None

    def test_set_with_string_effective_at(self):
        cache = FXCache(default_ttl=60)

        cache.set("USD", "EUR", 0.92, "2024-01-15T10:30:00Z")
        result = cache.get("USD", "EUR")

        assert result is not None
        assert result["effective_at"] == "2024-01-15T10:30:00Z"

    def test_set_overwrites_existing(self):
        cache = FXCache(default_ttl=60)

        cache.set("USD", "EUR", 0.92, datetime.now(timezone.utc))
        cache.set("USD", "EUR", 0.95, datetime.now(timezone.utc))
        result = cache.get("USD", "EUR")

        assert result["rate"] == 0.95


class TestFXCacheExpiry:
    """Test TTL-based expiry."""

    @pytest.mark.asyncio
    async def test_entry_expires_after_ttl(self):
        """Entry expires after TTL passes."""
        cache = FXCache(default_ttl=0)  # Clamped to _MIN_TTL (1s).

        cache.set("USD", "EUR", 0.92, datetime.now(timezone.utc))

        # Entry should be valid immediately (minimum TTL enforced).
        result = cache.get("USD", "EUR")
        assert result is not None

        # Wait for TTL to pass.
        await asyncio.sleep(1.1)

        result = cache.get("USD", "EUR")
        assert result is None  # Now expired.

    def test_set_with_ttl_override(self):
        cache = FXCache(default_ttl=60)

        cache.set_with_ttl("USD", "EUR", 0.92, datetime.now(timezone.utc), ttl=1)
        result = cache.get("USD", "EUR")

        assert result is not None
        assert result["rate"] == 0.92


class TestFXCacheDelete:
    """Test cache deletion."""

    def test_delete_existing(self):
        cache = FXCache(default_ttl=60)

        cache.set("USD", "EUR", 0.92, datetime.now(timezone.utc))
        cache.delete("USD", "EUR")

        assert cache.get("USD", "EUR") is None

    def test_delete_nonexistent(self):
        cache = FXCache(default_ttl=60)

        # Should not raise.
        cache.delete("USD", "EUR")


class TestFXCachePurge:
    """Test periodic purge of expired entries."""

    @pytest.mark.asyncio
    async def test_purge_removes_expired(self):
        cache = FXCache(default_ttl=0)  # Clamped to _MIN_TTL (1s).

        cache.set("USD", "EUR", 0.92, datetime.now(timezone.utc))
        cache.set("USD", "GBP", 0.79, datetime.now(timezone.utc))
        cache.set_with_ttl("EUR", "GBP", 0.86, datetime.now(timezone.utc), ttl=3600)

        # Wait for TTL to pass.
        await asyncio.sleep(1.1)

        purged = cache.purge()

        assert purged == 2
        assert cache.get("USD", "EUR") is None
        assert cache.get("USD", "GBP") is None
        # EUR/GBP should still be cached (3600s TTL).
        assert cache.get("EUR", "GBP") is not None

    def test_purge_returns_zero_when_no_expired(self):
        cache = FXCache(default_ttl=3600)

        cache.set("USD", "EUR", 0.92, datetime.now(timezone.utc))
        purged = cache.purge()

        assert purged == 0


class TestFXCacheClear:
    """Test cache clear."""

    def test_clear_removes_all(self):
        cache = FXCache(default_ttl=3600)

        cache.set("USD", "EUR", 0.92, datetime.now(timezone.utc))
        cache.set("USD", "GBP", 0.79, datetime.now(timezone.utc))
        cache.clear()

        assert cache.get("USD", "EUR") is None
        assert cache.get("USD", "GBP") is None


class TestFXCacheGetOrRefresh:
    """Test async get_or_refresh with stampede protection."""

    @pytest.mark.asyncio
    async def test_get_or_refresh_cache_hit(self):
        cache = FXCache(default_ttl=3600)
        now = datetime.now(timezone.utc)

        cache.set("USD", "EUR", 0.92, now)
        call_count = 0

        async def loader(sell: str, buy: str) -> MockFXRate:
            nonlocal call_count
            call_count += 1
            return MockFXRate(sell, buy, 0.93)

        result = await cache.get_or_refresh("USD", "EUR", loader)

        assert result["rate"] == 0.92
        assert call_count == 0  # Loader not called on cache hit.

    @pytest.mark.asyncio
    async def test_get_or_refresh_cache_miss(self):
        cache = FXCache(default_ttl=3600)
        call_count = 0

        async def loader(sell: str, buy: str) -> MockFXRate:
            nonlocal call_count
            call_count += 1
            return MockFXRate(sell, buy, 0.92)

        result = await cache.get_or_refresh("USD", "EUR", loader)

        assert result["rate"] == 0.92
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_stampede_protection(self):
        """Concurrent misses should only call loader once."""
        cache = FXCache(default_ttl=0)  # Instant expiry.
        call_count = 0

        async def loader(sell: str, buy: str) -> MockFXRate:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # Simulate slow DB query.
            return MockFXRate(sell, buy, 0.92)

        # Launch 10 concurrent requests for the same key.
        tasks = [cache.get_or_refresh("USD", "EUR", loader) for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should return the same result.
        assert all(r["rate"] == 0.92 for r in results)
        # Loader should be called exactly once (stampede protection).
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_keys_load_independently(self):
        """Different keys should load independently."""
        cache = FXCache(default_ttl=3600)
        call_count = 0

        async def loader(sell: str, buy: str) -> MockFXRate:
            nonlocal call_count
            call_count += 1
            return MockFXRate(sell, buy, 0.92)

        results = await asyncio.gather(
            cache.get_or_refresh("USD", "EUR", loader),
            cache.get_or_refresh("USD", "GBP", loader),
        )

        assert call_count == 2  # Each key loaded once.
