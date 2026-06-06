"""Loan details repository — loan lifecycle operations against PostgreSQL.

Mirrors the Go `postgres.LoanRepo` with async SQLAlchemy Core queries.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import NoResultFound

from cbs.domain.errors import ErrNotFound

log = structlog.get_logger()


@dataclass
class LoanDetailRecord:
    """Row from the loan_details table."""

    id: int = 0
    account_id: int = 0
    principal: int = 0
    outstanding: int = 0
    interest_rate: float = 0.0
    term_months: int = 0
    disbursed_at: datetime | None = None
    maturity_date: datetime | None = None
    next_payment_due: datetime | None = None
    payment_amount: int = 0
    arrears_amount: int = 0
    status: str = ""
    created_at: datetime | None = None


_LOAN_COLS = (
    "id, account_id, principal, outstanding, interest_rate, term_months, "
    "disbursed_at, maturity_date, next_payment_due, payment_amount, "
    "arrears_amount, status"
)


def _row_to_loan(row: tuple) -> LoanDetailRecord:
    return LoanDetailRecord(
        id=row[0],
        account_id=row[1],
        principal=row[2],
        outstanding=row[3],
        interest_rate=row[4],
        term_months=row[5],
        disbursed_at=row[6],
        maturity_date=row[7],
        next_payment_due=row[8],
        payment_amount=row[9],
        arrears_amount=row[10],
        status=row[11],
    )


async def create(session, rec: LoanDetailRecord) -> LoanDetailRecord:
    """Insert a new loan_details row and return the created record."""
    result = await session.execute(
        text(
            f"INSERT INTO loan_details "
            f"(account_id, principal, outstanding, interest_rate, term_months, "
            f" disbursed_at, maturity_date, next_payment_due, payment_amount, status) "
            f"VALUES (:account_id, :principal, :outstanding, :interest_rate, :term_months, "
            f" :disbursed_at, :maturity_date, :next_payment_due, :payment_amount, :status) "
            f"RETURNING id, created_at"
        ),
        {
            "account_id": rec.account_id,
            "principal": rec.principal,
            "outstanding": rec.outstanding,
            "interest_rate": rec.interest_rate,
            "term_months": rec.term_months,
            "disbursed_at": rec.disbursed_at,
            "maturity_date": rec.maturity_date,
            "next_payment_due": rec.next_payment_due,
            "payment_amount": rec.payment_amount,
            "status": rec.status,
        },
    )
    row = result.fetchone()
    if row is None:
        raise RuntimeError("loan_details insert returned no rows")
    rec.id = row[0]
    rec.created_at = row[1]
    log.info("loan_details_created", id=rec.id, account_id=rec.account_id)
    return rec


async def get_by_account_id(session, account_id: int) -> LoanDetailRecord | None:
    """Retrieve loan details by PG account ID. Returns None if not found."""
    result = await session.execute(
        text(f"SELECT {_LOAN_COLS} FROM loan_details WHERE account_id = :account_id"),
        {"account_id": account_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    rec = _row_to_loan(row)
    log.debug("loan_details_fetched", account_id=account_id, id=rec.id)
    return rec


async def reduce_outstanding(
    session, repayment_amount: int, account_id: int, payment_date: datetime
) -> LoanDetailRecord | None:
    """Decrease outstanding by repayment_amount and auto-close at zero.

    Uses CTE to atomically update loan_details and insert repayment record.
    The WHERE guard prevents negative outstanding — raises ErrNotFound if
    repayment exceeds current balance (mirrors Go behaviour).

    Returns the updated LoanDetailRecord, or raises ErrNotFound if not found.
    """
    try:
        result = await session.execute(
            text(
                f"WITH updated AS ("
                f"  UPDATE loan_details "
                f"  SET outstanding = outstanding - :repayment_amount, "
                f"      status = CASE WHEN outstanding - :repayment_amount <= 0 THEN 'closed' ELSE status END "
                f"  WHERE account_id = :account_id AND outstanding >= :repayment_amount "
                f"  RETURNING {_LOAN_COLS}"
                f") "
                f"INSERT INTO loan_repayments (account_id, amount, payment_date, tb_transfer_id) "
                f"SELECT :account_id, :repayment_amount, :payment_date, NULL FROM updated"
            ),
            {
                "repayment_amount": repayment_amount,
                "account_id": account_id,
                "payment_date": payment_date,
            },
        )
        row = result.fetchone()
    except NoResultFound:
        raise ErrNotFound

    if row is None:
        raise ErrNotFound
    rec = _row_to_loan(row)
    log.info(
        "loan_outstanding_reduced",
        account_id=account_id,
        new_outstanding=rec.outstanding,
        status=rec.status,
    )
    return rec


async def update_arrears_status(session) -> int:
    """Mark loans as in_arrears when next_payment_due is past and outstanding > 0.

    Returns the number of rows updated.
    """
    result = await session.execute(
        text(
            "UPDATE loan_details "
            "SET status = 'in_arrears' "
            "WHERE next_payment_due < CURRENT_DATE "
            "AND outstanding > 0 "
            "AND status NOT IN ('closed', 'in_arrears')"
        )
    )
    count = result.rowcount or 0
    log.info("loan_arrears_updated", count=count)
    return count


async def set_disbursed_at(session, account_id: int, disbursed_at: datetime) -> None:
    """Update the disbursed_at timestamp for a loan account."""
    await session.execute(
        text("UPDATE loan_details SET disbursed_at = :disbursed_at WHERE account_id = :account_id"),
        {"disbursed_at": disbursed_at, "account_id": account_id},
    )
    log.info("loan_disbursed_at_set", account_id=account_id, disbursed_at=disbursed_at)
