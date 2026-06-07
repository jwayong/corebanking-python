"""Product cache — TTL-based in-memory caching with stampede protection.

Mirrors corebanking/internal/cache/product_cache.go (placeholder in Go,
fully implemented here for the Python port).
"""

from __future__ import annotations

# mypy: disable-error-code="no-untyped-def"

import asyncio
import time
from dataclasses import dataclass


# Minimum TTL to ensure stampede protection works (prevents instant expiry
# from defeating per-key locking).
_MIN_TTL = 1.0


@dataclass(frozen=True)
class _CacheEntry:
    """Internal cache entry with expiry timestamp."""

    data: dict[str, object]
    expires_at: float  # monotonic epoch seconds


class ProductCache:
    """In-memory TTL cache for product definitions.

    Provides stampede protection via per-key locks — when a cache entry
    expires, only one caller will fetch from the backing store while others
    wait for the result.

    Args:
        default_ttl: Default time-to-live in seconds (default 300 = 5 minutes).
    """

    def __init__(self, default_ttl: int = 300) -> None:
        self._default_ttl = max(default_ttl, _MIN_TTL)
        self._products: dict[str, _CacheEntry] = {}
        # Per-key lock to prevent stampede on concurrent misses.
        self._locks: dict[str, asyncio.Lock] = {}

    def _key_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for the given cache key."""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def get(self, code: str) -> dict[str, object] | None:
        """Return cached product if it exists and is not expired.

        Returns a dict with product fields, or ``None`` on miss/expiry.
        """
        entry = self._products.get(code)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._products[code]
            return None
        return dict(entry.data)  # Return a copy to prevent mutation.

    async def get_or_refresh(self, code: str, loader) -> dict[str, object]:
        """Return cached product, or fetch from *loader* on miss.

        Uses per-key locking to prevent stampede: when a cache entry expires,
        only one caller executes *loader* while others wait for the result.

        Args:
            loader: Async callable ``(code) -> ProductRecord`` that fetches
                from the backing store (e.g., PostgreSQL).

        Returns:
            Dict with product fields.
        """
        # Fast path: check cache without lock.
        cached = self.get(code)
        if cached is not None:
            return cached

        # Slow path: acquire per-key lock, re-check, then load.
        async with self._key_lock(code):
            # Re-check inside lock (another caller may have refreshed).
            cached = self.get(code)
            if cached is not None:
                return cached

            # Cache miss — fetch from backing store.
            record = await loader(code)
            self.set(record)

            return {
                "id": record.id,
                "code": record.code,
                "name": record.name,
                "category": record.category,
                "tb_account_code": record.tb_account_code,
                "currency": record.currency,
                "tb_ledger": record.tb_ledger,
                "interest_rate": record.interest_rate,
                "is_active": record.is_active,
            }

    def set(self, record) -> None:
        """Cache a product record with the default TTL."""
        self._products[record.code] = _CacheEntry(
            data={
                "id": record.id,
                "code": record.code,
                "name": record.name,
                "category": record.category,
                "tb_account_code": record.tb_account_code,
                "currency": record.currency,
                "tb_ledger": record.tb_ledger,
                "interest_rate": record.interest_rate,
                "is_active": record.is_active,
            },
            expires_at=time.monotonic() + self._default_ttl,
        )

    def set_with_ttl(self, record, ttl: int) -> None:
        """Cache a product record with an explicit TTL override (minimum ``_MIN_TTL``)."""
        self._products[record.code] = _CacheEntry(
            data={
                "id": record.id,
                "code": record.code,
                "name": record.name,
                "category": record.category,
                "tb_account_code": record.tb_account_code,
                "currency": record.currency,
                "tb_ledger": record.tb_ledger,
                "interest_rate": record.interest_rate,
                "is_active": record.is_active,
            },
            expires_at=time.monotonic() + max(ttl, _MIN_TTL),
        )

    def delete(self, code: str) -> None:
        """Remove a cached product."""
        self._products.pop(code, None)

    def purge(self) -> int:
        """Remove all expired entries. Returns count of purged entries."""
        now = time.monotonic()
        expired_keys = [k for k, v in self._products.items() if now > v.expires_at]
        for key in expired_keys:
            del self._products[key]
        return len(expired_keys)

    def clear(self) -> None:
        """Remove all entries (for testing)."""
        self._products.clear()
