"""PostgreSQL repository for account metadata operations.

Mirrors corebanking/internal/store/postgres/account_meta_repo.go.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import NoResultFound

from cbs.domain.errors import ErrNotFound
from cbs.store.postgres.database import Database

log = structlog.get_logger()


def _hash_prefix(prefix: str) -> int:
    """Stable int64 hash for pg_advisory_xact_lock key.

    Mirrors the Go implementation: h = h*31 + rune, with overflow
    masked to signed 64-bit range.
    """
    h = 0
    for c in prefix:
        h = h * 31 + ord(c)
    return h & 0x7FFFFFFFFFFFFFFF


# Shared SELECT column list for accounts JOIN products.
_ACCOUNT_WITH_PRODUCT_COLS = """\
    a.id, a.tb_account_id, a.account_number, a.status, a.opened_at, a.closed_at,
    a.product_id, p.code, p.name, p.category, p.tb_account_code, p.currency, p.tb_ledger
"""


@dataclass
class AccountRecord:
    """PG row from the accounts table."""

    id: int = 0
    tb_account_id: bytes = b""
    product_id: int = 0
    account_number: str = ""
    status: str = "active"
    opened_at: datetime | None = None


@dataclass
class CustomerAccountRecord:
    """Row in the customer_accounts join table."""

    customer_ref: str = ""
    account_id: int = 0
    ownership_type: str = ""
    role: str = ""


@dataclass
class AccountWithProduct:
    """Account metadata joined with product info."""

    id: int = 0
    tb_account_id: bytes = b""
    account_number: str = ""
    status: str = ""
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    product_id: int = 0
    product_code: str = ""
    product_name: str = ""
    category: str = ""
    tb_account_code: int = 0
    currency: str = ""
    tb_ledger: int = 0


@dataclass
class OwnerRecord:
    """Customer-account ownership row (customers JOIN customer_accounts)."""

    customer_ref: str = ""
    name: str = ""
    ownership_type: str = ""
    role: str = ""


# --- row constructors ---------------------------------------------------

def _row_to_account_with_product(row) -> AccountWithProduct:
    """Map a 13-column result row to AccountWithProduct."""
    return AccountWithProduct(
        id=int(row[0]),
        tb_account_id=row[1],
        account_number=row[2],
        status=row[3],
        opened_at=row[4],
        closed_at=row[5],
        product_id=int(row[6]),
        product_code=row[7],
        product_name=row[8],
        category=row[9],
        tb_account_code=int(row[10]),
        currency=row[11],
        tb_ledger=int(row[12]),
    )


def _row_to_owner_record(row) -> OwnerRecord:
    """Map a 4-column result row to OwnerRecord."""
    return OwnerRecord(
        customer_ref=row[0],
        name=row[1],
        ownership_type=row[2],
        role=row[3],
    )


# --- repository ---------------------------------------------------------

class AccountRepo:
    """Handles account metadata queries in PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # -- write operations ------------------------------------------------

    async def create(self, session, rec: AccountRecord) -> AccountRecord:
        """Insert a new account metadata record.

        Returns the created AccountRecord with PG-generated ``id`` and
        ``opened_at`` populated.  Mutates *rec* in-place (mirrors Go).
        """
        result = await session.execute(
            text(
                "INSERT INTO accounts "
                "(tb_account_id, product_id, account_number, status) "
                "VALUES (:tb_account_id, :product_id, :account_number, :status) "
                "RETURNING id, opened_at"
            ),
            {
                "tb_account_id": rec.tb_account_id,
                "product_id": rec.product_id,
                "account_number": rec.account_number,
                "status": rec.status,
            },
        )
        row = result.fetchone()
        if row is None:
            raise RuntimeError("account insert returned no rows")

        rec.id = int(row[0])
        rec.opened_at = row[1]
        log.info("account_created", id=rec.id, account_number=rec.account_number)
        return rec

    async def create_customer_account(self, session, rec: CustomerAccountRecord) -> None:
        """Create the customer-account relationship row."""
        await session.execute(
            text(
                "INSERT INTO customer_accounts "
                "(customer_ref, account_id, ownership_type, role) "
                "VALUES (:customer_ref, :account_id, :ownership_type, :role)"
            ),
            {
                "customer_ref": rec.customer_ref,
                "account_id": rec.account_id,
                "ownership_type": rec.ownership_type,
                "role": rec.role,
            },
        )

    # -- sequence --------------------------------------------------------

    async def next_account_sequence(self, session, prefix: str) -> int:
        """Return the next sequence number for a given account-number prefix.

        Uses ``pg_advisory_xact_lock`` keyed on a hash of *prefix* to
        serialize concurrent requests.  The lock is released when the
        surrounding transaction ends (commit or rollback).

        The MAX query uses ``COALESCE(MAX(...), 0)`` so that deleted or
        closed accounts do not cause sequence collisions.
        """
        lock_key = _hash_prefix(prefix)

        async with session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )

            result = await session.execute(
                text(
                    "SELECT COALESCE(MAX("
                    "  CAST(SUBSTRING(account_number FROM LENGTH(:prefix) + 2) AS BIGINT)"
                    "), 0) "
                    "FROM accounts "
                    "WHERE account_number LIKE :pattern"
                ),
                {
                    "prefix": prefix,
                    "pattern": f"{prefix}-%",
                },
            )
            row = result.fetchone()
            max_seq = int(row[0]) if row else 0

        return max_seq + 1

    # -- read operations -------------------------------------------------

    async def get_by_tb_account_id(
        self, session, tb_account_id: bytes
    ) -> AccountWithProduct | None:
        """Fetch account metadata with product info by TigerBeetle account ID.

        Returns ``None`` when no matching row exists (never raises).
        """
        try:
            result = await session.execute(
                text(
                    f"SELECT {_ACCOUNT_WITH_PRODUCT_COLS} "
                    "FROM accounts a "
                    "JOIN products p ON p.id = a.product_id "
                    "WHERE a.tb_account_id = :tb_account_id"
                ),
                {"tb_account_id": tb_account_id},
            )
            row = result.fetchone()
        except NoResultFound:
            return None

        if row is None:
            return None

        return _row_to_account_with_product(row)

    async def get_owners_by_account_id(
        self, session, account_id: int
    ) -> list[OwnerRecord]:
        """Return the owners of an account.

        Returns an empty list when no owners exist (never ``None``).
        """
        result = await session.execute(
            text(
                "SELECT ca.customer_ref, c.name, ca.ownership_type, ca.role "
                "FROM customer_accounts ca "
                "JOIN customers c ON c.customer_ref = ca.customer_ref "
                "WHERE ca.account_id = :account_id "
                "ORDER BY ca.created_at"
            ),
            {"account_id": account_id},
        )
        rows = result.fetchall()

        if not rows:
            return []

        return [_row_to_owner_record(row) for row in rows]

    async def close_account(self, session, account_id: int) -> datetime | None:
        """Set account status to ``'closed'`` and record ``closed_at``.

        Only transitions from ``'active'`` — returns ``None`` if the
        account does not exist or is already closed.
        """
        try:
            result = await session.execute(
                text(
                    "UPDATE accounts "
                    "SET status = 'closed', closed_at = NOW() "
                    "WHERE id = :id AND status = 'active' "
                    "RETURNING closed_at"
                ),
                {"id": account_id},
            )
            row = result.fetchone()
        except NoResultFound:
            return None

        if row is None:
            return None

        closed_at = row[0]
        log.info("account_closed", id=account_id, closed_at=closed_at)
        return closed_at

    async def list_by_customer_ref(
        self, session, customer_ref: str, cursor: int = 0, limit: int = 20
    ) -> list[AccountWithProduct]:
        """Return accounts for a customer with cursor-based pagination.

        Results are ordered by account ID ascending.  Fetches ``limit + 1``
        rows so the caller can detect whether more pages exist by checking
        ``len(result) > limit``.

        *cursor* is the account ID to start after (0 for the first page).
        """
        result = await session.execute(
            text(
                f"SELECT {_ACCOUNT_WITH_PRODUCT_COLS} "
                "FROM customer_accounts ca "
                "JOIN accounts a ON a.id = ca.account_id "
                "JOIN products p ON p.id = a.product_id "
                "WHERE ca.customer_ref = :customer_ref AND a.id > :cursor "
                "ORDER BY a.id ASC "
                "LIMIT :limit"
            ),
            {
                "customer_ref": customer_ref,
                "cursor": cursor,
                "limit": limit + 1,
            },
        )
        rows = result.fetchall()

        return [_row_to_account_with_product(row) for row in rows]
