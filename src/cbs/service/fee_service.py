"""Fee service — business logic for fee charging operations.

Mirrors the Charge method from corebanking/internal/service/transfer_service.go
as a standalone service.  Follows TB-first dual-write semantics: execute the
fee transfer in TigerBeetle, then write PG metadata (fire-and-forget).
"""

from __future__ import annotations

import structlog
from datetime import date, datetime
from typing import TYPE_CHECKING
import uuid as _uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.accounts import AccountCode, Balance
from cbs.domain.currency import lookup_currency
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrAccountFrozen,
    ErrInvalidAccount,
    ErrNotFound,
    ErrServiceUnavailable,
    ValidationError,
)
from cbs.domain.transfers import (
    FeeChargeRequest,
    FeeChargeResponse,
    map_transfer_code,
)
from cbs.service.errors import check_transfer_result, map_tb_error
from cbs.store.postgres.audit_repo import TransferMetadataRecord
from cbs.util.tb_types import int_to_uint128
from cbs.util.uuid import (
    generate_uuidv7,
    tb_id_to_uuid,
    uuid_to_uint128,
)

log = structlog.get_logger()


class FeeService:
    """Handles fee charging with TB-first dual-write.

    Debits a customer account and credits the system Fee Income account
    (code 4110).  The fee income account is resolved from PG per-currency,
    then both accounts are validated in TigerBeetle before execution.
    """

    def __init__(
        self,
        tb_transfer_repo,  # mypy: disable-error-code="empty-body"
        tb_account_repo,   # mypy: disable-error-code="empty-body"
        account_meta_repo,  # mypy: disable-error-code="empty-body"
        system_account_repo,  # mypy: disable-error-code="empty-body"
        metadata_writer,  # mypy: disable-error-code="empty-body"
        logger=None,
    ) -> None:
        self._tb_transfer_repo = tb_transfer_repo
        self._tb_account_repo = tb_account_repo
        self._account_meta_repo = account_meta_repo
        self._system_account_repo = system_account_repo
        self._metadata_writer = metadata_writer
        self._log = (logger or log).bind(component="fee_service")

    # -- public methods ---------------------------------------------------

    async def charge(
        self, session: "AsyncSession", req: FeeChargeRequest
    ) -> FeeChargeResponse:
        """Apply a fee by debiting a customer account and crediting Fee Income.

        Steps:
            1. Validate the request fields.
            2. Parse value_date (defaults to now).
            3. Resolve customer account TB ID and validate via PG (active status).
            4. Resolve Fee Income system account from PG by currency + code 4110.
            5. Batch lookup both accounts in TB — validate existence and same ledger.
            6. Generate UUIDv7 fee transfer ID.
            7. Build and execute TB transfer: debit customer, credit Fee Income.
            8. Write PG metadata (fire-and-forget).

        Raises:
            ValidationError: If request fields are invalid or ledger mismatch.
            ErrInvalidAccount: If customer account does not exist in PG or TB.
            ErrAccountClosed / ErrAccountFrozen: If customer account is inactive.
            TransferError: If TB rejects the transfer (e.g., insufficient balance).

        Returns:
            ``FeeChargeResponse`` with status 'posted'.
        """
        req.validate()

        # 1. Parse value date (defaults to now).
        value_date = datetime.now()
        if req.value_date:
            value_date = date.fromisoformat(req.value_date)

        # 2. Resolve customer account TB ID from UUID string.
        customer_uuid = _uuid.UUID(req.customer_account_id)
        customer_tb_id = uuid_to_uint128(customer_uuid)

        # 3. Validate customer account in PG (must be active).
        meta = await self._get_account_metadata(session, req.customer_account_id)

        # 4. Resolve Fee Income system account from PG.
        fee_income_bytes = await self._system_account_repo.get_by_code(
            session, req.currency, int(AccountCode.INC_FEE_SERVICE)
        )
        if fee_income_bytes is None:
            self._log.error(
                "fee_income_account_not_found",
                currency=req.currency,
            )
            raise ErrInvalidAccount

        # 5. Convert fee income bytes (big-endian UUID) to TB uint128.
        if len(fee_income_bytes) != 16:
            self._log.error(
                "invalid_fee_income_id",
                length=len(fee_income_bytes),
            )
            raise ErrInvalidAccount
        fee_income_uuid = tb_id_to_uuid(fee_income_bytes)
        fee_income_tb_id = uuid_to_uint128(fee_income_uuid)

        # 6. Look up currency info for ledger and scale.
        try:
            cur_info = lookup_currency(req.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # 7. Batch lookup both accounts in TB — validate existence and same ledger.
        try:
            tb_map = await self._tb_account_repo.lookup_accounts(
                [customer_tb_id, fee_income_tb_id]
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "account_lookup_failed",
                error=str(exc),
            )
            raise ErrServiceUnavailable from exc

        customer_acct = tb_map.get(customer_tb_id)
        fee_income_acct = tb_map.get(fee_income_tb_id)
        if customer_acct is None:
            raise ErrInvalidAccount
        if fee_income_acct is None:
            self._log.error(
                "fee_income_account_not_found_in_tb",
                currency=req.currency,
            )
            raise ErrInvalidAccount

        # Both accounts must share the same ledger.
        if customer_acct.get("ledger") != fee_income_acct.get("ledger"):
            raise ValidationError(
                f"customer account and fee income account must be on the same ledger: "
                f"customer={customer_acct.get('ledger')}, fee_income={fee_income_acct.get('ledger')}"
            )

        # 8. Generate UUIDv7 for the fee transfer ID.
        fee_uuid = generate_uuidv7()
        tb_fee_id = uuid_to_uint128(fee_uuid)

        # 9. Build the TB transfer: debit customer, credit fee income.
        tb_amount = int_to_uint128(req.amount)

        # UserData64 stores value_date unix nanos.
        if isinstance(value_date, date) and not isinstance(value_date, datetime):
            value_date = datetime(value_date.year, value_date.month, value_date.day)
        user_data_64 = int(value_date.timestamp() * 1_000_000_000)

        tb_transfer = {
            "id": tb_fee_id,
            "debit_account_id": customer_tb_id,
            "credit_account_id": fee_income_tb_id,
            "amount": tb_amount,
            "ledger": cur_info.ledger,
            "code": int(map_transfer_code("fee")),
            "user_data_128": tb_fee_id,  # correlation ID = transfer ID
            "user_data_64": user_data_64,
        }

        self._log.info(
            "charging_fee",
            transfer_id=str(fee_uuid),
            customer_account=req.customer_account_id,
            amount=req.amount,
            currency=req.currency,
            description=req.description,
        )

        # 10. Execute in TigerBeetle (TB-first dual-write).
        try:
            results = await self._tb_transfer_repo.create_transfers([tb_transfer])
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn(
                "fee_charge_failed",
                error=str(domain_err),
            )
            raise domain_err from exc

        # 11. Inspect results (M4 pattern — check results first).
        error = check_transfer_result(results, None)
        if error is not None:
            self._log.warn(
                "fee_charge_rejected",
                error=str(error),
            )
            raise error

        self._log.info(
            "fee_charged",
            transfer_id=str(fee_uuid),
        )

        # 12. Build response.
        fee_income_id_str = str(fee_income_uuid)

        value_date_str = ""
        if isinstance(value_date, datetime):
            value_date_str = value_date.strftime("%Y-%m-%d")
        elif isinstance(value_date, date):
            value_date_str = value_date.isoformat()

        response = FeeChargeResponse(
            id=str(fee_uuid),
            transfer_type="fee",
            debit_account_id=req.customer_account_id,
            credit_account_id=fee_income_id_str,
            amount=Balance(
                amount=req.amount, currency=req.currency, scale=cur_info.scale
            ),
            description=req.description,
            fee_schedule_ref=req.fee_schedule_ref,
            value_date=value_date_str,
            status="posted",
            created_at=datetime.now(),
        )

        # 13. Dual-write: PG transfer metadata (fire-and-forget — log errors but don't fail).
        await self._write_metadata(
            session,
            tb_fee_id,
            meta.id if meta else 0,
            fee_income_id_str,
            req.description,
            req.fee_schedule_ref,
            value_date,
        )

        return response

    # -- private helpers --------------------------------------------------

    async def _get_account_metadata(
        self, session: "AsyncSession", account_id: str
    ) -> object | None:
        """Fetch PG account metadata and validate the account is active.

        Raises:
            ValidationError: If *account_id* is not a valid UUID format.
            ErrInvalidAccount: If the account does not exist in PG.
            ErrAccountClosed: If the account is closed.
            ErrAccountFrozen: If the account is frozen.
        """
        acct_uuid = _uuid.UUID(account_id)

        try:
            meta = await self._account_meta_repo.get_by_tb_account_id(
                session, acct_uuid.bytes
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise ErrInvalidAccount from exc
            self._log.error(
                "failed_to_get_account_metadata",
                account_id=account_id,
                error=str(exc),
            )
            raise RuntimeError(f"get account: {exc}") from exc

        if meta is None:
            raise ErrInvalidAccount

        if meta.status == "closed":
            raise ErrAccountClosed
        if meta.status == "frozen":
            raise ErrAccountFrozen

        return meta

    async def _write_metadata(
        self,
        session: "AsyncSession",
        tb_transfer_id: bytes,
        account_id: int,
        counterparty: str,
        description: str,
        reference: str,
        value_date,
    ) -> None:
        """Write PG transfer metadata (fire-and-forget).

        Errors are logged but do not cause the fee charge to fail.
        """
        if self._metadata_writer is None:
            return

        try:
            if isinstance(value_date, datetime):
                vd = value_date.date()
            elif isinstance(value_date, date):
                vd = value_date
            else:
                vd = date.today()

            rec = TransferMetadataRecord(
                tb_transfer_id=tb_transfer_id,
                tb_correlation=None,  # fee charge is its own correlation
                account_id=account_id,
                counterparty=counterparty,
                description=description if description else None,
                reference=reference if reference else None,
                value_date=vd,
            )
            await self._metadata_writer.create_transfer_metadata(session, rec)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed_to_write_fee_metadata",
                error=str(exc),
            )


# -- factory -------------------------------------------------------------

def NewFeeService(
    tb_transfer_repo,
    tb_account_repo,
    account_meta_repo,
    system_account_repo,
    metadata_writer,
    logger=None,
) -> FeeService:
    """Create a new FeeService instance.

    Mirrors the Go constructor pattern for consistency across ports.
    """
    return FeeService(
        tb_transfer_repo=tb_transfer_repo,
        tb_account_repo=tb_account_repo,
        account_meta_repo=account_meta_repo,
        system_account_repo=system_account_repo,
        metadata_writer=metadata_writer,
        logger=logger,
    )
