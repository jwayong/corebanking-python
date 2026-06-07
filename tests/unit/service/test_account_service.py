"""Unit tests for AccountService (business logic layer).

Tests verify request validation, dual-write orchestration (TB-first / PG-second),
error propagation from repository layers, and response enrichment — all using
mocked dependencies.

Mirrors the style of :mod:`tests.unit.service.test_customer_service`.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from cbs.domain.accounts import (
    AccountListResponse,
    ComputeBalanceResult,
    CreateAccountRequest,
)
from cbs.domain.errors import (
    ErrAccountClosed,
    ErrNotFound,
    ErrNonZeroBalance,
    ErrPendingHolds,
    ErrProductInactive,
    ValidationError,
)
from cbs.domain.loans import LoanDetails, LoanRequest
from cbs.service.account_service import (
    AccountService,
    NewAccountService,
    _compute_balance_from_tb,
)
from cbs.store.postgres.account_repo import (
    AccountRecord,
    CustomerAccountRecord,
)
from cbs.util.tb_types import uint128_to_int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_product(
    code="SAVINGS",
    name="Savings Account",
    category="deposit",
    tb_account_code=2110,
    currency="USD",
    tb_ledger=840,
    is_active=True,
    interest_rate=None,
):
    """Build a mock ProductRecord for test fixtures."""
    product = MagicMock()
    product.id = 1
    product.code = code
    product.name = name
    product.category = category
    product.tb_account_code = tb_account_code
    product.currency = currency
    product.tb_ledger = tb_ledger
    product.is_active = is_active
    product.interest_rate = interest_rate
    return product


def _make_customer(
    customer_ref="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
    name="Alice",
):
    """Build a mock Customer for test fixtures."""
    customer = MagicMock()
    customer.customer_ref = customer_ref
    customer.name = name
    return customer


def _make_tb_repo():
    """Create a mock TigerBeetleAccountRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.create_account = AsyncMock()
    repo.lookup_account = AsyncMock()
    repo.lookup_accounts = AsyncMock()
    return repo


def _make_pg_repo():
    """Create a mock AccountRepo with AsyncMock methods."""
    repo = MagicMock()
    repo.create = AsyncMock()
    repo.get_by_tb_account_id = AsyncMock()
    repo.next_account_sequence = AsyncMock()
    repo.create_customer_account = AsyncMock()
    repo.get_owners_by_account_id = AsyncMock()
    repo.list_by_customer_ref = AsyncMock()
    repo.close_account = AsyncMock()
    return repo


def _make_product_repo():
    """Create a mock product repo with AsyncMock get_by_code method."""
    repo = MagicMock()
    repo.get_by_code = AsyncMock()
    return repo


def _make_loan_repo():
    """Create a mock loan repo with AsyncMock methods."""
    repo = MagicMock()
    repo.create = AsyncMock()
    repo.get_by_account_id = AsyncMock()
    return repo


def _make_customer_service():
    """Create a mock CustomerService with AsyncMock get method."""
    svc = MagicMock()
    svc.get = AsyncMock()
    return svc


def _make_account_with_product(
    id=1,
    tb_account_id=None,
    account_number="USD-2110-00001",
    product_code="SAVINGS",
    category="deposit",
    currency="USD",
    status="active",
    opened_at=None,
    tb_account_code=2110,
):
    """Build an AccountWithProduct for test fixtures."""
    from cbs.store.postgres.account_repo import AccountWithProduct

    return AccountWithProduct(
        id=id,
        tb_account_id=tb_account_id or b"\x01" * 16,
        account_number=account_number,
        product_code=product_code,
        category=category,
        currency=currency,
        status=status,
        opened_at=opened_at or datetime(2025, 1, 1, 10, 0),
        tb_account_code=tb_account_code,
    )


def _make_owner_record(
    customer_ref="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
    name="Alice",
    ownership_type="sole",
    role="owner",
):
    """Build an OwnerRecord for test fixtures."""
    from cbs.store.postgres.account_repo import OwnerRecord

    return OwnerRecord(
        customer_ref=customer_ref,
        name=name,
        ownership_type=ownership_type,
        role=role,
    )


def _make_loan_detail_record(
    id=1,
    account_id=1,
    principal=100000,
    outstanding=100000,
    interest_rate=5.0,
    term_months=12,
    payment_amount=8563,
    status="active",
):
    """Build a LoanDetailRecord for test fixtures."""
    from cbs.store.postgres.loan_repo import LoanDetailRecord

    return LoanDetailRecord(
        id=id,
        account_id=account_id,
        principal=principal,
        outstanding=outstanding,
        interest_rate=interest_rate,
        term_months=term_months,
        disbursed_at=None,
        maturity_date=date(2026, 1, 1),
        next_payment_due=date(2025, 2, 1),
        payment_amount=payment_amount,
        status=status,
    )


# ---------------------------------------------------------------------------
# AccountService.create()
# ---------------------------------------------------------------------------

class TestAccountServiceCreate:
    """Tests for ``AccountService.create()``."""

    async def test_success_deposit_account(self, mock_session, sample_uuid):
        """Happy path: deposit account creation with dual-write and zero balance."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        product = _make_product()
        product_repo.get_by_code.return_value = product

        pg_repo.next_account_sequence.return_value = 1

        mock_uuid = MagicMock()
        mock_uuid.hex = "0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"
        mock_uuid.bytes = b"\x01" * 16
        mock_uuid.__str__ = MagicMock(return_value=sample_uuid)

        pg_rec = AccountRecord(
            id=1,
            tb_account_id=mock_uuid.bytes,
            product_id=product.id,
            account_number="USD-2110-00001",
            status="active",
            opened_at=datetime(2025, 1, 1, 10, 0),
        )
        pg_repo.create.return_value = pg_rec

        customer = _make_customer(customer_ref=sample_uuid)
        customer_service.get.return_value = customer

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="SAVINGS",
        )

        with patch("cbs.service.account_service.generate_uuidv7", return_value=mock_uuid), \
             patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            result = await svc.create(mock_session, req)

        assert result.id == sample_uuid
        assert result.account_number == "USD-2110-00001"
        assert result.product_code == "SAVINGS"
        assert result.category == "deposit"
        assert result.balance.amount == 0
        assert result.available_balance.amount == 0
        assert result.status == "active"
        assert len(result.owners) == 1
        assert result.owners[0].customer_ref == sample_uuid
        assert result.loan_details is None

        # Verify dual-write order: TB first, then PG
        product_repo.get_by_code.assert_awaited_once_with(mock_session, "SAVINGS")
        customer_service.get.assert_awaited_once_with(mock_session, sample_uuid)

        call_args = pg_repo.next_account_sequence.call_args
        assert call_args[0][1] == "USD-2110"

        tb_repo.create_account.assert_awaited_once()
        assert tb_repo.create_account.call_args[0][0]["code"] == 2110

        pg_rec_arg = pg_repo.create.call_args[0][1]
        assert isinstance(pg_rec_arg, AccountRecord)
        assert pg_rec_arg.account_number == "USD-2110-00001"
        assert pg_rec_arg.status == "active"

        ca_arg = pg_repo.create_customer_account.call_args[0][1]
        assert isinstance(ca_arg, CustomerAccountRecord)
        assert ca_arg.customer_ref == sample_uuid
        assert ca_arg.account_id == 1

        # Deposit account — loan_repo.create should NOT be called
        loan_repo.create.assert_not_called()

    async def test_success_loan_account(self, mock_session, sample_uuid):
        """Happy path: loan account creation includes loan_details row."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        product = _make_product(
            code="PERSONAL_LOAN",
            category="loan",
            tb_account_code=1401,
            interest_rate=5.0,
        )
        product_repo.get_by_code.return_value = product

        pg_repo.next_account_sequence.return_value = 1

        mock_uuid = MagicMock()
        mock_uuid.hex = "0194e7c38f4a7b2d9c1e4f5a6b7c8d9e"
        mock_uuid.bytes = b"\x01" * 16
        mock_uuid.__str__ = MagicMock(return_value=sample_uuid)

        pg_rec = AccountRecord(
            id=1,
            tb_account_id=mock_uuid.bytes,
            product_id=product.id,
            account_number="USD-1401-00001",
            status="active",
            opened_at=datetime(2025, 1, 1, 10, 0),
        )
        pg_repo.create.return_value = pg_rec

        customer = _make_customer(customer_ref=sample_uuid)
        customer_service.get.return_value = customer

        loan_rec = _make_loan_detail_record(account_id=1)
        loan_repo.create.return_value = loan_rec

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="PERSONAL_LOAN",
            loan=LoanRequest(principal=100000, term_months=12),
        )

        with patch("cbs.service.account_service.generate_uuidv7", return_value=mock_uuid), \
             patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            result = await svc.create(mock_session, req)

        assert result.id == sample_uuid
        assert result.account_number == "USD-1401-00001"
        assert result.category == "loan"
        assert result.balance.amount == 0

        # Loan details should be created
        loan_repo.create.assert_awaited_once()
        assert result.loan_details is not None
        assert result.loan_details.principal == 100000

    async def test_validation_error_empty_product_code(self, mock_session, sample_uuid):
        """Empty product_code raises ValidationError before touching repos."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="",
        )

        with pytest.raises(ValidationError, match="product_code is required"):
            await svc.create(mock_session, req)

        product_repo.get_by_code.assert_not_called()
        pg_repo.create.assert_not_called()

    async def test_validation_error_empty_customer_ref(self, mock_session):
        """Empty customer_ref raises ValidationError before touching repos."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref="",
            product_code="SAVINGS",
        )

        with pytest.raises(ValidationError, match="customer_ref is required"):
            await svc.create(mock_session, req)

        product_repo.get_by_code.assert_not_called()
        pg_repo.create.assert_not_called()

    async def test_product_not_found(self, mock_session, sample_uuid):
        """ErrNotFound from product_repo converted to ValidationError."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        product_repo.get_by_code.side_effect = ErrNotFound

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="NONEXISTENT",
        )

        with pytest.raises(ValidationError, match='product "NONEXISTENT" not found'):
            await svc.create(mock_session, req)

        pg_repo.create.assert_not_called()

    async def test_product_inactive(self, mock_session, sample_uuid):
        """Inactive product raises ErrProductInactive."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        product = _make_product(is_active=False)
        product_repo.get_by_code.return_value = product

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="SAVINGS",
        )

        with pytest.raises(Exception) as exc_info:
            await svc.create(mock_session, req)
        assert exc_info.value is ErrProductInactive

    async def test_customer_not_found(self, mock_session, sample_uuid):
        """ErrNotFound from customer_service converted to ValidationError."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        product = _make_product()
        product_repo.get_by_code.return_value = product
        customer_service.get.side_effect = ErrNotFound

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="SAVINGS",
        )

        with pytest.raises(ValidationError, match='customer ".*" not found'):
            await svc.create(mock_session, req)

        pg_repo.create.assert_not_called()

    async def test_loan_required_for_loan_product(self, mock_session, sample_uuid):
        """Missing loan field for loan product raises ValidationError."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        product = _make_product(
            code="PERSONAL_LOAN",
            category="loan",
            interest_rate=5.0,
        )
        product_repo.get_by_code.return_value = product

        customer = _make_customer(customer_ref=sample_uuid)
        customer_service.get.return_value = customer

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="PERSONAL_LOAN",
        )

        with pytest.raises(ValidationError, match="loan field is required"):
            await svc.create(mock_session, req)

        pg_repo.create.assert_not_called()

    async def test_loan_invalid_for_non_loan_product(self, mock_session, sample_uuid):
        """Loan field on non-loan product raises ValidationError."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        product = _make_product(category="deposit")
        product_repo.get_by_code.return_value = product

        customer = _make_customer(customer_ref=sample_uuid)
        customer_service.get.return_value = customer

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        req = CreateAccountRequest(
            customer_ref=sample_uuid,
            product_code="SAVINGS",
            loan=LoanRequest(principal=1000, term_months=6),
        )

        with pytest.raises(ValidationError, match="loan field is only valid"):
            await svc.create(mock_session, req)

        pg_repo.create.assert_not_called()


# ---------------------------------------------------------------------------
# AccountService.get()
# ---------------------------------------------------------------------------

class TestAccountServiceGet:
    """Tests for ``AccountService.get()``."""

    async def test_success_deposit_account(self, mock_session):
        """Deposit account fetched with TB balance and owners."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        # Service uses _uuid.UUID(id).bytes — real UUID bytes, not our mock
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_acct = {
            "debits_posted": 0,
            "credits_posted": 1000,
            "debits_pending": 200,
            "credits_pending": 0,
        }
        tb_repo.lookup_account.return_value = tb_acct

        owner_recs = [_make_owner_record()]
        pg_repo.get_owners_by_account_id.return_value = owner_recs

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            result = await svc.get(mock_session, acct_uuid)

        assert result.id == acct_uuid
        assert result.account_number == "USD-2110-00001"
        assert result.product_code == "SAVINGS"
        assert result.category == "deposit"
        # Credit-balance: posted = credits - debits = 1000, available = posted - dpnd + cpnd = 800
        assert result.balance.amount == 1000
        assert result.available_balance.amount == 800
        assert len(result.owners) == 1
        assert result.loan_details is None

        pg_repo.get_by_tb_account_id.assert_awaited_once_with(
            mock_session, acct_uuid_bytes
        )

    async def test_success_loan_account(self, mock_session):
        """Loan account fetched with loan_details enrichment."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
            category="loan",
            product_code="PERSONAL_LOAN",
            tb_account_code=1401,  # LOAN_PERSONAL — debit-balance
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_acct = {
            "debits_posted": 1000,
            "credits_posted": 0,
            "debits_pending": 0,
            "credits_pending": 0,
        }
        tb_repo.lookup_account.return_value = tb_acct

        pg_repo.get_owners_by_account_id.return_value = []

        loan_rec = _make_loan_detail_record()
        loan_repo.get_by_account_id.return_value = loan_rec

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            result = await svc.get(mock_session, acct_uuid)

        assert result.category == "loan"
        # Debit-balance: posted = debits - credits = 1000
        assert result.balance.amount == 1000
        # Loan accounts have zero available_balance
        assert result.available_balance.amount == 0

        loan_repo.get_by_account_id.assert_awaited_once_with(mock_session, pg_acct.id)
        assert result.loan_details is not None
        assert result.loan_details.principal == 100000

    async def test_validation_error_empty_id(self, mock_session):
        """Empty id raises ValidationError before touching repos."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with pytest.raises(ValidationError, match="account id is required"):
            await svc.get(mock_session, "")

        pg_repo.get_by_tb_account_id.assert_not_called()

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Invalid UUID format raises ValidationError before touching repos."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with pytest.raises(ValidationError, match="account id must be a valid UUID"):
            await svc.get(mock_session, "not-a-uuid")

        pg_repo.get_by_tb_account_id.assert_not_called()

    async def test_err_not_found(self, mock_session):
        """PG returns None -> ErrNotFound raised."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        pg_repo.get_by_tb_account_id.return_value = None

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"

        with pytest.raises(Exception) as exc_info:
            await svc.get(mock_session, acct_uuid)
        assert exc_info.value is ErrNotFound

    async def test_tb_account_none_warn_but_continue(self, mock_session):
        """TB account None: warn but continue with zero balance."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_repo.lookup_account.return_value = None
        pg_repo.get_owners_by_account_id.return_value = []

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            result = await svc.get(mock_session, acct_uuid)

        assert result.balance.amount == 0
        assert result.available_balance.amount == 0


# ---------------------------------------------------------------------------
# AccountService.list()
# ---------------------------------------------------------------------------

class TestAccountServiceList:
    """Tests for ``AccountService.list()``."""

    async def test_success_single_page(self, mock_session):
        """Single page of accounts with balances."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        customer_ref = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"

        acct_uuid = MagicMock()
        acct_uuid.__str__ = MagicMock(return_value="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")

        mock_bytes = b"\x01" * 16
        pg_acct = _make_account_with_product(
            tb_account_id=mock_bytes,
        )

        pg_repo.list_by_customer_ref.return_value = [pg_acct]

        mock_uint128_key = MagicMock()
        tb_map = {mock_bytes: {"debits_posted": 0, "credits_posted": 500}}
        tb_repo.lookup_accounts.return_value = tb_map

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.tb_id_to_uuid", return_value=acct_uuid), \
             patch("cbs.service.account_service.uuid_to_uint128", return_value=mock_uint128_key):
            result = await svc.list(mock_session, customer_ref)

        assert isinstance(result, AccountListResponse)
        assert len(result.data) == 1
        assert result.data[0].id == "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        assert result.data[0].account_number == "USD-2110-00001"
        assert result.data[0].product_code == "SAVINGS"
        # Credit-balance: posted = credits - debits = 500
        assert result.data[0].balance.amount == 500
        assert not result.has_more
        assert result.next_cursor == ""

        pg_repo.list_by_customer_ref.assert_awaited_once_with(
            mock_session, customer_ref, 0, 50
        )

    async def test_success_has_more_pagination(self, mock_session):
        """Fetched limit+1 rows -> has_more=True with next_cursor."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        customer_ref = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"

        acct_uuid_1 = MagicMock()
        acct_uuid_1.__str__ = MagicMock(return_value="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")
        acct_uuid_2 = MagicMock()
        acct_uuid_2.__str__ = MagicMock(return_value="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f")

        mock_bytes_1 = b"\x01" * 16
        mock_bytes_2 = b"\x02" * 16

        pg_acct_1 = _make_account_with_product(
            id=1, tb_account_id=mock_bytes_1, account_number="USD-2110-00001"
        )
        pg_acct_2 = _make_account_with_product(
            id=2, tb_account_id=mock_bytes_2, account_number="USD-2110-00002"
        )

        pg_repo.list_by_customer_ref.return_value = [pg_acct_1, pg_acct_2]

        mock_uint128_key = MagicMock()
        tb_map = {
            mock_bytes_1: {"debits_posted": 0, "credits_posted": 500},
            mock_bytes_2: {"debits_posted": 0, "credits_posted": 300},
        }
        tb_repo.lookup_accounts.return_value = tb_map

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        def mock_tb_id_to_uuid(raw):
            if raw == mock_bytes_1:
                return acct_uuid_1
            return acct_uuid_2

        with patch("cbs.service.account_service.tb_id_to_uuid", side_effect=mock_tb_id_to_uuid), \
             patch("cbs.service.account_service.uuid_to_uint128", return_value=mock_uint128_key):
            result = await svc.list(mock_session, customer_ref, limit=1)

        assert len(result.data) == 1
        assert result.has_more
        # next_cursor is the PG id of the last account in the trimmed list
        assert result.next_cursor == "1"

        # Verify limit was passed to repo (repo adds +1 internally for pagination)
        pg_repo.list_by_customer_ref.assert_awaited_once_with(
            mock_session, customer_ref, 0, 1
        )

    async def test_empty_result(self, mock_session):
        """No accounts for customer returns empty list."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        customer_ref = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"

        pg_repo.list_by_customer_ref.return_value = []

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        result = await svc.list(mock_session, customer_ref)

        assert isinstance(result, AccountListResponse)
        assert result.data == []
        assert not result.has_more

    async def test_validation_error_empty_customer_ref(self, mock_session):
        """Empty customer_ref raises ValidationError."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with pytest.raises(ValidationError, match="customer_ref is required"):
            await svc.list(mock_session, "")

        pg_repo.list_by_customer_ref.assert_not_called()

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Invalid UUID for customer_ref raises ValidationError."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with pytest.raises(ValidationError, match="customer_ref must be a valid UUID"):
            await svc.list(mock_session, "not-a-uuid")

        pg_repo.list_by_customer_ref.assert_not_called()


# ---------------------------------------------------------------------------
# AccountService.close()
# ---------------------------------------------------------------------------

class TestAccountServiceClose:
    """Tests for ``AccountService.close()``."""

    async def test_success_zero_balance(self, mock_session):
        """Account with zero balance and no pending holds closes successfully."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_acct = {
            "debits_posted": 1000,
            "credits_posted": 1000,
            "debits_pending": 0,
            "credits_pending": 0,
        }
        tb_repo.lookup_account.return_value = tb_acct

        closed_at = datetime(2025, 6, 1, 12, 0)
        pg_repo.close_account.return_value = closed_at

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            result = await svc.close(mock_session, acct_uuid)

        assert result.id == acct_uuid
        assert result.status == "closed"
        assert result.closed_at == closed_at

        pg_repo.close_account.assert_awaited_once_with(mock_session, pg_acct.id)

    async def test_err_account_closed(self, mock_session):
        """Already closed account raises ErrAccountClosed."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
            status="closed",
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with pytest.raises(Exception) as exc_info:
            await svc.close(mock_session, acct_uuid)
        assert exc_info.value is ErrAccountClosed

    async def test_err_pending_holds(self, mock_session):
        """Pending holds prevent closing."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_acct = {
            "debits_posted": 1000,
            "credits_posted": 1000,
            "debits_pending": 100,
            "credits_pending": 50,
        }
        tb_repo.lookup_account.return_value = tb_acct

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            with pytest.raises(Exception) as exc_info:
                await svc.close(mock_session, acct_uuid)
            assert exc_info.value is ErrPendingHolds

        pg_repo.close_account.assert_not_called()

    async def test_err_non_zero_balance(self, mock_session):
        """Non-zero balance (debits != credits) prevents closing."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_acct = {
            "debits_posted": 1000,
            "credits_posted": 500,
            "debits_pending": 0,
            "credits_pending": 0,
        }
        tb_repo.lookup_account.return_value = tb_acct

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            with pytest.raises(Exception) as exc_info:
                await svc.close(mock_session, acct_uuid)
            assert exc_info.value is ErrNonZeroBalance

        pg_repo.close_account.assert_not_called()

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Invalid UUID format raises ValidationError."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with pytest.raises(ValidationError, match="account id must be a valid UUID"):
            await svc.close(mock_session, "not-a-uuid")

        pg_repo.get_by_tb_account_id.assert_not_called()

    async def test_err_not_found(self, mock_session):
        """PG account not found raises ErrNotFound."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        pg_repo.get_by_tb_account_id.return_value = None

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"

        with pytest.raises(Exception) as exc_info:
            await svc.close(mock_session, acct_uuid)
        assert exc_info.value is ErrNotFound

    async def test_tb_account_not_found_during_close(self, mock_session):
        """TB account missing during close raises ErrNotFound."""
        tb_repo = _make_tb_repo()
        pg_repo = _make_pg_repo()
        product_repo = _make_product_repo()
        loan_repo = _make_loan_repo()
        customer_service = _make_customer_service()

        acct_uuid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        import uuid as _uuid

        acct_uuid_bytes = _uuid.UUID(acct_uuid).bytes

        pg_acct = _make_account_with_product(
            tb_account_id=acct_uuid_bytes,
        )
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_repo.lookup_account.return_value = None

        svc = NewAccountService(
            tb_repo, pg_repo, product_repo, loan_repo, customer_service
        )

        with patch("cbs.service.account_service.uuid_to_uint128", return_value=b"\x00" * 16):
            with pytest.raises(Exception) as exc_info:
                await svc.close(mock_session, acct_uuid)
            assert exc_info.value is ErrNotFound

        pg_repo.close_account.assert_not_called()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class TestModuleHelpers:
    """Tests for module-level helper functions."""

    def test_uint128_to_int_bytes(self):
        """Convert 16-byte little-endian Uint128 to int."""
        value = (1000).to_bytes(16, byteorder="little")
        assert uint128_to_int(value) == 1000

    def test_uint128_to_int_already_int(self):
        """Pass-through when value is already an int."""
        assert uint128_to_int(42) == 42

    def test_compute_balance_from_tb_none(self):
        """None TB account returns zero balance."""
        result = _compute_balance_from_tb(None, 2110)
        assert result == ComputeBalanceResult(posted=0, pending=0, available=0)

    def test_compute_balance_from_tb_credit_account(self):
        """Credit-balance account (liability): posted = credits - debits."""
        tb_acct = {
            "debits_posted": 0,
            "credits_posted": 1000,
            "debits_pending": 200,
            "credits_pending": 0,
        }
        result = _compute_balance_from_tb(tb_acct, 2110)  # DEPOSIT_SAVINGS
        assert result.posted == 1000
        assert result.available == 800

    def test_compute_balance_from_tb_debit_account(self):
        """Debit-balance account (asset/loan): posted = debits - credits."""
        tb_acct = {
            "debits_posted": 1000,
            "credits_posted": 200,
            "debits_pending": 0,
            "credits_pending": 100,
        }
        result = _compute_balance_from_tb(tb_acct, 1401)  # LOAN_PERSONAL
        assert result.posted == 800
        # available = posted + debits_pending - credits_pending = 800 + 0 - 100
        assert result.available == 700
