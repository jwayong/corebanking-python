"""Account service — business logic for account operations.

Mirrors corebanking/internal/service/account_service.go with dual-write
(TB first, then PG) semantics.
"""

from __future__ import annotations

import structlog
from datetime import date, datetime
from typing import TYPE_CHECKING
import uuid as _uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.accounts import (
    AccountListResponse,
    AccountOwner,
    AccountResponse,
    AccountSummary,
    Balance,
    CloseAccountResponse,
    ComputeBalanceResult,
    CreateAccountRequest,
    _is_valid_uuid,
    compute_balance,
)
from cbs.domain.currency import lookup_currency
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrNotFound,
    ErrNonZeroBalance,
    ErrPendingHolds,
    ErrProductInactive,
    ValidationError,
)
from cbs.domain.loans import (
    LoanDetails,
    calculate_emi,
    calculate_maturity_date,
    calculate_next_payment_due,
)
from cbs.store.postgres.account_repo import (
    AccountRecord,
    CustomerAccountRecord,
)
from cbs.store.postgres.loan_repo import LoanDetailRecord

log = structlog.get_logger()

default_page_limit = 50
max_page_limit = 500


class AccountService:
    """Handles account business logic with dual-write (TB first, then PG).

    Delegates persistence to TigerBeetle and PostgreSQL repos, validates
    inputs, and orchestrates the TB-first / PG-second write order.
    """

    def __init__(
        self,
        tb_repo,  # mypy: disable-error-code="empty-body"
        pg_repo,  # mypy: disable-error-code="empty-body"
        product_repo,  # mypy: disable-error-code="empty-body"
        loan_repo,  # mypy: disable-error-code="empty-body"
        customer_service,  # mypy: disable-error-code="empty-body"
        logger=None,
    ) -> None:
        self._tb_repo = tb_repo
        self._pg_repo = pg_repo
        self._product_repo = product_repo
        self._loan_repo = loan_repo
        self._customer_service = customer_service
        self._log = (logger or log).bind(component="account_service")

    # -- public methods ---------------------------------------------------

    async def create(
        self, session: "AsyncSession", req: CreateAccountRequest
    ) -> AccountResponse:
        """Create a new deposit or loan account with dual-write (TB-first, PG-second).

        For loan accounts also creates the ``loan_details`` row.  Disbursement
        is handled separately via ``LoanService.disburse()``.

        Raises:
            ValidationError: If request fields, product, or customer are invalid.
            ErrProductInactive: If the referenced product is not active.

        Returns:
            ``AccountResponse`` with zero balance (disbursement sets principal).
        """
        req.validate()

        # 1. Validate product exists and is active.
        try:
            product = await self._product_repo.get_by_code(session, req.product_code)
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise ValidationError(
                    f'product "{req.product_code}" not found'
                ) from exc
            self._log.error(
                "failed to get product",
                product_code=req.product_code,
                error=str(exc),
            )
            raise RuntimeError(f"get product: {exc}") from exc

        if not product.is_active:
            raise ErrProductInactive

        # 2. Validate customer exists (via CustomerService).
        try:
            customer = await self._customer_service.get(session, req.customer_ref)
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise ValidationError(
                    f'customer "{req.customer_ref}" not found'
                ) from exc
            self._log.error(
                "failed to get customer",
                customer_ref=req.customer_ref,
                error=str(exc),
            )
            raise RuntimeError(f"get customer: {exc}") from exc

        # 3. Validate loan-specific requirements.
        is_loan = product.category == "loan"
        if is_loan and req.loan is None:
            raise ValidationError("loan field is required for loan products")
        if not is_loan and req.loan is not None:
            raise ValidationError("loan field is only valid for loan products")

        # 4. Generate account number with sequence.
        try:
            cur = lookup_currency(product.currency)
        except ValueError as exc:
            raise RuntimeError(f"lookup currency: {exc}") from exc

        prefix = f"{product.currency}-{product.tb_account_code}"
        try:
            seq = await self._pg_repo.next_account_sequence(session, prefix)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to get next account sequence",
                prefix=prefix,
                error=str(exc),
            )
            raise RuntimeError(f"next account sequence: {exc}") from exc

        account_number = f"{prefix}-{seq:05d}"

        # 5. Generate UUIDv7 for TB account ID.
        from cbs.util.uuid import generate_uuidv7, uuid_to_uint128

        acct_uuid = generate_uuidv7()
        tb_id = uuid_to_uint128(acct_uuid)

        # 6. Determine TB flags based on account code.
        from cbs.store.tigerbeetle.account_repo import AccountRepo as _AccountRepo

        flags = _AccountRepo.build_account_flags(product.tb_account_code)

        # 7. Create TigerBeetle account (TB-first dual-write).
        tb_account = {
            "id": tb_id,
            "ledger": product.tb_ledger,
            "code": product.tb_account_code,
            "flags": flags,
        }

        try:
            await self._tb_repo.create_account(tb_account)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to create TB account",
                account_id=str(acct_uuid),
                error=str(exc),
            )
            raise RuntimeError(f"create tb account: {exc}") from exc

        # 8. Create PG account metadata (second write).
        try:
            pg_rec = await self._pg_repo.create(
                session,
                AccountRecord(
                    tb_account_id=acct_uuid.bytes,
                    product_id=product.id,
                    account_number=account_number,
                    status="active",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to create PG account — orphan TB account",
                account_id=str(acct_uuid),
                tb_account_id=acct_uuid.hex,
                error=str(exc),
            )
            raise RuntimeError(f"create pg account: {exc}") from exc

        # 9. Create customer-account relationship.
        try:
            await self._pg_repo.create_customer_account(
                session,
                CustomerAccountRecord(
                    customer_ref=req.customer_ref,
                    account_id=pg_rec.id,
                    ownership_type=req.ownership_type,
                    role="owner",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to create customer-account link",
                error=str(exc),
            )
            raise RuntimeError(f"create customer account: {exc}") from exc

        # 10. For loan accounts: create loan_details row.
        loan_details: LoanDetails | None = None
        if is_loan and req.loan is not None:
            try:
                loan_details = await self._create_loan_details(
                    session, pg_rec.id, req, product
                )
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "failed to create loan details",
                    account_id=pg_rec.id,
                    error=str(exc),
                )
                raise RuntimeError(f"create loan details: {exc}") from exc

        self._log.info(
            "account_created",
            account_id=str(acct_uuid),
            account_number=account_number,
            product=product.code,
            category=product.category,
            customer_ref=req.customer_ref,
        )

        # 11. Build response — zero balance; disbursement handled separately.
        return self._build_create_response(
            acct_uuid,
            account_number,
            product,
            cur,
            customer,
            pg_rec.opened_at,  # type: ignore[arg-type]  # RETURNING guarantees non-None
            loan_details,
            req,
        )

    async def get(
        self, session: "AsyncSession", id: str
    ) -> AccountResponse:
        """Retrieve an account by its UUID, merging PG metadata with live TB balance.

        Raises:
            ValidationError: If *id* is empty or not a valid UUID format.
            ErrNotFound: If the account does not exist in PostgreSQL.

        Returns:
            ``AccountResponse`` with computed balance and owner list.
        """
        if not id:
            raise ValidationError("account id is required")
        if not _is_valid_uuid(id):
            raise ValidationError("account id must be a valid UUID")

        # Parse UUID to bytes for PG lookup.
        acct_uuid = _uuid.UUID(id)

        # 1. PG: fetch account metadata + product info.
        try:
            pg_acct = await self._pg_repo.get_by_tb_account_id(
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

        # 2. TB: lookup account for cumulative fields.
        from cbs.util.uuid import uuid_to_uint128

        tb_id = uuid_to_uint128(acct_uuid)
        try:
            tb_acct = await self._tb_repo.lookup_account(tb_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to lookup TB account",
                account_id=id,
                error=str(exc),
            )
            raise RuntimeError(f"lookup tb account: {exc}") from exc

        # 3. Compute balance from cumulative fields based on account code.
        if tb_acct is None:
            self._log.warn(
                "TB account not found for active PG account",
                account_id=id,
            )

        bal = _compute_balance_from_tb(tb_acct, pg_acct.tb_account_code)

        # 4. Fetch currency info for scale.
        try:
            cur = lookup_currency(pg_acct.currency)
        except ValueError as exc:
            raise RuntimeError(f"lookup currency: {exc}") from exc

        # 5. Fetch owners.
        try:
            owner_recs = await self._pg_repo.get_owners_by_account_id(
                session, pg_acct.id
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to get account owners",
                account_id=id,
                error=str(exc),
            )
            raise RuntimeError(f"get account owners: {exc}") from exc

        owners = [
            AccountOwner(
                customer_ref=o.customer_ref,
                name=o.name,
                ownership_type=o.ownership_type,
                role=o.role,
            )
            for o in owner_recs
        ]

        # 6. Fetch loan details if applicable.
        loan_details: LoanDetails | None = None
        if pg_acct.category == "loan":
            try:
                loan_rec = await self._loan_repo.get_by_account_id(session, pg_acct.id)
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "failed to get loan details",
                    account_id=id,
                    error=str(exc),
                )
                raise RuntimeError(f"get loan details: {exc}") from exc

            if loan_rec is not None:
                loan_details = _loan_record_to_details(loan_rec)

        # 7. Build response.
        is_loan = pg_acct.category == "loan"
        balance = Balance(amount=bal.posted, currency=pg_acct.currency, scale=cur.scale)

        if is_loan:
            available_balance = Balance(
                amount=0, currency=pg_acct.currency, scale=cur.scale
            )
        else:
            available_balance = Balance(
                amount=bal.available, currency=pg_acct.currency, scale=cur.scale
            )

        return AccountResponse(
            id=id,
            account_number=pg_acct.account_number,
            product_code=pg_acct.product_code,
            category=pg_acct.category,
            currency=pg_acct.currency,
            scale=cur.scale,
            status=pg_acct.status,
            balance=balance,
            available_balance=available_balance,
            owners=owners,
            opened_at=pg_acct.opened_at,  # type: ignore[arg-type]  # always set for active accounts
            loan_details=loan_details,
        )

    async def list(
        self, session: "AsyncSession", customer_ref: str, cursor: str = "", limit: int = 0
    ) -> AccountListResponse:
        """Return accounts for a customer with live balances, cursor-based pagination.

        Fetches ``limit + 1`` rows from PG so the caller can detect whether
        more pages exist.  Batch-looks up TB accounts for live balances.

        Raises:
            ValidationError: If *customer_ref* is empty, not a valid UUID, or
                *cursor* is malformed.

        Returns:
            ``AccountListResponse`` with summaries, next_cursor, and has_more.
        """
        if not customer_ref:
            raise ValidationError("customer_ref is required")
        if not _is_valid_uuid(customer_ref):
            raise ValidationError("customer_ref must be a valid UUID")

        if limit <= 0:
            limit = default_page_limit
        if limit > max_page_limit:
            limit = max_page_limit

        # Parse cursor (PG account ID) for pagination.
        cursor_id = 0
        if cursor:
            try:
                cursor_id = int(cursor)
            except ValueError:
                raise ValidationError("cursor must be a valid integer")

        # 1. PG: list accounts for customer with cursor-based pagination.
        try:
            pg_accounts = await self._pg_repo.list_by_customer_ref(
                session, customer_ref, cursor_id, limit
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to list accounts by customer",
                customer_ref=customer_ref,
                error=str(exc),
            )
            raise RuntimeError(f"list accounts: {exc}") from exc

        # Determine pagination: we fetched limit+1, so if more than limit, there's a next page.
        has_more = len(pg_accounts) > limit
        if has_more:
            pg_accounts = pg_accounts[:limit]

        if not pg_accounts:
            return AccountListResponse(data=[])

        # 2. TB: batch lookup all account IDs for live balances.
        from cbs.util.uuid import tb_id_to_uuid, uuid_to_uint128

        tb_ids = []
        for acct in pg_accounts:
            u = tb_id_to_uuid(acct.tb_account_id)
            tb_ids.append(uuid_to_uint128(u))

        try:
            tb_map = await self._tb_repo.lookup_accounts(tb_ids)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to batch lookup TB accounts",
                count=len(tb_ids),
                error=str(exc),
            )
            raise RuntimeError(f"lookup tb accounts: {exc}") from exc

        # 3. Build summaries with computed balances.
        summaries: list[AccountSummary] = []
        for acct in pg_accounts:
            u = tb_id_to_uuid(acct.tb_account_id)
            acct_uuid_str = str(u)

            bal = _compute_balance_from_tb_map(
                tb_map, acct.tb_account_id, acct.tb_account_code
            )

            try:
                cur = lookup_currency(acct.currency)
            except ValueError as exc:
                raise RuntimeError(f"lookup currency: {exc}") from exc

            is_loan = acct.category == "loan"
            balance = Balance(
                amount=bal.posted, currency=acct.currency, scale=cur.scale
            )

            if is_loan:
                available_balance = Balance(
                    amount=0, currency=acct.currency, scale=cur.scale
                )
            else:
                available_balance = Balance(
                    amount=bal.available, currency=acct.currency, scale=cur.scale
                )

            opened_at = ""
            if acct.opened_at is not None:
                opened_at = acct.opened_at.isoformat()

            summaries.append(
                AccountSummary(
                    id=acct_uuid_str,
                    account_number=acct.account_number,
                    product_code=acct.product_code,
                    category=acct.category,
                    currency=acct.currency,
                    scale=cur.scale,
                    status=acct.status,
                    balance=balance,
                    available_balance=available_balance,
                    opened_at=opened_at,
                )
            )

        # 4. Build next cursor from the last account's PG ID.
        next_cursor = ""
        if has_more:
            next_cursor = str(pg_accounts[-1].id)

        return AccountListResponse(
            data=summaries,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def close(
        self, session: "AsyncSession", id: str
    ) -> CloseAccountResponse:
        """Close an account after verifying zero balance and no pending holds.

        Raises:
            ValidationError: If *id* is not a valid UUID format.
            ErrNotFound: If the account does not exist.
            ErrAccountClosed: If already closed.
            ErrPendingHolds: If pending holds exist.
            ErrNonZeroBalance: If posted balance is non-zero.

        Returns:
            ``CloseAccountResponse`` with closed_at timestamp.
        """
        if not _is_valid_uuid(id):
            raise ValidationError("account id must be a valid UUID")

        tb_account_id = _uuid.UUID(id)

        # 1. Get PG account metadata.
        try:
            pg_acct = await self._pg_repo.get_by_tb_account_id(
                session, tb_account_id.bytes
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrNotFound:
                raise
            self._log.error(
                "failed to get account for close",
                id=id,
                error=str(exc),
            )
            raise RuntimeError(f"close account: {exc}") from exc

        if pg_acct is None:
            raise ErrNotFound

        if pg_acct.status == "closed":
            raise ErrAccountClosed

        # 2. Lookup TB account for balance check.
        from cbs.util.uuid import uuid_to_uint128

        tb_id = uuid_to_uint128(tb_account_id)
        try:
            tb_acct = await self._tb_repo.lookup_account(tb_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to lookup TB account for close",
                id=id,
                error=str(exc),
            )
            raise RuntimeError(f"close account tb lookup: {exc}") from exc

        if tb_acct is None:
            self._log.warn(
                "TB account not found for active PG account during close",
                id=id,
            )
            raise ErrNotFound

        # 3. Reject if pending holds exist.
        dpnd = _uint128_to_int(tb_acct.get("debits_pending", b"\x00" * 16))
        cpnd = _uint128_to_int(tb_acct.get("credits_pending", b"\x00" * 16))
        if dpnd > 0 or cpnd > 0:
            raise ErrPendingHolds

        # 4. Reject if non-zero balance (debits != credits means non-zero).
        dp = _uint128_to_int(tb_acct.get("debits_posted", b"\x00" * 16))
        cp = _uint128_to_int(tb_acct.get("credits_posted", b"\x00" * 16))
        if dp != cp:
            raise ErrNonZeroBalance

        # 5. Update PG: status='closed', closed_at=NOW().
        try:
            closed_at = await self._pg_repo.close_account(session, pg_acct.id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to close account in PG",
                id=id,
                error=str(exc),
            )
            raise RuntimeError(f"close account pg: {exc}") from exc

        if closed_at is None:
            # Race condition — account was already closed between our check and update.
            raise ErrAccountClosed

        return CloseAccountResponse(
            id=id,
            status="closed",
            closed_at=closed_at,
        )

    # -- private helpers --------------------------------------------------

    async def _create_loan_details(
        self,
        session: "AsyncSession",
        account_id: int,
        req: CreateAccountRequest,
        product,  # mypy: disable-error-code="empty-body"
    ) -> LoanDetails:
        """Create the loan_details row for a new loan account.

        Disbursement is handled separately via LoanService.disburse().
        """
        if product.interest_rate is None:
            raise ValidationError("product interest_rate is not configured")

        today = date.today()

        # Calculate EMI / payment_amount.
        payment_amount = calculate_emi(
            req.loan.principal, product.interest_rate, req.loan.term_months
        )

        # Calculate dates.
        maturity_date = calculate_maturity_date(today, req.loan.term_months)
        next_payment_due = calculate_next_payment_due(today)

        try:
            loan_rec = await self._loan_repo.create(
                session,
                LoanDetailRecord(
                    account_id=account_id,
                    principal=req.loan.principal,
                    outstanding=req.loan.principal,
                    interest_rate=product.interest_rate,
                    term_months=req.loan.term_months,
                    disbursed_at=None,  # set by LoanService.Disburse()
                    maturity_date=maturity_date,
                    next_payment_due=next_payment_due,
                    payment_amount=payment_amount,
                    status="active",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to create loan_details",
                account_id=account_id,
                error=str(exc),
            )
            raise RuntimeError(f"create loan details: {exc}") from exc

        self._log.info(
            "loan_details_created",
            account_id=account_id,
            principal=req.loan.principal,
            payment_amount=payment_amount,
            maturity_date=maturity_date.isoformat(),
        )

        return _loan_record_to_details(loan_rec)

    @staticmethod
    def _build_create_response(
        acct_uuid: _uuid.UUID,
        account_number: str,
        product,  # mypy: disable-error-code="empty-body"
        cur,  # mypy: disable-error-code="empty-body"
        customer,  # mypy: disable-error-code="empty-body"
        opened_at: datetime,
        loan_details: LoanDetails | None,
        req: CreateAccountRequest,
    ) -> AccountResponse:
        """Assemble the account creation response.

        New accounts start with zero balance; disbursement sets the principal
        for loan accounts via a separate API call.
        """
        is_loan = product.category == "loan"

        balance = Balance(amount=0, currency=product.currency, scale=cur.scale)

        # Loan accounts don't have available_balance (debit-balance account).
        if is_loan:
            available_balance = Balance(
                amount=0, currency=product.currency, scale=cur.scale
            )
        else:
            available_balance = Balance(
                amount=0, currency=product.currency, scale=cur.scale
            )

        return AccountResponse(
            id=str(acct_uuid),
            account_number=account_number,
            product_code=product.code,
            category=product.category,
            currency=product.currency,
            scale=cur.scale,
            status="active",
            balance=balance,
            available_balance=available_balance,
            owners=[
                AccountOwner(
                    customer_ref=req.customer_ref,
                    name=customer.name,
                    ownership_type=req.ownership_type,
                    role="owner",
                )
            ],
            opened_at=opened_at,
            loan_details=loan_details,
        )


def NewAccountService(
    tb_repo, pg_repo, product_repo, loan_repo, customer_service, logger=None
) -> AccountService:
    """Factory — mirrors the Go constructor name."""
    return AccountService(
        tb_repo, pg_repo, product_repo, loan_repo, customer_service, logger
    )


# -- module-level helpers -------------------------------------------------

def _uint128_to_int(value: bytes | int) -> int:
    """Convert a TigerBeetle Uint128 value to a Python int.

    TB stores cumulative fields as 16-byte little-endian Uint128.
    If the value is already an int (e.g., from a mock), it is returned as-is.
    """
    if isinstance(value, int):
        return value
    return int.from_bytes(value, byteorder="little")


def _compute_balance_from_tb(tb_acct: dict | None, code: int) -> ComputeBalanceResult:
    """Compute balance from a single TB account dict."""
    if tb_acct is None:
        return ComputeBalanceResult(posted=0, pending=0, available=0)

    dp = _uint128_to_int(tb_acct.get("debits_posted", b"\x00" * 16))
    cp = _uint128_to_int(tb_acct.get("credits_posted", b"\x00" * 16))
    dpnd = _uint128_to_int(tb_acct.get("debits_pending", b"\x00" * 16))
    cpnd = _uint128_to_int(tb_acct.get("credits_pending", b"\x00" * 16))

    return compute_balance(dp, cp, dpnd, cpnd, code)


def _compute_balance_from_tb_map(
    tb_map: dict[bytes, dict], tb_account_id: bytes, code: int
) -> ComputeBalanceResult:
    """Compute balance from a TB batch-lookup result map."""
    tb_acct = tb_map.get(tb_account_id)
    return _compute_balance_from_tb(tb_acct, code)


def _loan_record_to_details(rec: LoanDetailRecord) -> LoanDetails:
    """Convert a LoanDetailRecord to the domain LoanDetails model."""
    return LoanDetails(
        principal=rec.principal,
        outstanding=rec.outstanding,
        interest_rate=rec.interest_rate,
        term_months=rec.term_months,
        maturity_date=_format_date(rec.maturity_date),
        next_payment_due=_format_date(rec.next_payment_due),
        payment_amount=rec.payment_amount,
        status=rec.status,
    )


def _format_date(d) -> str:
    """Format a date/datetime as YYYY-MM-DD string."""
    if d is None:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    # date objects (from PG DATE columns) use isoformat directly.
    if isinstance(d, date):
        return d.isoformat()
    return str(d)
