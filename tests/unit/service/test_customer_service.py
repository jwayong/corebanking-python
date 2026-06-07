"""Unit tests for CustomerService (business logic layer).

Tests verify request validation, error propagation from the repository
layer, and account enrichment — all using mocked dependencies.

Mirrors the style of :mod:`tests.unit.store.postgres.test_customer_repo`.
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from cbs.domain.customer import RegisterCustomerRequest
from cbs.domain.errors import ErrAlreadyExists, ErrNotFound, ValidationError
from cbs.service.customer_service import CustomerService
from cbs.store.postgres.customer_repo import Customer, CustomerAccount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_customer(
    customer_ref="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
    name="Alice",
    labels=None,
    created_at=None,
):
    """Build a Customer dataclass for test fixtures."""
    if labels is None:
        labels = {}
    return Customer(
        customer_ref=customer_ref,
        name=name,
        labels=labels,
        created_at=created_at or datetime.now(),
    )


def _make_customer_account(
    id=1,
    account_number="SAV-001",
    product_code="SAVINGS",
    currency="USD",
    status="active",
    ownership_type="sole",
    role="owner",
):
    """Build a CustomerAccount dataclass for test fixtures."""
    return CustomerAccount(
        id=id,
        account_number=account_number,
        product_code=product_code,
        currency=currency,
        status=status,
        ownership_type=ownership_type,
        role=role,
    )


def _make_mock_repo():
    """Create a mock CustomerRepo with AsyncMock methods.

    Returns a MagicMock whose ``create``, ``get_by_ref``, and
    ``list_accounts_by_customer`` are AsyncMock instances ready for
    configuration via ``return_value`` or ``side_effect``.
    """
    repo = MagicMock()
    repo.create = AsyncMock()
    repo.get_by_ref = AsyncMock()
    repo.list_accounts_by_customer = AsyncMock()
    return repo


# ---------------------------------------------------------------------------
# CustomerService.register()
# ---------------------------------------------------------------------------

class TestCustomerServiceRegister:
    """Tests for ``CustomerService.register()``."""

    async def test_success_with_labels(self, mock_session, sample_uuid):
        """Happy path: valid request with labels persists and returns customer."""
        repo = _make_mock_repo()
        customer = _make_customer(customer_ref=sample_uuid, labels={"tier": "gold"})
        repo.create.return_value = customer

        svc = CustomerService(repo)
        req = RegisterCustomerRequest(
            customer_ref=sample_uuid,
            name="Alice",
            labels={"tier": "gold"},
        )
        result = await svc.register(mock_session, req)

        assert result is customer
        repo.create.assert_awaited_once_with(
            mock_session,
            customer_ref=sample_uuid,
            name="Alice",
            labels={"tier": "gold"},
        )

    async def test_success_without_labels(self, mock_session, sample_uuid):
        """Labels default to empty dict; service passes None to repo."""
        repo = _make_mock_repo()
        customer = _make_customer(labels={})
        repo.create.return_value = customer

        svc = CustomerService(repo)
        req = RegisterCustomerRequest(
            customer_ref=sample_uuid,
            name="Bob",
        )
        result = await svc.register(mock_session, req)

        assert result is customer
        repo.create.assert_awaited_once_with(
            mock_session,
            customer_ref=sample_uuid,
            name="Bob",
            labels=None,  # empty dict is falsy → None passed to repo
        )

    async def test_err_already_exists_re_raised(self, mock_session, sample_uuid):
        """When repo raises ErrAlreadyExists, service re-raises unchanged."""
        repo = _make_mock_repo()
        repo.create.side_effect = ErrAlreadyExists

        svc = CustomerService(repo)
        req = RegisterCustomerRequest(
            customer_ref=sample_uuid,
            name="Alice",
        )

        with pytest.raises(Exception) as exc_info:
            await svc.register(mock_session, req)
        assert exc_info.value is ErrAlreadyExists

    async def test_validation_error_empty_ref(self, mock_session):
        """Empty customer_ref raises ValidationError before touching repo."""
        repo = _make_mock_repo()

        svc = CustomerService(repo)
        req = RegisterCustomerRequest(customer_ref="", name="Alice")

        with pytest.raises(ValidationError, match="customer_ref is required"):
            await svc.register(mock_session, req)
        repo.create.assert_not_called()

    async def test_validation_error_empty_name(self, mock_session, sample_uuid):
        """Empty name raises ValidationError before touching repo."""
        repo = _make_mock_repo()

        svc = CustomerService(repo)
        req = RegisterCustomerRequest(
            customer_ref=sample_uuid,
            name="",
        )

        with pytest.raises(ValidationError, match="name is required"):
            await svc.register(mock_session, req)
        repo.create.assert_not_called()

    async def test_runtime_error_on_repo_failure(self, mock_session, sample_uuid):
        """Unexpected repo exception is wrapped in RuntimeError."""
        repo = _make_mock_repo()
        db_error = ConnectionError("connection refused")
        repo.create.side_effect = db_error

        svc = CustomerService(repo)
        req = RegisterCustomerRequest(
            customer_ref=sample_uuid,
            name="Alice",
        )

        with pytest.raises(RuntimeError, match="register customer: connection refused"):
            await svc.register(mock_session, req)


# ---------------------------------------------------------------------------
# CustomerService.get()
# ---------------------------------------------------------------------------

class TestCustomerServiceGet:
    """Tests for ``CustomerService.get()``."""

    async def test_success_with_accounts(self, mock_session):
        """Customer fetched and enriched with account list."""
        repo = _make_mock_repo()
        customer = _make_customer(customer_ref="CUST-001", name="Alice")
        accounts = [
            _make_customer_account(account_number="SAV-001"),
            _make_customer_account(
                id=2, account_number="CHK-001", product_code="CHECKING"
            ),
        ]
        repo.get_by_ref.return_value = customer
        repo.list_accounts_by_customer.return_value = accounts

        svc = CustomerService(repo)
        result = await svc.get(mock_session, "CUST-001")

        assert result is customer
        assert result.accounts == accounts
        repo.get_by_ref.assert_awaited_once_with(mock_session, "CUST-001")
        repo.list_accounts_by_customer.assert_awaited_once_with(
            mock_session, "CUST-001"
        )

    async def test_success_no_accounts(self, mock_session):
        """Customer with zero accounts returns empty list."""
        repo = _make_mock_repo()
        customer = _make_customer(customer_ref="CUST-002", name="Bob")
        repo.get_by_ref.return_value = customer
        repo.list_accounts_by_customer.return_value = []

        svc = CustomerService(repo)
        result = await svc.get(mock_session, "CUST-002")

        assert result is customer
        assert result.accounts == []

    async def test_err_not_found(self, mock_session):
        """When repo returns None, service raises ErrNotFound."""
        repo = _make_mock_repo()
        repo.get_by_ref.return_value = None

        svc = CustomerService(repo)

        with pytest.raises(Exception) as exc_info:
            await svc.get(mock_session, "CUST-999")
        assert exc_info.value is ErrNotFound

    async def test_validation_error_empty_ref(self, mock_session):
        """Empty ref string raises ValidationError before touching repo."""
        repo = _make_mock_repo()

        svc = CustomerService(repo)

        with pytest.raises(ValidationError, match="customer_ref is required"):
            await svc.get(mock_session, "")
        repo.get_by_ref.assert_not_called()

    async def test_runtime_error_on_list_accounts_failure(self, mock_session):
        """When list_accounts_by_customer fails, wrapped in RuntimeError."""
        repo = _make_mock_repo()
        customer = _make_customer(customer_ref="CUST-001", name="Alice")
        repo.get_by_ref.return_value = customer
        db_error = ConnectionError("connection timeout")
        repo.list_accounts_by_customer.side_effect = db_error

        svc = CustomerService(repo)

        with pytest.raises(RuntimeError, match="get customer accounts: connection timeout"):
            await svc.get(mock_session, "CUST-001")
