"""PostgreSQL repository for product catalogue operations.

Mirrors corebanking/internal/store/postgres/product_store.go.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.currency import lookup_currency
from cbs.domain.errors import ErrNotFound
from cbs.domain.products import Fee, Product

log = structlog.get_logger()


@dataclass
class ProductRecord:
    """Product row data needed for account creation."""

    id: int
    code: str
    name: str
    category: str
    tb_account_code: int
    currency: str
    tb_ledger: int
    interest_rate: float | None
    is_active: bool


async def system_accounts_exist_for_currency(
    session: AsyncSession, currency: str
) -> bool:
    """Check whether system accounts exist for the given currency."""
    result = await session.execute(
        text("SELECT COUNT(*) FROM system_accounts WHERE currency = :currency"),
        {"currency": currency},
    )
    count = result.scalar()
    return count > 0


async def seed_products(session: AsyncSession, products: list[Product]) -> int:
    """Insert products and fee schedules into PG.

    Idempotent: products with existing codes are skipped entirely (including
    their fee schedules), so no orphan rows are created on re-runs.

    Returns:
        Count of newly inserted products.
    """
    async with session.begin():
        inserted = 0

        for product in products:
            # Check if product already exists — skip entirely to avoid orphan fee_schedules.
            result = await session.execute(
                text("SELECT EXISTS(SELECT 1 FROM products WHERE code = :code)"),
                {"code": product.code},
            )
            exists = result.scalar()
            if exists:
                log.debug("product already exists, skipping", code=product.code)
                continue

            cur = lookup_currency(product.currency)

            fee_schedule_id: int | None = None
            if product.fees:
                fees_json = json.dumps([
                    {"type": f.type, "description": f.description, "amount": f.amount}
                    for f in product.fees
                ])

                result = await session.execute(
                    text(
                        "INSERT INTO fee_schedules (name, fees) VALUES (:name, :fees) RETURNING id"
                    ),
                    {"name": f"{product.code}_fees", "fees": fees_json},
                )
                fee_schedule_id = result.scalar()

            await session.execute(
                text(
                    """INSERT INTO products
                       (code, name, category, tb_account_code, currency, tb_ledger,
                        interest_rate, fee_schedule_id, min_balance, max_balance, is_active)
                       VALUES (:code, :name, :category, :tb_account_code, :currency, :tb_ledger,
                               :interest_rate, :fee_schedule_id, :min_balance, :max_balance, :is_active)"""
                ),
                {
                    "code": product.code,
                    "name": product.name,
                    "category": product.category,
                    "tb_account_code": product.account_code,
                    "currency": product.currency,
                    "tb_ledger": cur.ledger,
                    "interest_rate": product.interest_rate if product.interest_rate != 0.0 else None,
                    "fee_schedule_id": fee_schedule_id,
                    "min_balance": product.min_balance,
                    "max_balance": product.max_balance,
                    "is_active": product.is_active,
                },
            )
            inserted += 1

        log.info("seeded_products", count=inserted, total=len(products))
        return inserted


async def count_products(session: AsyncSession) -> int:
    """Return the number of products in the catalogue."""
    result = await session.execute(text("SELECT COUNT(*) FROM products"))
    return result.scalar()


async def get_by_code(session: AsyncSession, code: str) -> ProductRecord | None:
    """Retrieve a product by its unique code.

    Returns:
        ProductRecord if found, None if not found.

    Raises:
        ErrNotFound: If no product exists with the given code.
    """
    result = await session.execute(
        text(
            """SELECT id, code, name, category, tb_account_code, currency,
                      tb_ledger, interest_rate, is_active
               FROM products
               WHERE code = :code"""
        ),
        {"code": code},
    )
    rec = result.fetchone()
    if rec is None:
        raise ErrNotFound

    return ProductRecord(
        id=rec.id,
        code=rec.code,
        name=rec.name,
        category=rec.category,
        tb_account_code=int(rec.tb_account_code),
        currency=rec.currency,
        tb_ledger=rec.tb_ledger,
        interest_rate=float(rec.interest_rate) if rec.interest_rate is not None else None,
        is_active=rec.is_active,
    )
