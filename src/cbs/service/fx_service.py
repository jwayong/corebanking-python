"""FX service — business logic for cross-currency exchange operations.

Mirrors corebanking/internal/service/fx_service.go with linked TB transfers
through liquidity pools for atomic FX execution.
"""

from __future__ import annotations

import asyncio
import structlog
from datetime import date, datetime
from typing import TYPE_CHECKING
import uuid as _uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.accounts import (
    AccountCode,
    Balance,
)
from cbs.domain.currency import lookup_currency
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrInvalidAccount,
    ErrLiquidityPoolUnavailable,
    ErrNotFound,
    ErrServiceUnavailable,
    ValidationError,
)
from cbs.domain.transfers import (
    FXLeg,
    FXRequest,
    FXResponse,
)
from cbs.service.errors import find_linked_root_cause, map_tb_error
from cbs.store.postgres.audit_repo import TransferMetadataRecord
from cbs.util.tb_types import int_to_uint128, uint64_to_uint128
from cbs.util.uuid import (
    generate_uuidv7,
    tb_id_to_uuid,
    uint128_to_uuid,
    uuid_to_uint128,
)

log = structlog.get_logger()

# TB transfer flag: Linked — set on all legs except the last.
_TB_LINKED_FLAG = 0x10


class FXService:
    """Handles FX (cross-currency) transfers via linked TB transfers.

    Executes two atomic legs through liquidity pool system accounts:
      1. Customer sell account → Sell-currency liquidity pool (debit)
      2. Buy-currency liquidity pool → Customer buy account (credit)

    TigerBeetle's linked transfer mechanism ensures all-or-nothing semantics
    across both legs.
    """

    def __init__(
        self,
        fx_rate_repo,  # mypy: disable-error-code="empty-body"
        fx_cache,  # mypy: disable-error-code="empty-body"
        tb_transfer_repo,  # mypy: disable-error-code="empty-body"
        tb_account_repo,  # mypy: disable-error-code="empty-body"
        account_meta_repo,  # mypy: disable-error-code="empty-body"
        system_account_repo,  # mypy: disable-error-code="empty-body"
        metadata_writer,  # mypy: disable-error-code="empty-body"
        logger=None,
    ) -> None:
        self._fx_rate_repo = fx_rate_repo
        self._fx_cache = fx_cache
        self._tb_transfer_repo = tb_transfer_repo
        self._tb_account_repo = tb_account_repo
        self._account_meta_repo = account_meta_repo
        self._system_account_repo = system_account_repo
        self._metadata_writer = metadata_writer
        self._log = (logger or log).bind(component="fx_service")

    # -- public methods ---------------------------------------------------

    async def exchange(
        self, session: "AsyncSession", req: FXRequest
    ) -> FXResponse:
        """Execute an FX exchange with two linked TB transfers.

        Debits the customer's sell-currency account and credits their
        buy-currency account via liquidity pool system accounts. Both legs
        are executed atomically using TigerBeetle's linked transfer mechanism.

        Raises:
            ValidationError: If request fields are invalid or cross-currency
                requirement is not met.
            ErrNotFound: If no FX rate exists for the currency pair.
            ErrInvalidAccount: If customer accounts do not exist in TB or PG.
            ErrLiquidityPoolUnavailable: If liquidity pool accounts are missing.
            TransferError: If TB rejects the transfer (e.g., insufficient balance).

        Returns:
            ``FXResponse`` with two legs, rate, and status 'posted'.
        """
        req.validate()

        # 1. Resolve value date (default to now).
        value_date = datetime.now()
        if req.value_date:
            try:
                value_date = datetime.combine(
                    date.fromisoformat(req.value_date), datetime.min.time()
                )
            except ValueError as exc:
                raise ValidationError(
                    "value_date must be in YYYY-MM-DD format"
                ) from exc

        # 2. Lookup currency info for both sides — get ledgers and scales.
        try:
            sell_info = lookup_currency(req.sell_currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        try:
            buy_info = lookup_currency(req.buy_currency)
        except ValueError as exc:
            raise ValidationError(
                f"unsupported buy_currency: {req.buy_currency}"
            ) from exc

        # 3. Validate cross-currency: sell and buy must be on different ledgers.
        if sell_info.ledger == buy_info.ledger:
            raise ValidationError(
                "sell_currency and buy_currency must be on different ledgers"
            )

        # 4. Resolve rate: use client-provided if > 0, otherwise cache-first lookup.
        effective_rate = req.rate
        if effective_rate <= 0:
            try:
                rate_info = await self._resolve_rate(
                    session, req.sell_currency, req.buy_currency
                )
            except Exception as exc:  # noqa: BLE001
                if exc is ErrNotFound:
                    raise
                self._log.error(
                    "failed to resolve fx rate",
                    sell=req.sell_currency,
                    buy=req.buy_currency,
                    error=str(exc),
                )
                raise ErrNotFound from exc
            effective_rate = rate_info["rate"]

        # 5. Convert rate to fixed-point int64 at scale 6 for integer arithmetic.
        rate_int = round(effective_rate * 1_000_000)

        # 6. Compute buy amount using integer arithmetic (round-half-up).
        buy_amount = _compute_buy_amount(
            req.sell_amount, rate_int, sell_info.scale, buy_info.scale
        )

        # 7. Generate shared correlation ID for linking the two legs in metadata.
        correlation_id = generate_uuidv7()
        corr_bytes = uuid_to_uint128(correlation_id)

        # 8. Resolve liquidity pool accounts for both currencies from PG.
        try:
            sell_liq_bytes = await self._system_account_repo.get_by_code(
                session, req.sell_currency, int(AccountCode.LIQUIDITY_POOL)
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to resolve sell liquidity pool",
                error=str(exc),
            )
            raise ErrLiquidityPoolUnavailable from exc

        if sell_liq_bytes is None:
            raise ErrLiquidityPoolUnavailable

        try:
            buy_liq_bytes = await self._system_account_repo.get_by_code(
                session, req.buy_currency, int(AccountCode.LIQUIDITY_POOL)
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to resolve buy liquidity pool",
                error=str(exc),
            )
            raise ErrLiquidityPoolUnavailable from exc

        if buy_liq_bytes is None:
            raise ErrLiquidityPoolUnavailable

        # 9. Convert customer account UUIDs to TB uint128.
        try:
            sell_acct_uuid = _uuid.UUID(req.debit_account_id)
        except ValueError as exc:
            raise ValidationError(
                f"invalid debit_account_id: {exc}"
            ) from exc
        try:
            buy_acct_uuid = _uuid.UUID(req.credit_account_id)
        except ValueError as exc:
            raise ValidationError(
                f"invalid credit_account_id: {exc}"
            ) from exc

        sell_acct_tb = uuid_to_uint128(sell_acct_uuid)
        buy_acct_tb = uuid_to_uint128(buy_acct_uuid)

        # Convert liquidity pool PG bytes (big-endian UUID) to TB uint128.
        sell_liq_tb = uuid_to_uint128(tb_id_to_uuid(sell_liq_bytes))
        buy_liq_tb = uuid_to_uint128(tb_id_to_uuid(buy_liq_bytes))

        # 10. Batch lookup all 4 accounts in TB.
        all_ids = [sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb]
        try:
            tb_map = await self._tb_account_repo.lookup_accounts(all_ids)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to lookup accounts",
                error=str(exc),
            )
            raise ErrServiceUnavailable from exc

        # 11. Validate all 4 accounts exist in TB.
        sell_acct = tb_map.get(sell_acct_tb)
        if sell_acct is None:
            raise ErrInvalidAccount

        buy_acct = tb_map.get(buy_acct_tb)
        if buy_acct is None:
            raise ErrInvalidAccount

        sell_liq_acct = tb_map.get(sell_liq_tb)
        if sell_liq_acct is None:
            raise ErrLiquidityPoolUnavailable

        buy_liq_acct = tb_map.get(buy_liq_tb)
        if buy_liq_acct is None:
            raise ErrLiquidityPoolUnavailable

        # 12. Validate ledger consistency.
        if sell_acct.get("ledger") != sell_info.ledger:
            raise ValidationError(
                "debit account currency does not match sell_currency"
            )
        if buy_acct.get("ledger") != buy_info.ledger:
            raise ValidationError(
                "credit account currency does not match buy_currency"
            )
        if sell_liq_acct.get("ledger") != sell_info.ledger:
            raise ErrLiquidityPoolUnavailable
        if buy_liq_acct.get("ledger") != buy_info.ledger:
            raise ErrLiquidityPoolUnavailable

        # 13. Validate customer accounts are active in PG.
        try:
            sell_meta = await self._account_meta_repo.get_by_tb_account_id(
                session, sell_acct_uuid.bytes
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise ErrInvalidAccount from exc
            self._log.error(
                "failed to get sell account metadata",
                error=str(exc),
            )
            raise RuntimeError(f"get sell account: {exc}") from exc

        if sell_meta is None:
            raise ErrInvalidAccount
        if sell_meta.status == "closed":
            raise ErrAccountClosed

        try:
            buy_meta = await self._account_meta_repo.get_by_tb_account_id(
                session, buy_acct_uuid.bytes
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise ErrInvalidAccount from exc
            self._log.error(
                "failed to get buy account metadata",
                error=str(exc),
            )
            raise RuntimeError(f"get buy account: {exc}") from exc

        if buy_meta is None:
            raise ErrInvalidAccount
        if buy_meta.status == "closed":
            raise ErrAccountClosed

        # 14. Build the two linked TB transfers.
        value_date_nanos = int(value_date.timestamp() * 1_000_000_000)
        user_data_128 = _pack_user_data_128(rate_int)

        leg1_uuid = generate_uuidv7()
        leg2_uuid = generate_uuidv7()

        transfers = [
            # Leg 1: Customer sell account → Sell-currency liquidity pool (linked)
            {
                "id": uuid_to_uint128(leg1_uuid),
                "debit_account_id": sell_acct_tb,
                "credit_account_id": sell_liq_tb,
                "amount": uint64_to_uint128(req.sell_amount),
                "ledger": sell_info.ledger,
                "code": 4,  # FX_DEBIT
                "user_data_128": user_data_128,
                "user_data_64": value_date_nanos,
                "flags": _TB_LINKED_FLAG,  # Linked flag set
            },
            # Leg 2: Buy-currency liquidity pool → Customer buy account (final)
            {
                "id": uuid_to_uint128(leg2_uuid),
                "debit_account_id": buy_liq_tb,
                "credit_account_id": buy_acct_tb,
                "amount": uint64_to_uint128(buy_amount),
                "ledger": buy_info.ledger,
                "code": 5,  # FX_CREDIT
                "user_data_128": user_data_128,
                "user_data_64": value_date_nanos,
                "flags": 0,  # Final leg — no Linked flag to commit batch
            },
        ]

        self._log.info(
            "executing_fx_exchange",
            correlation=str(correlation_id),
            sell_amount=req.sell_amount,
            buy_amount=buy_amount,
            rate=effective_rate,
            sell_currency=req.sell_currency,
            buy_currency=req.buy_currency,
        )

        # 15. Execute atomically via TB linked transfers.
        try:
            results = await self._tb_transfer_repo.create_transfers(transfers)
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn(
                "fx exchange failed",
                correlation=str(correlation_id),
                error=str(domain_err),
            )
            raise domain_err from exc

        # 16. Map errors using linked root cause analysis.
        root_cause = find_linked_root_cause(results)
        if root_cause is not None:
            self._log.warn(
                "fx exchange failed",
                correlation=str(correlation_id),
                error=str(root_cause),
            )
            raise root_cause

        self._log.info(
            "fx exchange posted",
            correlation=str(correlation_id),
            sell=req.sell_amount,
            buy=buy_amount,
            rate=effective_rate,
        )

        # 17. Convert liquidity pool TB IDs to UUID strings for response.
        sell_liq_uuid = uint128_to_uuid(sell_liq_tb)
        buy_liq_uuid = uint128_to_uuid(buy_liq_tb)

        # 18. Record metadata in background (detached from request context).
        if self._metadata_writer is not None:
            asyncio.create_task(
                self._write_metadata(
                    session,
                    transfers,
                    corr_bytes,
                    sell_meta.id if hasattr(sell_meta, "id") else 0,
                    buy_meta.id if hasattr(buy_meta, "id") else 0,
                    str(sell_liq_uuid),
                    str(buy_liq_uuid),
                    req.reference,
                    value_date,
                )
            )

        # 19. Build response.
        value_date_str = value_date.strftime("%Y-%m-%d")

        return FXResponse(
            id=str(correlation_id),
            transfer_type="fx",
            legs=[
                FXLeg(
                    id=str(leg1_uuid),
                    debit_account_id=req.debit_account_id,
                    credit_account_id=str(sell_liq_uuid),
                    amount=_build_balance(
                        req.sell_amount, req.sell_currency, sell_info.scale
                    ),
                    code="fx_debit",
                ),
                FXLeg(
                    id=str(leg2_uuid),
                    debit_account_id=str(buy_liq_uuid),
                    credit_account_id=req.credit_account_id,
                    amount=_build_balance(
                        buy_amount, req.buy_currency, buy_info.scale
                    ),
                    code="fx_credit",
                ),
            ],
            rate=effective_rate,
            sell_amount=_build_balance(
                req.sell_amount, req.sell_currency, sell_info.scale
            ),
            buy_amount=_build_balance(
                buy_amount, req.buy_currency, buy_info.scale
            ),
            value_date=value_date_str,
            status="posted",
            created_at=datetime.now(),
        )

    # -- private helpers --------------------------------------------------

    async def _resolve_rate(
        self, session: "AsyncSession", sell_currency: str, buy_currency: str
    ) -> dict[str, object]:
        """Cache-first rate lookup with PG fallback.

        Tries the in-memory cache first. On miss, fetches from PostgreSQL
        and populates the cache for subsequent requests.

        Returns:
            Dict with ``rate`` (float) and ``effective_at`` keys.

        Raises:
            ErrNotFound: If no rate exists for the currency pair.
        """
        # Try cache first (synchronous, no lock needed for fast path).
        cached = self._fx_cache.get(sell_currency, buy_currency)
        if cached is not None:
            return cached

        # Cache miss — use get_or_refresh with PG loader.
        async def _loader(sell: str, buy: str):
            try:
                rate_row = await self._fx_rate_repo.get_latest(
                    session, sell, buy
                )
            except Exception as exc:  # noqa: BLE001
                if exc is ErrNotFound:
                    raise
                self._log.error(
                    "fx rate pg fetch failed",
                    sell=sell,
                    buy=buy,
                    error=str(exc),
                )
                raise ErrNotFound from exc

            if rate_row is None:
                raise ErrNotFound

            return rate_row

        try:
            return await self._fx_cache.get_or_refresh(
                sell_currency, buy_currency, _loader
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise
            self._log.error(
                "fx rate resolve failed",
                sell=sell_currency,
                buy=buy_currency,
                error=str(exc),
            )
            raise ErrNotFound from exc

    async def _write_metadata(
        self,
        session: "AsyncSession",
        transfers: list[dict],
        corr_bytes: bytes,
        sell_meta_id: int,
        buy_meta_id: int,
        sell_liq_uuid_str: str,
        buy_liq_uuid_str: str,
        reference: str,
        value_date: datetime,
    ) -> None:
        """Write PG transfer metadata for both FX legs (fire-and-forget).

        Errors are logged but do not cause the exchange to fail.
        """
        corr_slice = bytes(corr_bytes)

        for i, t in enumerate(transfers):
            try:
                if isinstance(value_date, datetime):
                    vd = value_date.date()
                else:
                    vd = date.today()

                rec = TransferMetadataRecord(
                    tb_transfer_id=bytes(t.get("id", b"")),
                    tb_correlation=corr_slice,
                    account_id=sell_meta_id if i == 0 else buy_meta_id,
                    counterparty=sell_liq_uuid_str if i == 0 else buy_liq_uuid_str,
                    reference=reference if reference else None,
                    value_date=vd,
                )
                await self._metadata_writer.create_transfer_metadata(session, rec)
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "fx metadata write failed",
                    leg=i,
                    error=str(exc),
                )


# -- module-level helpers ------------------------------------------------

def _pack_user_data_128(rate_int: int) -> bytes:
    """Pack rate (int64, fixed-point scale 6) into TB Uint128.

    Bytes 0-7: rate as little-endian uint64.
    Bytes 8-15: zero (reserved).

    Args:
        rate_int: Rate as int64 at scale 6 (e.g., 0.85 → 850000).

    Returns:
        16 bytes in little-endian order (Uint128 format).
    """
    rate_u64 = rate_int if rate_int >= 0 else int(rate_int & 0xFFFFFFFFFFFFFFFF)
    return rate_u64.to_bytes(16, byteorder="little")


def _compute_buy_amount(
    sell_amount: int, rate_int: int, sell_scale: int, buy_scale: int
) -> int:
    """Calculate buy_amount in minor units using integer arithmetic.

    rate_int is fixed-point at scale 6 (e.g., 0.85 = 850000).

    Formula: buy_minor = (sell_amount * rate_int) / 10^(sell_scale + 6 - buy_scale),
    with round-half-up.

    Args:
        sell_amount: Sell amount in sell-currency minor units.
        rate_int: Rate as int64 at scale 6.
        sell_scale: Decimal places of sell currency.
        buy_scale: Decimal places of buy currency.

    Returns:
        Buy amount in buy-currency minor units (non-negative).
    """
    # Step 1: sell_amount * rate_int → result at scale (sell_scale + 6).
    raw = sell_amount * rate_int

    # Step 2: Convert from scale (sell_scale + 6) to buy_scale with round-half-up.
    diff = sell_scale + 6 - buy_scale

    if diff > 0:
        divisor = 10 ** diff
        raw = (raw + divisor // 2) // divisor  # round half up
    elif diff < 0:
        raw = raw * (10 ** (-diff))

    if raw < 0:
        return 0
    return raw


def _build_balance(amount: int, currency: str, scale: int) -> Balance:
    """Build a Balance object for response construction."""
    return Balance(amount=amount, currency=currency, scale=scale)


# -- factory -------------------------------------------------------------

def NewFXService(
    fx_rate_repo,
    fx_cache,
    tb_transfer_repo,
    tb_account_repo,
    account_meta_repo,
    system_account_repo,
    metadata_writer,
    logger=None,
) -> FXService:
    """Create a new FXService instance.

    Mirrors the Go constructor pattern for consistency across ports.
    """
    return FXService(
        fx_rate_repo=fx_rate_repo,
        fx_cache=fx_cache,
        tb_transfer_repo=tb_transfer_repo,
        tb_account_repo=tb_account_repo,
        account_meta_repo=account_meta_repo,
        system_account_repo=system_account_repo,
        metadata_writer=metadata_writer,
        logger=logger,
    )
