"""Hold service — two-phase transfer operations (hold, capture, void).

Mirrors corebanking/internal/service/hold_service.go.

Orchestrates pending holds (phase 1), captures (phase 2a), and voids
(phase 2b) against TigerBeetle with PostgreSQL metadata writes for audit.
"""

from __future__ import annotations

import structlog
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import uuid as _uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.accounts import Balance
from cbs.domain.currency import currency_scale_from_ledger, ledger_to_currency, lookup_currency
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrAccountFrozen,
    ErrInvalidAccount,
    ErrNotFound,
    ErrServiceUnavailable,
    ValidationError,
)
from cbs.domain.transfers import (
    CaptureRequest,
    HoldRequest,
    HoldResponse,
    _is_valid_uuid,
    HOLD_STATUS_CAPTURED,
    HOLD_STATUS_PENDING,
    HOLD_STATUS_VOIDED,
    map_transfer_code,
)
from cbs.service.errors import check_hold_result, map_tb_error
from cbs.store.postgres.audit_repo import TransferMetadataRecord
from cbs.util.tb_types import int_to_uint128, uint128_to_int
from cbs.util.uuid import (
    generate_uuidv7,
    uint128_to_uuid,
    uuid_to_uint128,
)

log = structlog.get_logger()


class HoldService:
    """Handles two-phase transfers: hold, capture, void.

    Delegates persistence to TigerBeetle and PostgreSQL repos, validates
    inputs, and orchestrates the TB-first / PG-second write order.
    """

    def __init__(
        self,
        tb_transfer_repo,  # mypy: disable-error-code="empty-body"
        tb_account_repo,  # mypy: disable-error-code="empty-body"
        account_meta_repo,  # mypy: disable-error-code="empty-body"
        metadata_writer,  # mypy: disable-error-code="empty-body"
        logger=None,
    ) -> None:
        self._tb_transfer_repo = tb_transfer_repo
        self._tb_account_repo = tb_account_repo
        self._account_meta_repo = account_meta_repo
        self._metadata_writer = metadata_writer
        self._log = (logger or log).bind(component="hold_service")

    # -- public methods ---------------------------------------------------

    async def create(
        self, session: "AsyncSession", req: HoldRequest
    ) -> HoldResponse:
        """Create a pending hold (two-phase transfer, phase 1).

        Funds are reserved as ``debits_pending`` on the debit account.
        The hold must later be captured or voided to resolve.

        Raises:
            ValidationError: If request fields are invalid.
            ErrInvalidAccount: If debit or credit account does not exist.
            ErrAccountClosed / ErrAccountFrozen: If an account is inactive.
            TransferError: If TB rejects the hold (e.g., insufficient balance).

        Returns:
            ``HoldResponse`` with status 'pending' and expires_at set.
        """
        req.validate()

        # 1. Parse debit/credit UUIDs and convert to TB uint128.
        debit_uuid = _uuid_parse(req.debit_account_id)
        credit_uuid = _uuid_parse(req.credit_account_id)
        debit_tb_id = uuid_to_uint128(debit_uuid)
        credit_tb_id = uuid_to_uint128(credit_uuid)

        # 2. Lookup currency for ledger and scale.
        try:
            cur_info = lookup_currency(req.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # 3. Validate both accounts: TB existence + ledger, then PG active status.
        debit_meta = await self._validate_accounts(
            session, debit_tb_id, credit_tb_id, cur_info.ledger
        )

        # 4. Generate UUIDv7 for the hold transfer ID.
        hold_uuid = generate_uuidv7()
        tb_hold_id = uuid_to_uint128(hold_uuid)

        # 5. Build the pending transfer dict.
        tb_transfer = {
            "id": tb_hold_id,
            "debit_account_id": debit_tb_id,
            "credit_account_id": credit_tb_id,
            "amount": int_to_uint128(req.amount),
            "ledger": cur_info.ledger,
            "code": int(map_transfer_code("hold")),
            "flags": 0x02,  # Pending — two-phase, reserves funds
            "pending_id": b"\x00" * 16,  # not applicable for phase 1
            "timeout": req.timeout_seconds,
            "user_data_64": int(datetime.now().timestamp() * 1_000_000_000),
            "user_data_128": tb_hold_id,  # correlation ID = hold transfer ID
        }

        self._log.info(
            "creating_hold",
            hold_id=str(hold_uuid),
            debit_account=req.debit_account_id,
            credit_account=req.credit_account_id,
            amount=req.amount,
            currency=req.currency,
            timeout_seconds=req.timeout_seconds,
        )

        # 6. Execute in TigerBeetle.
        try:
            results = await self._tb_transfer_repo.create_transfers([tb_transfer])
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn("hold_creation_failed", error=str(domain_err))
            raise domain_err from exc

        # 7. Inspect results (M4 pattern — check results first).
        error = check_hold_result(results, None)
        if error is not None:
            self._log.warn("hold_rejected", error=str(error))
            raise error

        # 8. Compute expires_at.
        now = datetime.now()
        expires_at = now + timedelta(seconds=req.timeout_seconds)

        # 9. Write PG metadata (fire-and-forget — log errors but don't fail).
        await self._write_hold_metadata(
            session,
            tb_hold_id,
            debit_meta.id if debit_meta else 0,
            req.credit_account_id,
            req.amount,
            req.reference,
            now,
        )

        return HoldResponse(
            id=str(hold_uuid),
            transfer_type="hold",
            debit_account_id=req.debit_account_id,
            credit_account_id=req.credit_account_id,
            amount=Balance(
                amount=req.amount, currency=req.currency, scale=cur_info.scale
            ),
            status=HOLD_STATUS_PENDING,
            expires_at=expires_at,
            reference=req.reference,
            created_at=now,
        )

    async def capture(
        self, session: "AsyncSession", hold_id: str, req: CaptureRequest
    ) -> HoldResponse:
        """Capture a pending hold (two-phase transfer, phase 2a).

        Posts the reserved funds. If *req.amount* is zero, captures the full
        hold amount. Otherwise, performs a partial capture (must not exceed
        the original hold).

        Raises:
            ValidationError: If request fields or hold_id are invalid.
            ErrNotFound: If the original hold does not exist.

        Returns:
            ``HoldResponse`` with status 'captured'.
        """
        req.validate()

        if not _is_valid_uuid(hold_id):
            raise ValidationError("invalid hold id format")

        # 1. Lookup original hold in TB.
        hold_uuid = _uuid_parse(hold_id)
        tb_hold_id = uuid_to_uint128(hold_uuid)

        try:
            tb_hold = await self._tb_transfer_repo.lookup_transfer(tb_hold_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "lookup_hold_failed",
                hold_id=hold_id,
                error=str(exc),
            )
            raise RuntimeError(f"lookup hold: {exc}") from exc

        if tb_hold is None:
            raise ErrNotFound

        # 2. Extract hold amount from TB transfer.
        hold_amount = uint128_to_int(tb_hold.get("amount", b"\x00" * 16))

        # 3. Determine capture amount: default to full hold amount.
        capture_amount = req.amount if req.amount > 0 else hold_amount
        if capture_amount <= 0:
            raise ValidationError("capture amount must be positive")
        if capture_amount > hold_amount:
            raise ValidationError("capture amount exceeds hold amount")

        # 4. Generate UUIDv7 capture ID.
        capture_uuid = generate_uuidv7()
        tb_capture_id = uuid_to_uint128(capture_uuid)

        # 5. Build the capture transfer — reuse original hold's accounts and ledger.
        tb_transfer = {
            "id": tb_capture_id,
            "debit_account_id": tb_hold.get("debit_account_id", b"\x00" * 16),
            "credit_account_id": tb_hold.get("credit_account_id", b"\x00" * 16),
            "amount": int_to_uint128(capture_amount),
            "ledger": tb_hold.get("ledger", 0),
            "code": int(map_transfer_code("capture")),
            "flags": 0x04,  # PostPendingTransfer — posts the pending hold
            "pending_id": tb_hold_id,
            "user_data_64": int(datetime.now().timestamp() * 1_000_000_000),
            "user_data_128": tb_capture_id,  # correlation ID = capture transfer ID
        }

        self._log.info(
            "capturing_hold",
            capture_id=str(capture_uuid),
            hold_id=hold_id,
            amount=capture_amount,
        )

        # 6. Execute in TigerBeetle.
        try:
            results = await self._tb_transfer_repo.create_transfers([tb_transfer])
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn("capture_failed", error=str(domain_err))
            raise domain_err from exc

        # 7. Inspect results (M4 pattern).
        error = check_hold_result(results, None)
        if error is not None:
            self._log.warn("capture_rejected", error=str(error))
            raise error

        # 8. Convert TB account IDs back to UUID strings.
        debit_uuid = uint128_to_uuid(
            tb_hold.get("debit_account_id", b"\x00" * 16)
        )
        credit_uuid = uint128_to_uuid(
            tb_hold.get("credit_account_id", b"\x00" * 16)
        )

        # 9. Derive currency from ledger.
        cur_code = ledger_to_currency(tb_hold.get("ledger", 0)) or "UNKNOWN"
        scale = currency_scale_from_ledger(tb_hold.get("ledger", 0))

        # 10. Lookup debit account PG metadata for AccountID (best-effort).
        try:
            debit_meta = await self._account_meta_repo.get_by_tb_account_id(
                session, bytes(tb_hold.get("debit_account_id", b"\x00" * 16))
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "capture_debit_meta_lookup_failed",
                error=str(exc),
            )
            debit_meta = None

        # 11. Write PG metadata for capture (fire-and-forget).
        await self._write_capture_metadata(
            session,
            tb_capture_id,
            tb_hold_id,
            debit_meta.id if debit_meta else 0,
            capture_amount,
        )

        return HoldResponse(
            id=str(capture_uuid),
            transfer_type="capture",
            debit_account_id=str(debit_uuid),
            credit_account_id=str(credit_uuid),
            amount=Balance(amount=capture_amount, currency=cur_code, scale=scale),
            status=HOLD_STATUS_CAPTURED,
            created_at=datetime.now(),
        )

    async def void(
        self, session: "AsyncSession", hold_id: str
    ) -> HoldResponse:
        """Void a pending hold (two-phase transfer, phase 2b).

        Releases the reserved funds back to the debit account.

        Raises:
            ValidationError: If *hold_id* is not a valid UUID format.
            ErrNotFound: If the original hold does not exist.

        Returns:
            ``HoldResponse`` with status 'voided'.
        """
        if not _is_valid_uuid(hold_id):
            raise ValidationError("invalid hold id format")

        # 1. Lookup original hold in TB.
        hold_uuid = _uuid_parse(hold_id)
        tb_hold_id = uuid_to_uint128(hold_uuid)

        try:
            tb_hold = await self._tb_transfer_repo.lookup_transfer(tb_hold_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "lookup_hold_failed",
                hold_id=hold_id,
                error=str(exc),
            )
            raise RuntimeError(f"lookup hold: {exc}") from exc

        if tb_hold is None:
            raise ErrNotFound

        # 2. Generate UUIDv7 void ID.
        void_uuid = generate_uuidv7()
        tb_void_id = uuid_to_uint128(void_uuid)

        # 3. Build the void transfer — zero amount, references original hold.
        tb_transfer = {
            "id": tb_void_id,
            "debit_account_id": tb_hold.get("debit_account_id", b"\x00" * 16),
            "credit_account_id": tb_hold.get("credit_account_id", b"\x00" * 16),
            "amount": b"\x00" * 16,  # Zero amount for void
            "ledger": tb_hold.get("ledger", 0),
            "code": int(map_transfer_code("void")),
            "flags": 0x08,  # VoidPendingTransfer — voids the pending hold
            "pending_id": tb_hold_id,
            "user_data_64": int(datetime.now().timestamp() * 1_000_000_000),
            "user_data_128": tb_void_id,  # correlation ID = void transfer ID
        }

        self._log.info(
            "voiding_hold",
            void_id=str(void_uuid),
            hold_id=hold_id,
        )

        # 4. Execute in TigerBeetle.
        try:
            results = await self._tb_transfer_repo.create_transfers([tb_transfer])
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn("void_failed", error=str(domain_err))
            raise domain_err from exc

        # 5. Inspect results (M4 pattern).
        error = check_hold_result(results, None)
        if error is not None:
            self._log.warn("void_rejected", error=str(error))
            raise error

        # 6. Convert TB account IDs back to UUID strings.
        debit_uuid = uint128_to_uuid(
            tb_hold.get("debit_account_id", b"\x00" * 16)
        )
        credit_uuid = uint128_to_uuid(
            tb_hold.get("credit_account_id", b"\x00" * 16)
        )

        # 7. Derive currency from ledger and extract original hold amount.
        cur_code = ledger_to_currency(tb_hold.get("ledger", 0)) or "UNKNOWN"
        scale = currency_scale_from_ledger(tb_hold.get("ledger", 0))
        hold_amount = uint128_to_int(tb_hold.get("amount", b"\x00" * 16))

        # 8. Lookup debit account PG metadata for AccountID (best-effort).
        try:
            debit_meta = await self._account_meta_repo.get_by_tb_account_id(
                session, bytes(tb_hold.get("debit_account_id", b"\x00" * 16))
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "void_debit_meta_lookup_failed",
                error=str(exc),
            )
            debit_meta = None

        # 9. Write PG metadata for void (fire-and-forget).
        await self._write_void_metadata(
            session,
            tb_void_id,
            tb_hold_id,
            debit_meta.id if debit_meta else 0,
            hold_amount,
        )

        return HoldResponse(
            id=str(void_uuid),
            transfer_type="void",
            debit_account_id=str(debit_uuid),
            credit_account_id=str(credit_uuid),
            amount=Balance(amount=hold_amount, currency=cur_code, scale=scale),
            status=HOLD_STATUS_VOIDED,
            created_at=datetime.now(),
        )

    # -- private helpers --------------------------------------------------

    async def _validate_accounts(
        self,
        session: "AsyncSession",
        debit_tb_id: bytes,
        credit_tb_id: bytes,
        ledger: int,
    ) -> object | None:
        """Validate both accounts exist, are active, and share the same ledger.

        Performs a batch TB lookup followed by individual PG metadata checks.
        Returns the debit account PG metadata on success so callers can use its
        primary key for audit writes.

        Raises:
            ErrServiceUnavailable: If TB or PG lookup fails.
            ErrInvalidAccount: If an account does not exist or ledger mismatch.
            ErrAccountClosed / ErrAccountFrozen: If PG status is inactive.
        """
        # Batch TB lookup for both accounts.
        try:
            tb_map = await self._tb_account_repo.lookup_accounts(
                [debit_tb_id, credit_tb_id]
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error("account_lookup_failed", error=str(exc))
            raise ErrServiceUnavailable from exc

        for tb_id in (debit_tb_id, credit_tb_id):
            acct = tb_map.get(tb_id)
            if acct is None:
                raise ErrInvalidAccount
            if acct.get("account_flags", 0) & 0x08:  # Closed flag
                raise ErrAccountClosed
            if acct.get("ledger") != ledger:
                raise ErrInvalidAccount

        # PG metadata lookup for both accounts — check active status.
        debit_meta = None
        for i, tb_id in enumerate((debit_tb_id, credit_tb_id)):
            try:
                meta = await self._account_meta_repo.get_by_tb_account_id(
                    session, bytes(tb_id)
                )
            except Exception as exc:  # noqa: BLE001
                if exc is ErrNotFound:
                    raise ErrInvalidAccount from exc
                self._log.error(
                    "account_metadata_lookup_failed",
                    error=str(exc),
                )
                raise ErrServiceUnavailable from exc

            if meta is None:
                raise ErrInvalidAccount

            if meta.status == "closed":
                raise ErrAccountClosed
            if meta.status == "frozen":
                raise ErrAccountFrozen

            if i == 0:
                debit_meta = meta  # capture debit metadata for callers

        return debit_meta

    async def _write_hold_metadata(
        self,
        session: "AsyncSession",
        tb_id: bytes,
        account_id: int,
        counterparty: str,
        amount: int,
        reference: str,
        created_at: datetime,
    ) -> None:
        """Write hold metadata to PG for audit trail (fire-and-forget)."""
        if self._metadata_writer is None:
            return

        try:
            rec = TransferMetadataRecord(
                tb_transfer_id=tb_id,
                tb_correlation=None,  # hold is its own correlation
                account_id=account_id,
                counterparty=counterparty if counterparty else None,
                description="hold",
                reference=reference if reference else None,
                value_date=created_at.date(),
            )
            await self._metadata_writer.create_transfer_metadata(session, rec)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "write_hold_metadata_failed",
                hold_id=tb_id.hex if isinstance(tb_id, bytes) else str(tb_id),
                error=str(exc),
            )

    async def _write_capture_metadata(
        self,
        session: "AsyncSession",
        tb_id: bytes,
        hold_id: bytes,
        account_id: int,
        amount: int,
    ) -> None:
        """Write capture metadata to PG (fire-and-forget)."""
        if self._metadata_writer is None:
            return

        try:
            hold_uuid_str = str(uint128_to_uuid(hold_id))
            correlation = hold_id
            rec = TransferMetadataRecord(
                tb_transfer_id=tb_id,
                tb_correlation=correlation,
                account_id=account_id,
                description="capture",
                reference=f"capture of hold {hold_uuid_str}",
                value_date=datetime.now().date(),
            )
            await self._metadata_writer.create_transfer_metadata(session, rec)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "write_capture_metadata_failed",
                capture_id=tb_id.hex if isinstance(tb_id, bytes) else str(tb_id),
                error=str(exc),
            )

    async def _write_void_metadata(
        self,
        session: "AsyncSession",
        tb_id: bytes,
        hold_id: bytes,
        account_id: int,
        amount: int,
    ) -> None:
        """Write void metadata to PG (fire-and-forget)."""
        if self._metadata_writer is None:
            return

        try:
            hold_uuid_str = str(uint128_to_uuid(hold_id))
            correlation = hold_id
            rec = TransferMetadataRecord(
                tb_transfer_id=tb_id,
                tb_correlation=correlation,
                account_id=account_id,
                description="void",
                reference=f"void of hold {hold_uuid_str}",
                value_date=datetime.now().date(),
            )
            await self._metadata_writer.create_transfer_metadata(session, rec)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "write_void_metadata_failed",
                void_id=tb_id.hex if isinstance(tb_id, bytes) else str(tb_id),
                error=str(exc),
            )


# -- module-level helpers ------------------------------------------------

def _uuid_parse(s: str) -> "_uuid.UUID":
    """Parse a UUID string, raising ValidationError on failure."""
    try:
        return _uuid.UUID(s)
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"invalid uuid format: {s}") from exc


# -- factory -------------------------------------------------------------

def NewHoldService(
    tb_transfer_repo,
    tb_account_repo,
    account_meta_repo,
    metadata_writer,
    logger=None,
) -> HoldService:
    """Create a new HoldService instance.

    Mirrors the Go constructor pattern for consistency across ports.
    """
    return HoldService(
        tb_transfer_repo=tb_transfer_repo,
        tb_account_repo=tb_account_repo,
        account_meta_repo=account_meta_repo,
        metadata_writer=metadata_writer,
        logger=logger,
    )
