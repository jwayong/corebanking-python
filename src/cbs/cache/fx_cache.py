"""FX rate cache — TTL-based in-memory caching with stampede protection.

Mirrors corebanking/internal/cache/fx_cache.go.
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

    rate: float
    effective_at: str  # ISO format datetime string
    expires_at: float   # monotonic epoch seconds


class FXCache:
    """In-memory TTL cache for FX exchange rates.

    Provides stampede protection via per-key locks — when a cache entry
    expires, only one caller will fetch from the backing store while others
    wait for the result.

    Args:
        default_ttl: Default time-to-live in seconds (default 30).
    """

    def __init__(self, default_ttl: int = 30) -> None:
        self._default_ttl = max(default_ttl, _MIN_TTL)
        self._rates: dict[str, _CacheEntry] = {}
        # Per-key lock to prevent stampede on concurrent misses.
        self._locks: dict[str, asyncio.Lock] = {}

    def _key_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for the given cache key."""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def get(self, sell_currency: str, buy_currency: str) -> dict[str, object] | None:
        """Return cached rate if it exists and is not expired.

        Returns a dict with ``rate`` (float) and ``effective_at`` (str),
        or ``None`` on miss/expiry.

        This is a synchronous method — the async wrapper ``get_or_refresh``
        handles stampede protection.
        """
        key = f"{sell_currency}/{buy_currency}"
        entry = self._rates.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._rates[key]
            return None
        return {"rate": entry.rate, "effective_at": entry.effective_at}

    async def get_or_refresh(
        self, sell_currency: str, buy_currency: str, loader
    ) -> dict[str, object]:
        """Return cached rate, or fetch from *loader* on miss.

        Uses per-key locking to prevent stampede: when a cache entry expires,
        only one caller executes *loader* while others wait for the result.

        Args:
            loader: Async callable ``(sell, buy) -> FXRate`` that fetches
                from the backing store (e.g., PostgreSQL).

        Returns:
            Dict with ``rate`` and ``effective_at`` keys.
        """
        key = f"{sell_currency}/{buy_currency}"

        # Fast path: check cache without lock.
        cached = self.get(sell_currency, buy_currency)
        if cached is not None:
            return cached

        # Slow path: acquire per-key lock, re-check, then load.
        async with self._key_lock(key):
            # Re-check inside lock (another caller may have refreshed).
            cached = self.get(sell_currency, buy_currency)
            if cached is not None:
                return cached

            # Cache miss — fetch from backing store.
            rate = await loader(sell_currency, buy_currency)
            self.set(rate.sell_currency, rate.buy_currency, rate.rate, rate.effective_at)

            return {"rate": rate.rate, "effective_at": rate.effective_at.isoformat()}

    def set(
        self, sell_currency: str, buy_currency: str, rate: float, effective_at
    ) -> None:
        """Cache a rate with the default TTL.

        Args:
            effective_at: datetime object or ISO string.
        """
        key = f"{sell_currency}/{buy_currency}"
        if isinstance(effective_at, str):
            eff_str = effective_at
        else:
            eff_str = effective_at.isoformat()

        self._rates[key] = _CacheEntry(
            rate=rate,
            effective_at=eff_str,
            expires_at=time.monotonic() + self._default_ttl,
        )

    def set_with_ttl(
        self, sell_currency: str, buy_currency: str, rate: float, effective_at, ttl: int
    ) -> None:
        """Cache a rate with an explicit TTL override (minimum ``_MIN_TTL``)."""
        key = f"{sell_currency}/{buy_currency}"
        if isinstance(effective_at, str):
            eff_str = effective_at
        else:
            eff_str = effective_at.isoformat()

        self._rates[key] = _CacheEntry(
            rate=rate,
            effective_at=eff_str,
            expires_at=time.monotonic() + max(ttl, _MIN_TTL),
        )

    def delete(self, sell_currency: str, buy_currency: str) -> None:
        """Remove a cached rate for a currency pair."""
        key = f"{sell_currency}/{buy_currency}"
        self._rates.pop(key, None)

    def purge(self) -> int:
        """Remove all expired entries. Returns count of purged entries."""
        now = time.monotonic()
        expired_keys = [k for k, v in self._rates.items() if now > v.expires_at]
        for key in expired_keys:
            del self._rates[key]
        return len(expired_keys)

    def clear(self) -> None:
        """Remove all entries (for testing)."""
        self._rates.clear()
