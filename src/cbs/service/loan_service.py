"""Loan service — business logic for loan disbursement and repayment operations.

Mirrors corebanking/internal/service/loan_service.go with dual-write
(TB first, then PG) semantics.

Focus areas:
    - disburse(): loan account → customer deposit (single TB transfer)
    - repay(): customer deposit → loan account + reduce outstanding
    - repay_with_fee(): three-leg linked repayment (principal, interest, fee)

All operations follow TB-first / PG-second write order.
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
    ErrAccountFrozen,
    ErrInvalidAccount,
    ErrLiquidityPoolUnavailable,
    ErrNotFound,
    ErrRepaymentExceedsOutstanding,
    ErrServiceUnavailable,
    ValidationError,
)
from cbs.domain.loans import (
    LoanDisbursementRequest,
    LoanDisbursementResponse,
    LoanRepaymentRequest,
    LoanRepaymentResponse,
    LoanRepaymentWithFeeRequest,
    LoanRepaymentWithFeeResponse,
    RepayWithFeeLeg,
)
from cbs.domain.transfers import (
    TransferCode,
    map_transfer_code,
)
from cbs.service.errors import find_linked_root_cause, map_tb_error
from cbs.store.postgres.audit_repo import TransferMetadataRecord
from cbs.util.tb_types import uint64_to_uint128
from cbs.util.uuid import (
    generate_uuidv7,
    tb_id_to_uuid,
    uint128_to_uuid,
    uuid_to_uint128,
)

log = structlog.get_logger()

# TB transfer flag: Linked — set on all legs except the last.
_TB_LINKED_FLAG = 0x10


class LoanService:
    """Handles loan disbursement and repayment transfers.

    Orchestrates TB-first / PG-second dual-write for all loan operations.
    For repay_with_fee, uses TigerBeetle's linked transfer mechanism to
    execute up to three legs atomically.
    """

    def __init__(
        self,
        tb_transfer_repo,  # mypy: disable-error-code="empty-body"
        tb_account_repo,  # mypy: disable-error-code="empty-body"
        account_meta_repo,  # mypy: disable-error-code="empty-body"
        system_account_repo,  # mypy: disable-error-code="empty-body"
        metadata_writer,  # mypy: disable-error-code="empty-body"
        loan_repo,  # mypy: disable-error-code="empty-body"
        logger=None,
    ) -> None:
        self._tb_transfer_repo = tb_transfer_repo
        self._tb_account_repo = tb_account_repo
        self._account_meta_repo = account_meta_repo
        self._system_account_repo = system_account_repo
        self._metadata_writer = metadata_writer
        self._loan_repo = loan_repo
        self._log = (logger or log).bind(component="loan_service")

    # -- public methods ---------------------------------------------------

    async def disburse(
        self, session: "AsyncSession", req: LoanDisbursementRequest
    ) -> LoanDisbursementResponse:
        """Transfer funds from a loan account to a customer deposit account.

        Executes the TB transfer first, then updates the disbursed_at
        timestamp in PostgreSQL. PG failures are logged but non-fatal.

        Raises:
            ValidationError: If request fields are invalid.
            ErrInvalidAccount: If loan or credit account does not exist.
            ErrAccountClosed / ErrAccountFrozen: If an account is inactive.
            TransferError: If TB rejects the transfer (e.g., insufficient balance).

        Returns:
            ``LoanDisbursementResponse`` with status 'posted'.
        """
        req.validate()

        # 1. Parse account UUIDs and convert to TB uint128.
        loan_uuid = _uuid.UUID(req.loan_account_id)
        credit_uuid = _uuid.UUID(req.credit_account_id)
        loan_tb_id = uuid_to_uint128(loan_uuid)
        credit_tb_id = uuid_to_uint128(credit_uuid)

        # 2. Lookup currency info for ledger and scale.
        try:
            cur_info = lookup_currency(req.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # 3. Validate accounts: exist in TB, active in PG, correct categories.
        loan_meta = await self._validate_loan_accounts(
            session, loan_tb_id, credit_tb_id, cur_info.ledger
        )

        # 4. Parse value date (defaults to now).
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

        # 5. Generate UUIDv7 for the disbursement transfer ID.
        disburse_uuid = generate_uuidv7()
        tb_disburse_id = uuid_to_uint128(disburse_uuid)

        # 6. Build the TB transfer: debit loan account, credit customer deposit.
        value_date_nanos = int(value_date.timestamp() * 1_000_000_000)
        tb_transfer = {
            "id": tb_disburse_id,
            "debit_account_id": loan_tb_id,
            "credit_account_id": credit_tb_id,
            "amount": uint64_to_uint128(req.amount),
            "ledger": cur_info.ledger,
            "code": int(map_transfer_code("disbursement")),
            "user_data_128": tb_disburse_id,  # correlation ID = transfer ID
            "user_data_64": value_date_nanos,
        }

        self._log.info(
            "disbursing_loan",
            transfer_id=str(disburse_uuid),
            loan_account=req.loan_account_id,
            credit_account=req.credit_account_id,
            amount=req.amount,
            currency=req.currency,
        )

        # 7. Execute in TigerBeetle (TB-first dual-write).
        try:
            results = await self._tb_transfer_repo.create_transfers([tb_transfer])
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn("disbursement failed", error=str(domain_err))
            raise domain_err from exc

        # 8. Inspect results (M4 pattern — check results first).
        error = self._check_single_result(results)
        if error is not None:
            self._log.warn(
                "disbursement rejected by TigerBeetle",
                error=str(error),
            )
            raise error

        now = datetime.now()

        # 9. PG second: set disbursed_at timestamp (non-fatal on error).
        try:
            await self._loan_repo.set_disbursed_at(session, loan_meta.id, now)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "set disbursed_at failed",
                account_id=loan_meta.id,
                error=str(exc),
            )

        # 10. Write PG metadata (fire-and-forget).
        if self._metadata_writer is not None:
            asyncio.create_task(
                self._write_disbursement_metadata(
                    session,
                    tb_disburse_id,
                    loan_meta.id,
                    req.credit_account_id,
                    req.amount,
                    req.reference,
                    value_date,
                )
            )

        # 11. Build response.
        value_date_str = value_date.strftime("%Y-%m-%d")

        return LoanDisbursementResponse(
            id=str(disburse_uuid),
            transfer_type="disbursement",
            loan_account_id=req.loan_account_id,
            credit_account_id=req.credit_account_id,
            amount=Balance(
                amount=req.amount, currency=req.currency, scale=cur_info.scale
            ),
            currency=req.currency,
            value_date=value_date_str,
            status="posted",
            created_at=now,
        )

    async def repay(
        self, session: "AsyncSession", req: LoanRepaymentRequest
    ) -> LoanRepaymentResponse:
        """Transfer funds from a customer deposit account to a loan account.

        Executes the TB transfer first, then reduces the outstanding balance
        in PostgreSQL. If repayment exceeds outstanding, returns an error.

        Raises:
            ValidationError: If request fields are invalid.
            ErrInvalidAccount: If debit or loan account does not exist.
            ErrAccountClosed / ErrAccountFrozen: If an account is inactive.
            ErrRepaymentExceedsOutstanding: If repayment > outstanding balance.
            TransferError: If TB rejects the transfer (e.g., insufficient balance).

        Returns:
            ``LoanRepaymentResponse`` with status 'posted'.
        """
        req.validate()

        # 1. Parse account UUIDs and convert to TB uint128.
        loan_uuid = _uuid.UUID(req.loan_account_id)
        debit_uuid = _uuid.UUID(req.debit_account_id)
        loan_tb_id = uuid_to_uint128(loan_uuid)
        debit_tb_id = uuid_to_uint128(debit_uuid)

        # 2. Lookup currency info for ledger and scale.
        try:
            cur_info = lookup_currency(req.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # 3. Validate accounts: exist in TB, active in PG, correct categories.
        loan_meta = await self._validate_loan_accounts(
            session, loan_tb_id, debit_tb_id, cur_info.ledger
        )

        # 4. Parse value date (defaults to now).
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

        # 5. Generate UUIDv7 for the repayment transfer ID.
        repay_uuid = generate_uuidv7()
        tb_repay_id = uuid_to_uint128(repay_uuid)

        # 6. Build the TB transfer: debit customer deposit, credit loan account.
        value_date_nanos = int(value_date.timestamp() * 1_000_000_000)
        tb_transfer = {
            "id": tb_repay_id,
            "debit_account_id": debit_tb_id,
            "credit_account_id": loan_tb_id,
            "amount": uint64_to_uint128(req.amount),
            "ledger": cur_info.ledger,
            "code": int(map_transfer_code("repayment")),
            "user_data_128": tb_repay_id,  # correlation ID = transfer ID
            "user_data_64": value_date_nanos,
        }

        self._log.info(
            "repaying_loan",
            transfer_id=str(repay_uuid),
            loan_account=req.loan_account_id,
            debit_account=req.debit_account_id,
            amount=req.amount,
            currency=req.currency,
        )

        # 7. Execute in TigerBeetle (TB-first dual-write).
        try:
            results = await self._tb_transfer_repo.create_transfers([tb_transfer])
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn("repayment failed", error=str(domain_err))
            raise domain_err from exc

        # 8. Inspect results (M4 pattern — check results first).
        error = self._check_single_result(results)
        if error is not None:
            self._log.warn(
                "repayment rejected by TigerBeetle",
                error=str(error),
            )
            raise error

        # 9. PG second: reduce outstanding balance.
        try:
            await self._loan_repo.reduce_outstanding(
                session, req.amount, loan_meta.id, value_date
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                self._log.warn(
                    "repayment exceeds outstanding balance",
                    transfer_id=str(repay_uuid),
                    amount=req.amount,
                )
                raise ErrRepaymentExceedsOutstanding from exc
            self._log.error("reduce outstanding failed", error=str(exc))
            raise ErrServiceUnavailable from exc

        now = datetime.now()

        # 10. Write PG metadata (fire-and-forget).
        if self._metadata_writer is not None:
            asyncio.create_task(
                self._write_repayment_metadata(
                    session,
                    tb_repay_id,
                    loan_meta.id,
                    req.debit_account_id,
                    req.amount,
                    req.reference,
                    value_date,
                )
            )

        # 11. Build response.
        value_date_str = value_date.strftime("%Y-%m-%d")

        return LoanRepaymentResponse(
            id=str(repay_uuid),
            transfer_type="repayment",
            debit_account_id=req.debit_account_id,
            loan_account_id=req.loan_account_id,
            amount=Balance(
                amount=req.amount, currency=req.currency, scale=cur_info.scale
            ),
            currency=req.currency,
            value_date=value_date_str,
            status="posted",
            created_at=now,
        )

    async def repay_with_fee(
        self, session: "AsyncSession", req: LoanRepaymentWithFeeRequest
    ) -> LoanRepaymentWithFeeResponse:
        """Execute a three-leg linked repayment (principal, interest, fee).

        Uses TigerBeetle's linked transfer mechanism for atomic execution:
        all legs succeed or all fail together. Legs with zero amounts are
        omitted from the batch.

        Raises:
            ValidationError: If request fields are invalid.
            ErrInvalidAccount: If debit or loan account does not exist.
            ErrAccountClosed / ErrAccountFrozen: If an account is inactive.
            ErrLiquidityPoolUnavailable: If system accounts are missing.
            ErrRepaymentExceedsOutstanding: If principal > outstanding balance.
            TransferError: If TB rejects the transfer batch.

        Returns:
            ``LoanRepaymentWithFeeResponse`` with legs list and status 'posted'.
        """
        req.validate()

        # 1. Parse account UUIDs and convert to TB uint128.
        loan_uuid = _uuid.UUID(req.loan_account_id)
        debit_uuid = _uuid.UUID(req.debit_account_id)
        loan_tb_id = uuid_to_uint128(loan_uuid)
        debit_tb_id = uuid_to_uint128(debit_uuid)

        # 2. Lookup currency info for ledger and scale.
        try:
            cur_info = lookup_currency(req.currency)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # 3. Validate accounts: exist in TB, active in PG, correct categories.
        loan_meta = await self._validate_loan_accounts(
            session, loan_tb_id, debit_tb_id, cur_info.ledger
        )

        # 4. Parse value date (defaults to now).
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

        # 5. Generate shared correlation ID for the batch.
        correlation_id = generate_uuidv7()
        corr_bytes = uuid_to_uint128(correlation_id)

        # 6. Build up to 3 transfers, only including legs with non-zero amounts.
        value_date_nanos = int(value_date.timestamp() * 1_000_000_000)
        transfers: list[dict] = []
        leg_infos: list[tuple[int, str, bytes]] = []  # (amount, code_str, credit_tb_id)

        # T1: Principal — debit customer deposit, credit loan account.
        if req.principal > 0:
            principal_uuid = generate_uuidv7()
            tb_principal_id = uuid_to_uint128(principal_uuid)

            transfers.append({
                "id": tb_principal_id,
                "debit_account_id": debit_tb_id,
                "credit_account_id": loan_tb_id,
                "amount": uint64_to_uint128(req.principal),
                "ledger": cur_info.ledger,
                "code": int(map_transfer_code("repayment")),
                "user_data_128": corr_bytes,
                "user_data_64": value_date_nanos,
                "flags": _TB_LINKED_FLAG,  # Linked — cleared on final leg below
            })
            leg_infos.append((req.principal, "repayment", loan_tb_id))

        # T2: Interest — debit customer deposit, credit interest income (4101).
        if req.interest_amount > 0:
            interest_uuid = generate_uuidv7()
            tb_interest_id = uuid_to_uint128(interest_uuid)

            interest_acct_bytes = await self._system_account_repo.get_by_code(
                session, req.currency, int(AccountCode.INC_INTEREST_LOAN)
            )
            if interest_acct_bytes is None:
                raise ErrLiquidityPoolUnavailable

            interest_tb_id = uuid_to_uint128(tb_id_to_uuid(interest_acct_bytes))

            transfers.append({
                "id": tb_interest_id,
                "debit_account_id": debit_tb_id,
                "credit_account_id": interest_tb_id,
                "amount": uint64_to_uint128(req.interest_amount),
                "ledger": cur_info.ledger,
                "code": int(TransferCode.INTEREST_CREDIT),
                "user_data_128": corr_bytes,
                "user_data_64": value_date_nanos,
                "flags": _TB_LINKED_FLAG,  # Linked — cleared on final leg below
            })
            leg_infos.append((req.interest_amount, "interest", interest_tb_id))

        # T3: Fee — debit customer deposit, credit fee income (4110).
        if req.fee_amount > 0:
            fee_uuid = generate_uuidv7()
            tb_fee_id = uuid_to_uint128(fee_uuid)

            fee_acct_bytes = await self._system_account_repo.get_by_code(
                session, req.currency, int(AccountCode.INC_FEE_SERVICE)
            )
            if fee_acct_bytes is None:
                raise ErrLiquidityPoolUnavailable

            fee_tb_id = uuid_to_uint128(tb_id_to_uuid(fee_acct_bytes))

            transfers.append({
                "id": tb_fee_id,
                "debit_account_id": debit_tb_id,
                "credit_account_id": fee_tb_id,
                "amount": uint64_to_uint128(req.fee_amount),
                "ledger": cur_info.ledger,
                "code": int(TransferCode.FEE),
                "user_data_128": corr_bytes,
                "user_data_64": value_date_nanos,
                "flags": _TB_LINKED_FLAG,  # Linked — cleared on final leg below
            })
            leg_infos.append((req.fee_amount, "fee", fee_tb_id))

        # 7. Clear Linked flag on final leg to commit the batch.
        if transfers:
            transfers[-1]["flags"] = 0

        self._log.info(
            "repaying_loan_with_fee",
            correlation=str(correlation_id),
            loan_account=req.loan_account_id,
            debit_account=req.debit_account_id,
            principal=req.principal,
            interest=req.interest_amount,
            fee=req.fee_amount,
            currency=req.currency,
        )

        # 8. Execute atomically via TB linked transfers.
        try:
            results = await self._tb_transfer_repo.create_transfers(transfers)
        except ValueError as exc:
            domain_err = map_tb_error(exc)
            self._log.warn(
                "repay-with-fee failed",
                correlation=str(correlation_id),
                error=str(domain_err),
            )
            raise domain_err from exc

        # 9. Map errors using linked root cause analysis.
        root_cause = find_linked_root_cause(results)
        if root_cause is not None:
            self._log.warn(
                "repay-with-fee failed",
                correlation=str(correlation_id),
                error=str(root_cause),
            )
            raise root_cause

        # 10. PG second: reduce outstanding balance (principal only).
        try:
            await self._loan_repo.reduce_outstanding(
                session, req.principal, loan_meta.id, value_date
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                self._log.warn(
                    "repayment exceeds outstanding balance",
                    correlation=str(correlation_id),
                    principal=req.principal,
                )
                raise ErrRepaymentExceedsOutstanding from exc
            self._log.error("reduce outstanding failed", error=str(exc))
            raise ErrServiceUnavailable from exc

        now = datetime.now()

        # 11. Write PG metadata for each leg (fire-and-forget).
        if self._metadata_writer is not None:
            asyncio.create_task(
                self._write_repay_with_fee_metadata(
                    session,
                    transfers,
                    corr_bytes,
                    loan_meta.id,
                    req.debit_account_id,
                    leg_infos,
                    req.reference,
                    value_date,
                )
            )

        # 12. Build response legs.
        debit_id_str = req.debit_account_id
        legs: list[RepayWithFeeLeg] = []
        for i, t in enumerate(transfers):
            leg_uuid = uint128_to_uuid(t["id"])
            credit_acct_uuid = uint128_to_uuid(leg_infos[i][2])

            legs.append(RepayWithFeeLeg(
                id=str(leg_uuid),
                debit_account_id=debit_id_str,
                credit_account_id=str(credit_acct_uuid),
                amount=Balance(
                    amount=leg_infos[i][0],
                    currency=req.currency,
                    scale=cur_info.scale,
                ),
                code=leg_infos[i][1],
            ))

        # 13. Build response with conditional Balance fields.
        value_date_str = value_date.strftime("%Y-%m-%d")

        resp = LoanRepaymentWithFeeResponse(
            id=str(correlation_id),
            transfer_type="repay_with_fee",
            legs=legs,
            loan_account_id=req.loan_account_id,
            debit_account_id=req.debit_account_id,
            principal=(
                Balance(amount=req.principal, currency=req.currency, scale=cur_info.scale)
                if req.principal > 0
                else None
            ),
            interest=(
                Balance(amount=req.interest_amount, currency=req.currency, scale=cur_info.scale)
                if req.interest_amount > 0
                else None
            ),
            fee=(
                Balance(amount=req.fee_amount, currency=req.currency, scale=cur_info.scale)
                if req.fee_amount > 0
                else None
            ),
            currency=req.currency,
            value_date=value_date_str,
            status="posted",
            created_at=now,
        )

        return resp

    # -- private helpers --------------------------------------------------

    async def _validate_loan_accounts(
        self,
        session: "AsyncSession",
        loan_tb_id: bytes,
        deposit_tb_id: bytes,
        ledger: int,
    ) -> object:
        """Validate both loan and deposit accounts in TB + PG.

        Checks existence, closed flag, ledger match in TB. Validates
        PG metadata: loan must be category='loan', deposit must be
        category='deposit'. Both must have status 'active'.

        Returns:
            The loan account PG metadata record (for downstream PG operations).

        Raises:
            ErrServiceUnavailable: If TB or PG lookup fails.
            ErrInvalidAccount: If accounts do not exist, are closed, or have wrong category.
            ErrAccountClosed / ErrAccountFrozen: If an account is inactive in PG.
        """
        # Batch lookup in TB — check existence, closed flag, ledger match.
        try:
            tb_map = await self._tb_account_repo.lookup_accounts(
                [loan_tb_id, deposit_tb_id]
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error("account lookup failed", error=str(exc))
            raise ErrServiceUnavailable from exc

        for tb_id in [loan_tb_id, deposit_tb_id]:
            acct = tb_map.get(tb_id)
            if acct is None:
                raise ErrInvalidAccount
            # Check closed flag in TB account flags.
            if acct.get("closed", False):
                raise ErrAccountClosed
            # Check ledger match.
            if acct.get("ledger") != ledger:
                raise ErrInvalidAccount

        # PG metadata lookup — validate status and category.
        loan_uuid = uint128_to_uuid(loan_tb_id)
        deposit_uuid = uint128_to_uuid(deposit_tb_id)

        loan_meta = await self._get_account_meta(session, loan_uuid.bytes)
        if loan_meta is None:
            raise ErrInvalidAccount

        deposit_meta = await self._get_account_meta(session, deposit_uuid.bytes)
        if deposit_meta is None:
            raise ErrInvalidAccount

        # Validate PG status.
        if loan_meta.status == "closed":
            raise ErrAccountClosed
        if loan_meta.status == "frozen":
            raise ErrAccountFrozen
        if deposit_meta.status == "closed":
            raise ErrAccountClosed
        if deposit_meta.status == "frozen":
            raise ErrAccountFrozen

        # Validate product categories.
        if loan_meta.category != "loan":
            raise ValidationError("loan account must be a loan product")
        if deposit_meta.category != "deposit":
            raise ValidationError("credit account must be a deposit product")

        return loan_meta

    async def _get_account_meta(
        self, session: "AsyncSession", tb_account_id_bytes: bytes
    ) -> object | None:
        """Fetch PG account metadata by TB account ID bytes.

        Returns the AccountWithProduct record, or None if not found.
        """
        try:
            meta = await self._account_meta_repo.get_by_tb_account_id(
                session, tb_account_id_bytes
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                return None
            self._log.error(
                "account metadata lookup failed",
                error=str(exc),
            )
            raise ErrServiceUnavailable from exc

        if meta is None:
            return None

        # Check for non-active, non-closed, non-frozen status.
        if meta.status not in ("active", "closed", "frozen"):
            raise ErrInvalidAccount

        return meta

    @staticmethod
    def _check_single_result(results: list[dict]) -> Exception | None:
        """Check a single-transfer result and return domain error if failed.

        Args:
            results: List of result dicts from TB create call (single transfer).

        Returns:
            Domain error if the transfer failed, None on success.
        """
        from cbs.service.errors import check_transfer_result as _ctr

        return _ctr(results, None)

    async def _write_disbursement_metadata(
        self,
        session: "AsyncSession",
        tb_transfer_id: bytes,
        account_id: int,
        counterparty: str,
        amount: int,
        reference: str,
        value_date: datetime,
    ) -> None:
        """Write disbursement metadata to PG for audit trail (fire-and-forget)."""
        if self._metadata_writer is None:
            return

        try:
            vd = value_date.date() if isinstance(value_date, datetime) else date.today()

            rec = TransferMetadataRecord(
                tb_transfer_id=bytes(tb_transfer_id),
                tb_correlation=None,  # disbursement is its own correlation
                account_id=account_id,
                counterparty=counterparty,
                description="disbursement",
                reference=reference if reference else None,
                value_date=vd,
            )
            await self._metadata_writer.create_transfer_metadata(session, rec)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "write disbursement metadata failed",
                transfer_id=tb_transfer_id.hex if isinstance(tb_transfer_id, bytes) else str(tb_transfer_id),
                error=str(exc),
            )

    async def _write_repayment_metadata(
        self,
        session: "AsyncSession",
        tb_transfer_id: bytes,
        account_id: int,
        counterparty: str,
        amount: int,
        reference: str,
        value_date: datetime,
    ) -> None:
        """Write repayment metadata to PG for audit trail (fire-and-forget)."""
        if self._metadata_writer is None:
            return

        try:
            vd = value_date.date() if isinstance(value_date, datetime) else date.today()

            rec = TransferMetadataRecord(
                tb_transfer_id=bytes(tb_transfer_id),
                tb_correlation=None,  # repayment is its own correlation
                account_id=account_id,
                counterparty=counterparty,
                description="repayment",
                reference=reference if reference else None,
                value_date=vd,
            )
            await self._metadata_writer.create_transfer_metadata(session, rec)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "write repayment metadata failed",
                transfer_id=tb_transfer_id.hex if isinstance(tb_transfer_id, bytes) else str(tb_transfer_id),
                error=str(exc),
            )

    async def _write_repay_with_fee_metadata(
        self,
        session: "AsyncSession",
        transfers: list[dict],
        corr_bytes: bytes,
        account_id: int,
        counterparty: str,
        leg_infos: list[tuple[int, str, bytes]],  # (amount, code_str, credit_tb_id)
        reference: str,
        value_date: datetime,
    ) -> None:
        """Write repay-with-fee metadata for each leg (fire-and-forget)."""
        if self._metadata_writer is None:
            return

        corr_slice = bytes(corr_bytes)

        for i, t in enumerate(transfers):
            try:
                vd = value_date.date() if isinstance(value_date, datetime) else date.today()

                rec = TransferMetadataRecord(
                    tb_transfer_id=bytes(t.get("id", b"")),
                    tb_correlation=corr_slice,
                    account_id=account_id,
                    counterparty=counterparty,
                    description=leg_infos[i][1],  # "repayment", "interest", or "fee"
                    reference=reference if reference else None,
                    value_date=vd,
                )
                await self._metadata_writer.create_transfer_metadata(session, rec)
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "repay-with-fee metadata write failed",
                    leg=i,
                    error=str(exc),
                )


# -- factory --------------------------------------------------------------

def NewLoanService(
    tb_transfer_repo,
    tb_account_repo,
    account_meta_repo,
    system_account_repo,
    metadata_writer,
    loan_repo,
    logger=None,
) -> LoanService:
    """Create a new LoanService instance.

    Mirrors the Go constructor pattern for consistency across ports.
    """
    return LoanService(
        tb_transfer_repo=tb_transfer_repo,
        tb_account_repo=tb_account_repo,
        account_meta_repo=account_meta_repo,
        system_account_repo=system_account_repo,
        metadata_writer=metadata_writer,
        loan_repo=loan_repo,
        logger=logger,
    )
