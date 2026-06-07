"""Ledger cache — immutable currency-to-ledger and type-to-code mappings.

Mirrors corebanking/internal/cache/ledger_cache.go (placeholder in Go,
fully implemented here with O(1) lookups from CurrencyInfo constants).
"""

from __future__ import annotations

from cbs.domain.currency import CURRENCIES, CurrencyInfo


class LedgerCache:
    """Immutable cache for currency-to-ledger and type-to-code mappings.

    Loaded once at startup from :data:`CURRENCIES` constants — never expires,
    no locking needed (read-only after construction).

    Provides O(1) lookups for:
    - Currency code → ledger ID (e.g., "USD" → 840)
    - Currency code → scale (e.g., "JPY" → 0, others → 2)
    - Full CurrencyInfo lookup by code

    Example::

        cache = LedgerCache()
        ledger_id = cache.ledger_for("USD")  # → 840
        scale = cache.scale_for("JPY")       # → 0
    """

    def __init__(self) -> None:
        self._ledgers: dict[str, int] = {}
        self._scales: dict[str, int] = {}
        self._info: dict[str, CurrencyInfo] = {}

        for code, info in CURRENCIES.items():
            self._ledgers[code] = info.ledger
            self._scales[code] = info.scale
            self._info[code] = info

    def ledger_for(self, currency: str) -> int | None:
        """Return the TigerBeetle ledger ID for a currency code.

        Returns ``None`` if the currency is not supported.
        """
        return self._ledgers.get(currency)

    def scale_for(self, currency: str) -> int | None:
        """Return the decimal scale for a currency code.

        Returns ``None`` if the currency is not supported.
        """
        return self._scales.get(currency)

    def info_for(self, currency: str) -> CurrencyInfo | None:
        """Return the full CurrencyInfo for a currency code.

        Returns ``None`` if the currency is not supported.
        """
        return self._info.get(currency)

    def is_supported(self, currency: str) -> bool:
        """Check if a currency code is supported."""
        return currency in self._ledgers

    @property
    def currencies(self) -> list[str]:
        """Return the list of supported currency codes."""
        return list(self._ledgers.keys())
