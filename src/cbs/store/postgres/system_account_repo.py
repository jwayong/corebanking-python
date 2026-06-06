"""PostgreSQL repository for system account storage.

Mirrors corebanking/internal/store/postgres/system_account.go
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()


@dataclass
class CreatedSystemAccount:
    """A system account created in TigerBeetle and persisted to PostgreSQL."""

    tb_account_id: bytes
    currency: str
    ledger: int
    code: int
    name: str


class SystemAccountRepo:
    """Handles system account queries and batch insertion."""

    @staticmethod
    async def exists(session: AsyncSession, currency: str) -> bool:
        """Return True if system accounts for the given currency already exist."""
        stmt = text("SELECT COUNT(*) FROM system_accounts WHERE currency = :currency")
        result = await session.execute(stmt, {"currency": currency})
        count = result.scalar()
        return count > 0

    @staticmethod
    async def get_by_code(session: AsyncSession, currency: str, code: int) -> Optional[bytes]:
        """Return the TigerBeetle account ID bytes for a given currency and code.

        Returns None if not found.
        """
        stmt = text(
            "SELECT tb_account_id FROM system_accounts WHERE currency = :currency AND account_code = :code"
        )
        result = await session.execute(stmt, {"currency": currency, "code": code})
        rec = result.fetchone()

        if rec is None:
            return None

        return rec.tb_account_id

    @staticmethod
    async def insert_batch(session: AsyncSession, accounts: list[CreatedSystemAccount]) -> None:
        """Insert a batch of system accounts in a transaction.

        Uses ON CONFLICT DO NOTHING to skip already-existing rows.
        """
        async with session.begin():
            for a in accounts:
                stmt = text(
                    """INSERT INTO system_accounts (tb_account_id, currency, ledger, account_code, account_name)
                         VALUES (:tb_account_id, :currency, :ledger, :code, :name)
                         ON CONFLICT (currency, account_code) DO NOTHING"""
                )
                await session.execute(
                    stmt,
                    {
                        "tb_account_id": a.tb_account_id,
                        "currency": a.currency,
                        "ledger": a.ledger,
                        "code": a.code,
                        "name": a.name,
                    },
                )
