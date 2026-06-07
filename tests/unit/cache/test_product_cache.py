"""Tests for product cache — TTL, stampede protection."""

import asyncio

import pytest

from cbs.cache.product_cache import ProductCache


class MockProductRecord:
    """Mock product record for testing."""

    def __init__(self, code: str):
        self.id = 1
        self.code = code
        self.name = f"Product {code}"
        self.category = "deposit"
        self.tb_account_code = 2001
        self.currency = "USD"
        self.tb_ledger = 840
        self.interest_rate = 0.05
        self.is_active = True


class TestProductCacheGetSet:
    """Test basic get/set operations."""

    def test_set_and_get(self):
        cache = ProductCache(default_ttl=60)

        record = MockProductRecord("SAVINGS")
        cache.set(record)
        result = cache.get("SAVINGS")

        assert result is not None
        assert result["code"] == "SAVINGS"
        assert result["name"] == "Product SAVINGS"
        assert result["category"] == "deposit"

    def test_get_missing_returns_none(self):
        cache = ProductCache(default_ttl=60)
        result = cache.get("SAVINGS")

        assert result is None

    def test_get_returns_copy(self):
        """Ensure get returns a copy, not the internal dict."""
        cache = ProductCache(default_ttl=60)

        record = MockProductRecord("SAVINGS")
        cache.set(record)
        result1 = cache.get("SAVINGS")
        result2 = cache.get("SAVINGS")

        assert result1 is not result2  # Different dict objects.
        assert result1 == result2

    def test_set_overwrites_existing(self):
        cache = ProductCache(default_ttl=60)

        record1 = MockProductRecord("SAVINGS")
        cache.set(record1)

        record2 = MockProductRecord("SAVINGS")
        record2.name = "Updated Savings"
        cache.set(record2)

        result = cache.get("SAVINGS")
        assert result["name"] == "Updated Savings"


class TestProductCacheExpiry:
    """Test TTL-based expiry."""

    @pytest.mark.asyncio
    async def test_entry_expires_after_ttl(self):
        """Entry expires after TTL passes."""
        cache = ProductCache(default_ttl=0)  # Clamped to _MIN_TTL (1s).

        record = MockProductRecord("SAVINGS")
        cache.set(record)

        # Entry should be valid immediately (minimum TTL enforced).
        assert cache.get("SAVINGS") is not None

        # Wait for TTL to pass.
        await asyncio.sleep(1.1)

        assert cache.get("SAVINGS") is None  # Now expired.

    def test_set_with_ttl_override(self):
        cache = ProductCache(default_ttl=60)

        record = MockProductRecord("SAVINGS")
        cache.set_with_ttl(record, ttl=1)

        result = cache.get("SAVINGS")
        assert result is not None


class TestProductCacheDelete:
    """Test cache deletion."""

    def test_delete_existing(self):
        cache = ProductCache(default_ttl=60)

        record = MockProductRecord("SAVINGS")
        cache.set(record)
        cache.delete("SAVINGS")

        assert cache.get("SAVINGS") is None

    def test_delete_nonexistent(self):
        cache = ProductCache(default_ttl=60)

        # Should not raise.
        cache.delete("SAVINGS")


class TestProductCachePurge:
    """Test periodic purge of expired entries."""

    @pytest.mark.asyncio
    async def test_purge_removes_expired(self):
        cache = ProductCache(default_ttl=0)  # Clamped to _MIN_TTL (1s).

        record1 = MockProductRecord("SAVINGS")
        cache.set(record1)

        record2 = MockProductRecord("LOANS")
        cache.set_with_ttl(record2, ttl=3600)

        # Wait for TTL to pass.
        await asyncio.sleep(1.1)

        purged = cache.purge()

        assert purged == 1
        assert cache.get("SAVINGS") is None
        assert cache.get("LOANS") is not None

    def test_purge_returns_zero_when_no_expired(self):
        cache = ProductCache(default_ttl=3600)

        record = MockProductRecord("SAVINGS")
        cache.set(record)
        purged = cache.purge()

        assert purged == 0


class TestProductCacheClear:
    """Test cache clear."""

    def test_clear_removes_all(self):
        cache = ProductCache(default_ttl=3600)

        record1 = MockProductRecord("SAVINGS")
        cache.set(record1)

        record2 = MockProductRecord("LOANS")
        cache.set(record2)

        cache.clear()

        assert cache.get("SAVINGS") is None
        assert cache.get("LOANS") is None


class TestProductCacheGetOrRefresh:
    """Test async get_or_refresh with stampede protection."""

    @pytest.mark.asyncio
    async def test_get_or_refresh_cache_hit(self):
        cache = ProductCache(default_ttl=3600)

        record = MockProductRecord("SAVINGS")
        cache.set(record)

        call_count = 0

        async def loader(code: str) -> MockProductRecord:
            nonlocal call_count
            call_count += 1
            return MockProductRecord(code)

        result = await cache.get_or_refresh("SAVINGS", loader)

        assert result["code"] == "SAVINGS"
        assert call_count == 0  # Loader not called on cache hit.

    @pytest.mark.asyncio
    async def test_get_or_refresh_cache_miss(self):
        cache = ProductCache(default_ttl=3600)
        call_count = 0

        async def loader(code: str) -> MockProductRecord:
            nonlocal call_count
            call_count += 1
            return MockProductRecord(code)

        result = await cache.get_or_refresh("SAVINGS", loader)

        assert result["code"] == "SAVINGS"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_stampede_protection(self):
        """Concurrent misses should only call loader once."""
        cache = ProductCache(default_ttl=0)  # Instant expiry.
        call_count = 0

        async def loader(code: str) -> MockProductRecord:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # Simulate slow DB query.
            return MockProductRecord(code)

        # Launch 10 concurrent requests for the same key.
        tasks = [cache.get_or_refresh("SAVINGS", loader) for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should return the same result.
        assert all(r["code"] == "SAVINGS" for r in results)
        # Loader should be called exactly once (stampede protection).
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_keys_load_independently(self):
        """Different keys should load independently."""
        cache = ProductCache(default_ttl=3600)
        call_count = 0

        async def loader(code: str) -> MockProductRecord:
            nonlocal call_count
            call_count += 1
            return MockProductRecord(code)

        results = await asyncio.gather(
            cache.get_or_refresh("SAVINGS", loader),
            cache.get_or_refresh("LOANS", loader),
        )

        assert call_count == 2  # Each key loaded once.
