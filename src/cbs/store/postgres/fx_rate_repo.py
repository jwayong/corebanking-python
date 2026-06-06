"""PostgreSQL repository for FX exchange rate operations.

Mirrors corebanking/internal/store/postgres/fx_rate_repo.go.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.errors import ErrNotFound

log = structlog.get_logger()


@dataclass
class FXRate:
    """Exchange rate record."""

    sell_currency: str  # Base currency (e.g., "USD")
    buy_currency: str   # Target currency (e.g., "EUR")
    rate: float         # 1 unit of sell = rate units of buy
    effective_at: datetime

    def pair_key(self) -> str:
        """Return a unique key for the currency pair."""
        return f"{self.sell_currency}/{self.buy_currency}"


async def get_latest(
    session: AsyncSession, sell_currency: str, buy_currency: str
) -> FXRate | None:
    """Return the most recent rate for a sell->buy currency pair.

    Raises:
        ErrNotFound: If no rate exists for the given pair.
    """
    result = await session.execute(
        text(
            """SELECT rate, effective_at
               FROM exchange_rates
               WHERE sell_currency = :sell AND buy_currency = :buy
               ORDER BY effective_at DESC
               LIMIT 1"""
        ),
        {"sell": sell_currency, "buy": buy_currency},
    )
    rec = result.fetchone()
    if rec is None:
        raise ErrNotFound

    return FXRate(
        sell_currency=sell_currency,
        buy_currency=buy_currency,
        rate=float(rec.rate),
        effective_at=rec.effective_at,
    )


async def insert(session: AsyncSession, rate: FXRate) -> None:
    """Insert a new FX rate record.

    Append-only: each insert creates a historical entry.
    """
    await session.execute(
        text(
            """INSERT INTO exchange_rates (sell_currency, buy_currency, rate, effective_at)
               VALUES (:sell, :buy, :rate, :effective_at)"""
        ),
        {
            "sell": rate.sell_currency,
            "buy": rate.buy_currency,
            "rate": rate.rate,
            "effective_at": rate.effective_at,
        },
    )


async def get_by_effective_at(
    session: AsyncSession, sell_currency: str, buy_currency: str, at: datetime
) -> FXRate | None:
    """Return the rate that was active at a specific point in time.

    Selects the most recent rate whose effective_at is <= the given timestamp.

    Raises:
        ErrNotFound: If no rate was active at the given time for this pair.
    """
    result = await session.execute(
        text(
            """SELECT rate, effective_at
               FROM exchange_rates
               WHERE sell_currency = :sell AND buy_currency = :buy AND effective_at <= :at
               ORDER BY effective_at DESC
               LIMIT 1"""
        ),
        {"sell": sell_currency, "buy": buy_currency, "at": at},
    )
    rec = result.fetchone()
    if rec is None:
        raise ErrNotFound

    return FXRate(
        sell_currency=sell_currency,
        buy_currency=buy_currency,
        rate=float(rec.rate),
        effective_at=rec.effective_at,
    )
