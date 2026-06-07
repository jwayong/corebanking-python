"""PostgreSQL repository for customer operations.

Mirrors corebanking/internal/store/postgres/customer_repo.go.
"""

from __future__ import annotations

import json
import structlog
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import NoResultFound

from cbs.domain.errors import ErrNotFound, ErrAlreadyExists
from cbs.store.postgres.database import Database

log = structlog.get_logger()


@dataclass
class Customer:
    """Customer record from the customers table."""

    customer_ref: str = ""
    name: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime | None = None
    accounts: list[CustomerAccount] = field(default_factory=list)


@dataclass
class CustomerAccount:
    """Customer account summary for listing endpoints."""

    id: int = 0
    account_number: str = ""
    product_code: str = ""
    currency: str = ""
    status: str = ""
    ownership_type: str = ""
    role: str = ""


# --- row constructors ---------------------------------------------------

def _row_to_customer(row) -> Customer:
    """Map a 4-column result row to Customer."""
    labels_raw = row[2]
    return Customer(
        customer_ref=row[0],
        name=row[1],
        labels=dict(labels_raw) if labels_raw else {},
        created_at=row[3],
    )


def _row_to_customer_account(row) -> CustomerAccount:
    """Map a 7-column result row to CustomerAccount."""
    return CustomerAccount(
        id=int(row[0]),
        account_number=row[1],
        product_code=row[2],
        currency=row[3],
        status=row[4],
        ownership_type=row[5],
        role=row[6],
    )


# --- helpers -------------------------------------------------------------

def _is_unique_violation(exc: Exception) -> bool:
    """Check if *exc* is a PostgreSQL unique-violation error (23505)."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    return getattr(orig, "pgcode", None) == "23505"


# --- repository ---------------------------------------------------------

class CustomerRepo:
    """Handles customer queries against PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # -- write operations ------------------------------------------------

    async def create(
        self,
        session,
        customer_ref: str,
        name: str,
        labels: dict[str, str] | None = None,
    ) -> Customer:
        """Insert a new customer.

        *labels* are stored as JSONB.  On unique-key collision (duplicate
        ``customer_ref``) raises ``ErrAlreadyExists``.

        Returns the created Customer record with ``created_at`` populated.
        """
        labels_json = json.dumps(labels if labels else {})

        try:
            result = await session.execute(
                text(
                    "INSERT INTO customers (customer_ref, name, labels) "
                    "VALUES (:customer_ref, :name, :labels::jsonb) "
                    "RETURNING customer_ref, name, labels, created_at"
                ),
                {
                    "customer_ref": customer_ref,
                    "name": name,
                    "labels": labels_json,
                },
            )
        except Exception as e:  # noqa: BLE001
            if _is_unique_violation(e):
                raise ErrAlreadyExists from e
            raise

        row = result.fetchone()
        if row is None:
            raise RuntimeError("customer insert returned no rows")

        customer = _row_to_customer(row)
        log.info("customer_created", customer_ref=customer_ref)
        return customer

    # -- read operations -------------------------------------------------

    async def get_by_ref(self, session, ref: str) -> Customer | None:
        """Fetch a customer by their reference.

        Returns ``None`` when no matching row exists (never raises).
        """
        try:
            result = await session.execute(
                text(
                    "SELECT customer_ref, name, labels, created_at "
                    "FROM customers "
                    "WHERE customer_ref = :ref"
                ),
                {"ref": ref},
            )
            row = result.fetchone()
        except NoResultFound:
            return None

        if row is None:
            return None

        return _row_to_customer(row)

    async def list_accounts_by_customer(
        self, session, ref: str
    ) -> list[CustomerAccount]:
        """Return accounts associated with a customer.

        Joins ``customer_accounts`` + ``accounts`` + ``products``.
        Returns an empty list when no accounts exist (never ``None``).
        """
        result = await session.execute(
            text(
                "SELECT a.id, a.account_number, p.code, p.currency, a.status, "
                "ca.ownership_type, ca.role "
                "FROM customer_accounts ca "
                "JOIN accounts a ON a.id = ca.account_id "
                "JOIN products p ON p.id = a.product_id "
                "WHERE ca.customer_ref = :ref "
                "ORDER BY a.created_at"
            ),
            {"ref": ref},
        )
        rows = result.fetchall()

        if not rows:
            return []

        return [_row_to_customer_account(row) for row in rows]
