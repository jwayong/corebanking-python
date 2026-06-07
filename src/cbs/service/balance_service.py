"""Balance service — business logic for balance queries.

Mirrors corebanking/internal/service/balance_service.go.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.accounts import compute_balance
from cbs.domain.currency import lookup_currency
from cbs.domain.errors import ErrNotFound, ValidationError

log = structlog.get_logger()


@dataclass
class BalanceResponse:
    """Balance query result for an account."""

    account_id: str
    posted_balance: int  # in minor units
    pending_amount: int
    available_balance: int
    currency: str
    scale: int


class BalanceService:
    """Handles balance queries.

    Fetches account metadata from PostgreSQL, cumulative fields from
    TigerBeetle, and computes human-readable balance figures.
    """

    def __init__(
        self,
        tb_account_repo,  # mypy: disable-error-code="empty-body"
        pg_account_repo,  # mypy: disable-error-code="empty-body"
        logger=None,
    ) -> None:
        self._tb_account_repo = tb_account_repo
        self._pg_account_repo = pg_account_repo
        self._log = (logger or log).bind(component="balance_service")

    async def get(
        self, session: "AsyncSession", id: str
    ) -> BalanceResponse:
        """Retrieve the balance for an account by its UUID.

        Validates *id*, fetches PG metadata (account code, currency),
        looks up the TB account for cumulative fields, and computes
        balance using ``domain.accounts.compute_balance()``.

        Raises:
            ValidationError: If *id* is empty or not a valid UUID.
            ErrNotFound: If the account does not exist in PostgreSQL.

        Returns:
            ``BalanceResponse`` with posted, pending, and available figures.
        """
        if not id:
            raise ValidationError("account id is required")

        # --- parse UUID ---------------------------------------------------
        try:
            acct_uuid = __import__("uuid").UUID(id)
        except (ValueError, AttributeError):
            raise ValidationError("account id must be a valid UUID")

        # --- 1. PG: fetch account metadata (code, currency) ---------------
        try:
            pg_acct = await self._pg_account_repo.get_by_tb_account_id(
                session, acct_uuid.bytes
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise
            self._log.error(
                "failed to get account from PG",
                account_id=id,
                error=str(exc),
            )
            raise RuntimeError(f"get account: {exc}") from exc

        if pg_acct is None:
            raise ErrNotFound

        # --- 2. TB: lookup account for cumulative fields ------------------
        try:
            from cbs.util.uuid import uuid_to_uint128

            tb_id = uuid_to_uint128(acct_uuid)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to convert uuid to uint128",
                account_id=id,
                error=str(exc),
            )
            raise RuntimeError(f"uuid to uint128: {exc}") from exc

        try:
            tb_acct = await self._tb_account_repo.lookup_account(tb_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to lookup TB account",
                account_id=id,
                error=str(exc),
            )
            raise RuntimeError(f"lookup tb account: {exc}") from exc

        # --- 3. compute balance from cumulative fields --------------------
        if tb_acct is not None:
            debits_posted = _uint128_to_int(tb_acct.get("debits_posted", b"\x00" * 16))
            credits_posted = _uint128_to_int(tb_acct.get("credits_posted", b"\x00" * 16))
            debits_pending = _uint128_to_int(tb_acct.get("debits_pending", b"\x00" * 16))
            credits_pending = _uint128_to_int(tb_acct.get("credits_pending", b"\x00" * 16))

            bal = compute_balance(
                debits_posted,
                credits_posted,
                debits_pending,
                credits_pending,
                pg_acct.tb_account_code,
            )
        else:
            from cbs.domain.accounts import ComputeBalanceResult

            bal = ComputeBalanceResult(posted=0, pending=0, available=0)

        # --- 4. get currency scale ----------------------------------------
        try:
            cur = lookup_currency(pg_acct.currency)
        except ValueError as exc:
            raise RuntimeError(f"lookup currency: {exc}") from exc

        return BalanceResponse(
            account_id=id,
            posted_balance=bal.posted,
            pending_amount=bal.pending,
            available_balance=bal.available,
            currency=pg_acct.currency,
            scale=cur.scale,
        )


def NewBalanceService(tb_account_repo, pg_account_repo, logger=None) -> BalanceService:
    """Factory — mirrors the Go constructor name."""
    return BalanceService(tb_account_repo, pg_account_repo, logger)


# --- helpers -------------------------------------------------------------

def _uint128_to_int(value: bytes | int) -> int:
    """Convert a TigerBeetle Uint128 value to a Python int.

    TB stores cumulative fields as 16-byte little-endian Uint128.
    If the value is already an int (e.g., from a mock), it is returned as-is.
    """
    if isinstance(value, int):
        return value
    return int.from_bytes(value, byteorder="little")
