"""PostgreSQL repository for fee collection operations.

Mirrors corebanking/internal/store/postgres/fee_collection_repo.go.
"""

from __future__ import annotations

import json
import structlog
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import NoResultFound

from cbs.domain.errors import ErrNotFound  # noqa: F401 — imported for domain error matching
from cbs.store.postgres.database import Database

log = structlog.get_logger()

# Batch size for transfer_metadata inserts to avoid PG parameter limits.
_FEE_COLLECTION_BATCH_SIZE = 500


@dataclass
class FeeItem:
    """A single fee entry from a fee schedule JSONB array."""

    type: str = ""
    description: str = ""
    amount: int = 0


@dataclass
class FeeBearingAccount:
    """Account fields needed for fee collection."""

    account_id: int = 0
    tb_account_id: bytes = b""
    tb_account_code: int = 0
    currency: str = ""
    tb_ledger: int = 0
    last_fee_date: datetime | None = None
    fees: list[FeeItem] = field(default_factory=list)


@dataclass
class FeeCollectionRecord:
    """A single record to persist after a successful fee transfer."""

    account_id: int = 0
    fee_date: datetime = field(default_factory=datetime.now)
    tb_transfer_id: bytes = b""
    description: str = ""
    reference: str = ""
    amount: int = 0


def _parse_fees(raw) -> list[FeeItem]:
    """Parse a JSONB fees column into a list of FeeItem."""
    if raw is None:
        return []

    # asyncpg returns JSONB as bytes; decode if needed.
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    items = json.loads(raw)
    if not isinstance(items, list):
        return []

    return [FeeItem(type=i.get("type", ""), description=i.get("description", ""), amount=int(i.get("amount", 0))) for i in items]


class FeeCollectionRepo:
    """Handles fee collection queries and persistence."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def fetch_fee_bearing_accounts(
        self, session, date: datetime
    ) -> list[FeeBearingAccount]:
        """Return all active accounts with an active fee schedule not yet collected for *date*'s month.

        Joins ``accounts``, ``products``, and ``fee_schedules``.  Filters on
        account status ``'active'``, fee schedule ``is_active = true``, and a
        month-level check on ``last_fee_date``.  The JSONB ``fees`` column is
        parsed into :class:`FeeItem` objects.

        Returns an empty list when no accounts qualify (never ``None``).
        """
        result = await session.execute(
            text(
                "SELECT a.id, a.tb_account_id, p.tb_account_code, p.currency, p.tb_ledger, "
                "       a.last_fee_date, fs.fees "
                "FROM accounts a "
                "JOIN products p ON p.id = a.product_id "
                "JOIN fee_schedules fs ON fs.id = p.fee_schedule_id "
                "WHERE a.status = 'active' "
                "  AND fs.is_active = true "
                "  AND (a.last_fee_date IS NULL "
                "       OR DATE_TRUNC('month', a.last_fee_date) < DATE_TRUNC('month', :date::date))"
            ),
            {"date": date},
        )
        rows = result.fetchall()

        accounts: list[FeeBearingAccount] = []
        for row in rows:
            accounts.append(
                FeeBearingAccount(
                    account_id=int(row[0]),
                    tb_account_id=row[1],
                    tb_account_code=int(row[2]),
                    currency=row[3],
                    tb_ledger=int(row[4]),
                    last_fee_date=row[5],
                    fees=_parse_fees(row[6]),
                )
            )

        log.info("fee_bearing_accounts_fetched", count=len(accounts), date=date)
        return accounts

    async def record_fee_collections(
        self, session, records: list[FeeCollectionRecord]
    ) -> None:
        """Update ``last_fee_date`` and insert transfer_metadata rows for collected fees.

        Wrapped in a single transaction.  Updates ``last_fee_date`` per unique
        account and batch-inserts transfer_metadata rows with
        ``ON CONFLICT (tb_transfer_id) DO NOTHING``.  Inserts are chunked at
        :data:`_FEE_COLLECTION_BATCH_SIZE` to avoid PG parameter limits.

        No-op when *records* is empty.
        """
        if not records:
            return

        async with session.begin():
            # Collect unique account_id -> fee_date for last_fee_date updates.
            seen: dict[int, datetime] = {}
            for rec in records:
                if rec.account_id not in seen:
                    seen[rec.account_id] = rec.fee_date

            for account_id, fee_date in seen.items():
                await session.execute(
                    text("UPDATE accounts SET last_fee_date = :fee_date WHERE id = :id"),
                    {"fee_date": fee_date, "id": account_id},
                )

            # Batch insert transfer_metadata rows.
            for i in range(0, len(records), _FEE_COLLECTION_BATCH_SIZE):
                batch = records[i : i + _FEE_COLLECTION_BATCH_SIZE]
                await self._insert_metadata_batch(session, batch)

        log.info("fee_collections_recorded", count=len(records))

    async def _insert_metadata_batch(
        self, session, records: list[FeeCollectionRecord]
    ) -> None:
        """Insert a batch of transfer_metadata rows with ON CONFLICT DO NOTHING."""
        n = len(records)

        # Build positional placeholders: ($1,$2,$3,$4,$5,$6),($7,...)
        placeholders = []
        args: list[object] = []
        for idx, rec in enumerate(records):
            base = idx * 6 + 1
            placeholders.append(
                f"(${base}, ${base + 1}, ${base + 2}, ${base + 3}, ${base + 4}, ${base + 5})"
            )
            args.extend([
                rec.tb_transfer_id,
                rec.account_id,
                "fee_income",       # counterparty
                rec.description,
                rec.reference,
                rec.fee_date,
            ])

        query = (
            f"INSERT INTO transfer_metadata "
            f"(tb_transfer_id, account_id, counterparty, description, reference, value_date) "
            f"VALUES {', '.join(placeholders)} "
            f"ON CONFLICT (tb_transfer_id) DO NOTHING"
        )

        await session.execute(text(query), args)
