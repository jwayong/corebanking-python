"""Transfer service — business logic for transfer operations.

Mirrors corebanking/internal/service/transfer_service.go with dual-write
(TB first, then PG) semantics.

Focus areas:
    - execute(): financial transfer with TB-first dual-write
    - get(): lookup a transfer by ID from TigerBeetle

Lower-priority methods (ListTransactions, GenerateStatement, GetDetail)
are deferred.
"""

from __future__ import annotations

import structlog
from datetime import date, datetime
from typing import TYPE_CHECKING
import uuid as _uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.accounts import (
    AccountCode,
    Balance,
    _is_valid_uuid,
)
from cbs.domain.currency import ledger_to_currency, lookup_currency
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrAccountFrozen,
    ErrInvalidAccount,
    ErrNotFound,
    ErrServiceUnavailable,
    ValidationError,
)
from cbs.domain.transfers import (
    TransferRequest,
    TransferResponse,
    map_transfer_code,
    transfer_code_to_string,
)
from cbs.service.errors import check_transfer_result, map_tb_error
from cbs.store.postgres.audit_repo import TransferMetadataRecord
from cbs.util.tb_types import int_to_uint128, uint128_to_int
from cbs.util.uuid import (
    generate_uuidv7,
    tb_id_to_uuid,
    uint128_to_uuid,
    uuid_to_uint128,
)

log = structlog.get_logger()


class TransferService:
    """Handles transfer orchestration with TB-first dual-write.

    Idempotency is handled by middleware, not at the service layer.
    This service focuses on: account resolution -> validation -> TB execution
    -> PG metadata write.
    """

    def __init__(
        self,
        tb_transfer_repo,  # mypy: disable-error-code="empty-body"
        tb_account_repo,  # mypy: disable-error-code="empty-body"
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
        self._log = (logger or log).bind(component="transfer_service")

    # -- public methods ---------------------------------------------------

    async def execute(
        self, session: "AsyncSession", req: TransferRequest
    ) -> TransferResponse:
        """Execute a financial transfer (TB-first dual-write).

        Supports 'transfer', 'deposit', and 'withdrawal' types.
        Idempotency is handled by the middleware layer; this service only
        performs the execution.

        Raises:
            ValidationError: If request fields are invalid.
            ErrInvalidAccount: If debit or credit account does not exist.
            ErrAccountClosed / ErrAccountFrozen: If an account is inactive.
            TransferError: If TB rejects the transfer (e.g., insufficient balance).

        Returns:
            ``TransferResponse`` with status 'posted'.
        """
        req.validate()

        # 1. Resolve full debit and credit account IDs based on transfer type.
        debit_id, credit_id, meta = await self._resolve_accounts(session, req)

        # 2. Parse value date (defaults to now).
        value_date = datetime.now().replace(microsecond=0, tzinfo=None)
        if req.value_date:
            value_date = date.fromisoformat(req.value_date)

        # 3. Look up currency info for ledger and scale.
        try:
            cur_info = lookup_currency(req.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # 4. Generate UUIDv7 transfer ID and convert to TB uint128.
        transfer_uuid = generate_uuidv7()
        tb_transfer_id = uuid_to_uint128(transfer_uuid)

        # 5. Map transfer type to TB code.
        tx_code = map_transfer_code(req.transfer_type)

        # 6. Convert account UUIDs to TB uint128.
        # System accounts resolved from PG are raw bytes (big-endian UUID).
        # Customer account IDs from request are UUID strings.
        if isinstance(debit_id, bytes):
            debit_uuid = tb_id_to_uuid(debit_id)
        else:
            debit_uuid = _uuid.UUID(debit_id)
        if isinstance(credit_id, bytes):
            credit_uuid = tb_id_to_uuid(credit_id)
        else:
            credit_uuid = _uuid.UUID(credit_id)
        debit_tb_id = uuid_to_uint128(debit_uuid)
        credit_tb_id = uuid_to_uint128(credit_uuid)

        # 7. Batch lookup both accounts in TB — validate existence and same ledger.
        try:
            tb_map = await self._tb_account_repo.lookup_accounts([debit_tb_id, credit_tb_id])
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "account lookup failed",
                error=str(exc),
            )
            raise ErrServiceUnavailable from exc

        debit_acct = tb_map.get(debit_tb_id)
        credit_acct = tb_map.get(credit_tb_id)
        if debit_acct is None:
            raise ErrInvalidAccount
        if credit_acct is None:
            raise ErrInvalidAccount

        # Both accounts must share the same ledger.
        if debit_acct.get("ledger") != credit_acct.get("ledger"):
            ledger_msg = (
                f"debit and credit accounts must be on the same ledger: "
                f"debit={debit_acct.get('ledger')}, credit={credit_acct.get('ledger')}"
            )
            raise ValidationError(ledger_msg)

        # 8. Build the TB transfer dict.
        tb_amount = int_to_uint128(req.amount)

        # UserData64 stores value_date unix nanos.
        if isinstance(value_date, date) and not isinstance(value_date, datetime):
            value_date = datetime(value_date.year, value_date.month, value_date.day)
        user_data_64 = int(value_date.timestamp() * 1_000_000_000)

        tb_transfer = {
            "id": tb_transfer_id,
            "debit_account_id": debit_tb_id,
            "credit_account_id": credit_tb_id,
            "amount": tb_amount,
            "ledger": cur_info.ledger,
            "code": int(tx_code),
            "user_data_128": tb_transfer_id,  # correlation ID = transfer ID
            "user_data_64": user_data_64,
        }

        self._log.info(
            "executing_transfer",
            transfer_id=str(transfer_uuid),
            transfer_type=req.transfer_type,
            debit_account=debit_id if isinstance(debit_id, str) else str(tb_id_to_uuid(debit_id)),
            credit_account=credit_id if isinstance(credit_id, str) else str(tb_id_to_uuid(credit_id)),
            amount=req.amount,
            currency=req.currency,
        )

        # 9. Execute in TigerBeetle (TB-first dual-write).
        # Python TB repo raises ValueError on failure.
        try:
            results = await self._tb_transfer_repo.create_transfers([tb_transfer])
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn(
                "transfer_failed",
                error=str(domain_err),
            )
            raise domain_err from exc

        # 10. Inspect results (M4 pattern — check results first).
        error = check_transfer_result(results, None)
        if error is not None:
            self._log.warn(
                "transfer_rejected",
                error=str(error),
            )
            raise error

        self._log.info(
            "transfer_created",
            transfer_id=str(transfer_uuid),
        )

        # 11. Build response.
        debit_id_str = debit_id if isinstance(debit_id, str) else str(tb_id_to_uuid(debit_id))
        credit_id_str = credit_id if isinstance(credit_id, str) else str(tb_id_to_uuid(credit_id))

        response = self._build_response(
            transfer_uuid,
            debit_id_str,
            credit_id_str,
            req,
            value_date,
        )

        # 12. Dual-write: PG transfer metadata (fire-and-forget — log errors but don't fail).
        await self._write_metadata(
            session,
            tb_transfer_id,
            meta.id if meta else 0,
            credit_id_str,
            req.description,
            req.reference,
            value_date,
        )

        return response

    async def get(self, session: "AsyncSession", transfer_id: str) -> TransferResponse:
        """Retrieve a transfer by its UUID from TigerBeetle.

        Raises:
            ValidationError: If *transfer_id* is not a valid UUID format.
            ErrNotFound: If the transfer does not exist in TigerBeetle.

        Returns:
            ``TransferResponse`` with status 'posted'.
        """
        if not transfer_id:
            raise ValidationError("transfer id is required")
        if not _is_valid_uuid(transfer_id):
            raise ValidationError("invalid transfer id format")

        # 1. Convert UUID to TB uint128 and lookup.
        transfer_uuid = _uuid.UUID(transfer_id)
        tb_id = uuid_to_uint128(transfer_uuid)

        try:
            tb_transfer = await self._tb_transfer_repo.lookup_transfer(tb_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "lookup_transfer_failed",
                id=transfer_id,
                error=str(exc),
            )
            raise RuntimeError(f"lookup transfer: {exc}") from exc

        if tb_transfer is None:
            raise ErrNotFound

        # 2. Extract amount from transfer data (big-endian uint128 -> int).
        # TB stores amounts as 16-byte little-endian Uint128; low 8 bytes hold the value.
        amount_bytes = tb_transfer.get("amount", b"\x00" * 16)
        amount = uint128_to_int(amount_bytes)

        # 3. Convert TB account IDs back to UUID strings.
        # TB returns account IDs as little-endian Uint128 bytes; uint128_to_uuid
        # reverses each 8-byte half to restore big-endian UUID byte order.
        debit_uuid = uint128_to_uuid(tb_transfer.get("debit_account_id", b"\x00" * 16))
        credit_uuid = uint128_to_uuid(tb_transfer.get("credit_account_id", b"\x00" * 16))

        # 4. Derive currency from ledger and transfer type from code.
        cur_code = ledger_to_currency(tb_transfer.get("ledger", 0)) or "UNKNOWN"
        scale = _currency_scale_from_ledger(tb_transfer.get("ledger", 0))
        transfer_type = transfer_code_to_string(tb_transfer.get("code", 0))

        # 5. Value date from UserData64 (unix nanos).
        user_data_64 = int(tb_transfer.get("user_data_64", 0))
        value_date = datetime.fromtimestamp(user_data_64 / 1_000_000_000)

        return TransferResponse(
            id=transfer_id,
            transfer_type=transfer_type,
            debit_account_id=str(debit_uuid),
            credit_account_id=str(credit_uuid),
            amount=Balance(amount=int(amount), currency=cur_code, scale=scale),
            value_date=value_date.strftime("%Y-%m-%d"),
            status="posted",
            created_at=value_date,  # approximation: TB lacks creation timestamp
        )

    # -- private helpers --------------------------------------------------

    async def _resolve_accounts(
        self, session: "AsyncSession", req: TransferRequest
    ) -> tuple[str | bytes, str | bytes, object]:
        """Resolve full debit and credit account IDs from a TransferRequest.

        For 'transfer': uses explicit debit_account_id and credit_account_id.
        For 'deposit': resolves Cash Vault as debit, customer account as credit.
        For 'withdrawal': customer account as debit, resolves Cash Vault as credit.

        Returns:
            Tuple of (debit_id, credit_id, account_meta). debit_id and credit_id
            are either UUID strings or raw bytes (for system accounts resolved
            from PG). account_meta is the AccountWithProduct for the customer
            account, or None if not applicable.

        Raises:
            ValidationError / ErrInvalidAccount / ErrAccountClosed / ErrAccountFrozen.
        """
        match req.transfer_type:
            case "deposit":
                return await self._resolve_deposit(session, req)
            case "withdrawal":
                return await self._resolve_withdrawal(session, req)
            case _:
                return await self._resolve_explicit(session, req)

    async def _resolve_deposit(
        self, session: "AsyncSession", req: TransferRequest
    ) -> tuple[str | bytes, str | bytes, object]:
        """Resolve a deposit transfer: Cash Vault (debit) -> customer account (credit)."""
        # Determine customer account ID.
        customer_id = req.credit_account_id or req.customer_account_id
        if not customer_id:
            raise ValidationError("credit_account_id is required for deposit")

        # Look up Cash Vault system account from PG.
        cash_vault_bytes = await self._system_account_repo.get_by_code(
            session, req.currency, int(AccountCode.CASH_VAULT)
        )
        if cash_vault_bytes is None:
            raise ErrInvalidAccount

        # Validate customer account exists and is active in PG.
        meta = await self._get_account_metadata(session, customer_id)

        # Validate both accounts exist in TB and share a ledger.
        debit_tb_id = uuid_to_uint128(tb_id_to_uuid(cash_vault_bytes))
        cust_tb_id = uuid_to_uint128(_uuid.UUID(customer_id))

        try:
            tb_map = await self._tb_account_repo.lookup_accounts([debit_tb_id, cust_tb_id])
        except Exception as exc:  # noqa: BLE001
            self._log.error("lookup_accounts_failed", error=str(exc))
            raise ErrServiceUnavailable from exc

        debit_acct = tb_map.get(debit_tb_id)
        cust_acct = tb_map.get(cust_tb_id)
        if debit_acct is None:
            raise ErrInvalidAccount
        if cust_acct is None:
            raise ErrInvalidAccount

        if debit_acct.get("ledger") != cust_acct.get("ledger"):
            raise ValidationError(
                "cash vault and customer account must be on the same ledger"
            )

        return cash_vault_bytes, customer_id, meta

    async def _resolve_withdrawal(
        self, session: "AsyncSession", req: TransferRequest
    ) -> tuple[str | bytes, str | bytes, object]:
        """Resolve a withdrawal transfer: customer account (debit) -> Cash Vault (credit)."""
        # Determine customer account ID.
        customer_id = req.debit_account_id or req.customer_account_id
        if not customer_id:
            raise ValidationError("debit_account_id is required for withdrawal")

        # Validate customer account exists and is active in PG.
        meta = await self._get_account_metadata(session, customer_id)

        # Look up Cash Vault system account from PG.
        cash_vault_bytes = await self._system_account_repo.get_by_code(
            session, req.currency, int(AccountCode.CASH_VAULT)
        )
        if cash_vault_bytes is None:
            raise ErrInvalidAccount

        # Validate both accounts exist in TB and share a ledger.
        debit_tb_id = uuid_to_uint128(_uuid.UUID(customer_id))
        credit_tb_id = uuid_to_uint128(tb_id_to_uuid(cash_vault_bytes))

        try:
            tb_map = await self._tb_account_repo.lookup_accounts([debit_tb_id, credit_tb_id])
        except Exception as exc:  # noqa: BLE001
            self._log.error("lookup_accounts_failed", error=str(exc))
            raise ErrServiceUnavailable from exc

        debit_acct = tb_map.get(debit_tb_id)
        credit_acct = tb_map.get(credit_tb_id)
        if debit_acct is None:
            raise ErrInvalidAccount
        if credit_acct is None:
            raise ErrInvalidAccount

        if debit_acct.get("ledger") != credit_acct.get("ledger"):
            raise ValidationError(
                "customer account and cash vault must be on the same ledger"
            )

        return customer_id, cash_vault_bytes, meta

    async def _resolve_explicit(
        self, session: "AsyncSession", req: TransferRequest
    ) -> tuple[str | bytes, str | bytes, object]:
        """Resolve a standard transfer with explicit debit and credit accounts."""
        debit_id = req.debit_account_id
        credit_id = req.credit_account_id

        # Validate both accounts via PG metadata.
        meta = await self._get_account_metadata(session, debit_id)
        if not await self._get_account_metadata(session, credit_id):
            raise ErrInvalidAccount

        # Validate both accounts exist in TB and share a ledger.
        debit_tb_id = uuid_to_uint128(_uuid.UUID(debit_id))
        credit_tb_id = uuid_to_uint128(_uuid.UUID(credit_id))

        try:
            tb_map = await self._tb_account_repo.lookup_accounts([debit_tb_id, credit_tb_id])
        except Exception as exc:  # noqa: BLE001
            self._log.error("lookup_accounts_failed", error=str(exc))
            raise ErrServiceUnavailable from exc

        debit_acct = tb_map.get(debit_tb_id)
        credit_acct = tb_map.get(credit_tb_id)
        if debit_acct is None:
            raise ErrInvalidAccount
        if credit_acct is None:
            raise ErrInvalidAccount

        if debit_acct.get("ledger") != credit_acct.get("ledger"):
            raise ValidationError(
                "debit and credit accounts must be on the same ledger"
            )

        return debit_id, credit_id, meta

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
        if not _is_valid_uuid(account_id):
            raise ValidationError("account id must be a valid UUID")

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

    @staticmethod
    def _build_response(
        transfer_uuid,
        debit_id: str,
        credit_id: str,
        req: TransferRequest,
        value_date,
    ) -> TransferResponse:
        """Assemble the TransferResponse from request data and generated IDs."""
        cur_info = lookup_currency(req.currency)

        value_date_str = ""
        if isinstance(value_date, datetime):
            value_date_str = value_date.strftime("%Y-%m-%d")
        elif isinstance(value_date, date):
            value_date_str = value_date.isoformat()

        return TransferResponse(
            id=str(transfer_uuid),
            transfer_type=req.transfer_type,
            debit_account_id=debit_id,
            credit_account_id=credit_id,
            amount=Balance(amount=req.amount, currency=req.currency, scale=cur_info.scale),
            reference=req.reference,
            description=req.description,
            value_date=value_date_str,
            status="posted",
            created_at=datetime.now(),
        )

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

        Errors are logged but do not cause the transfer to fail.
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
                tb_correlation=None,  # single-leg: correlation = transfer ID (handled by caller if needed)
                account_id=account_id,
                counterparty=counterparty,
                description=description if description else None,
                reference=reference if reference else None,
                value_date=vd,
            )
            await self._metadata_writer.create_transfer_metadata(session, rec)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed_to_write_transfer_metadata",
                error=str(exc),
            )


# -- module-level helpers ------------------------------------------------

def _currency_scale_from_ledger(ledger: int) -> int:
    """Return the scale for a given ledger number.

    Mirrors currency.currency_scale_from_ledger but avoids importing
    the full module at runtime (already imported at top level).
    """
    from cbs.domain.currency import currency_scale_from_ledger as _csfl
    return _csfl(ledger)


# -- factory -------------------------------------------------------------

def NewTransferService(
    tb_transfer_repo,
    tb_account_repo,
    account_meta_repo,
    system_account_repo,
    metadata_writer,
    logger=None,
) -> TransferService:
    """Create a new TransferService instance.

    Mirrors the Go constructor pattern for consistency across ports.
    """
    return TransferService(
        tb_transfer_repo=tb_transfer_repo,
        tb_account_repo=tb_account_repo,
        account_meta_repo=account_meta_repo,
        system_account_repo=system_account_repo,
        metadata_writer=metadata_writer,
        logger=logger,
    )
