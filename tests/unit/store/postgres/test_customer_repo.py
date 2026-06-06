"""Unit tests for CustomerRepo (PostgreSQL customer operations).

Tests verify INSERT/SELECT SQL, JSONB label handling, unique-violation
detection, and row mapping using mocked async sessions.
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock

from sqlalchemy.exc import NoResultFound, IntegrityError

from cbs.domain.errors import ErrAlreadyExists
from cbs.store.postgres.customer_repo import (
    Customer,
    CustomerAccount,
    CustomerRepo,
)
from tests.unit.store.postgres.fixtures import make_mock_result, make_pg_integrity_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_customer_row(
    customer_ref="CUST-001",
    name="Alice",
    labels=None,
    created_at=None,
):
    if labels is None:
        labels = {}
    return (customer_ref, name, labels, created_at)


def _make_customer_account_row(
    id=1,
    account_number="SAV-001",
    product_code="SAVINGS",
    currency="USD",
    status="active",
    ownership_type="sole",
    role="owner",
):
    return (id, account_number, product_code, currency, status, ownership_type, role)


# ---------------------------------------------------------------------------
# CustomerRepo.create()
# ---------------------------------------------------------------------------

class TestCustomerRepoCreate:
    async def test_success(self, mock_session, mock_db):
        now = datetime.now()
        labels_dict = {"tier": "gold"}
        row = _make_customer_row("CUST-001", "Alice", labels_dict, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        result = await repo.create(mock_session, "CUST-001", "Alice", labels={"tier": "gold"})

        assert result.customer_ref == "CUST-001"
        assert result.name == "Alice"
        assert result.labels == {"tier": "gold"}
        assert result.created_at == now

    async def test_success_with_none_labels(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_customer_row("CUST-002", "Bob", {}, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        result = await repo.create(mock_session, "CUST-002", "Bob")

        assert result.labels == {}
        mock_session.execute.assert_called_once()

    async def test_labels_serialized_to_json(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_customer_row("CUST-003", "Charlie", {}, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        await repo.create(mock_session, "CUST-003", "Charlie", labels={"region": "APAC"})

        params = mock_session.execute.call_args[0][1]
        assert json.loads(params["labels"]) == {"region": "APAC"}

    async def test_unique_violation_raises_err_already_exists(self, mock_session, mock_db):
        mock_session.execute.side_effect = make_pg_integrity_error("23505")

        repo = CustomerRepo(mock_db)
        with pytest.raises(Exception) as exc_info:
            await repo.create(mock_session, "CUST-001", "Alice")
        assert exc_info.value is ErrAlreadyExists

    async def test_unique_violation_preserves_cause(self, mock_session, mock_db):
        mock_session.execute.side_effect = make_pg_integrity_error("23505")

        repo = CustomerRepo(mock_db)
        with pytest.raises(Exception) as exc_info:
            await repo.create(mock_session, "CUST-001", "Alice")
        assert exc_info.value is ErrAlreadyExists
        assert isinstance(exc_info.value.__cause__, IntegrityError)

    async def test_non_unique_integrity_error_propagates(self, mock_session, mock_db):
        mock_session.execute.side_effect = make_pg_integrity_error("23503")

        repo = CustomerRepo(mock_db)
        with pytest.raises(IntegrityError):
            await repo.create(mock_session, "CUST-001", "Alice")

    async def test_no_rows_raises_runtime_error(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = CustomerRepo(mock_db)
        with pytest.raises(RuntimeError, match="customer insert returned no rows"):
            await repo.create(mock_session, "CUST-001", "Alice")

    async def test_uses_insert_sql(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_customer_row("CUST-001", "Alice", {}, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        await repo.create(mock_session, "CUST-001", "Alice")

        sql = str(mock_session.execute.call_args[0][0])
        assert "INSERT INTO customers" in sql
        assert "RETURNING customer_ref, name, labels, created_at" in sql

    async def test_sends_correct_params(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_customer_row("CUST-010", "Diana", {}, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        await repo.create(mock_session, "CUST-010", "Diana", labels={"vip": "true"})

        params = mock_session.execute.call_args[0][1]
        assert params["customer_ref"] == "CUST-010"
        assert params["name"] == "Diana"

    async def test_parses_jsonb_labels_from_db(self, mock_session, mock_db):
        now = datetime.now()
        labels = {"tier": "platinum", "region": "EMEA"}
        row = _make_customer_row("CUST-020", "Eve", labels, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        result = await repo.create(mock_session, "CUST-020", "Eve", labels=labels)

        assert result.labels == {"tier": "platinum", "region": "EMEA"}


# ---------------------------------------------------------------------------
# CustomerRepo.get_by_ref()
# ---------------------------------------------------------------------------

class TestCustomerRepoGetByRef:
    async def test_found(self, mock_session, mock_db):
        now = datetime.now()
        labels = {"tier": "gold"}
        row = _make_customer_row("CUST-001", "Alice", labels, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        result = await repo.get_by_ref(mock_session, "CUST-001")

        assert result is not None
        assert result.customer_ref == "CUST-001"
        assert result.name == "Alice"
        assert result.labels == {"tier": "gold"}
        assert result.created_at == now

    async def test_not_found_none(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = CustomerRepo(mock_db)
        result = await repo.get_by_ref(mock_session, "CUST-999")

        assert result is None

    async def test_noresultfound_returns_none(self, mock_session, mock_db):
        mock_session.execute.side_effect = NoResultFound()

        repo = CustomerRepo(mock_db)
        result = await repo.get_by_ref(mock_session, "CUST-999")

        assert result is None

    async def test_null_labels_become_empty_dict(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_customer_row("CUST-001", "Alice", None, now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = CustomerRepo(mock_db)
        result = await repo.get_by_ref(mock_session, "CUST-001")

        assert result.labels == {}

    async def test_sends_ref_param(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = CustomerRepo(mock_db)
        await repo.get_by_ref(mock_session, "CUST-ABC")

        params = mock_session.execute.call_args[0][1]
        assert params["ref"] == "CUST-ABC"

    async def test_uses_correct_select(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = CustomerRepo(mock_db)
        await repo.get_by_ref(mock_session, "CUST-001")

        sql = str(mock_session.execute.call_args[0][0])
        assert "SELECT customer_ref, name, labels, created_at" in sql
        assert "FROM customers" in sql


# ---------------------------------------------------------------------------
# CustomerRepo.list_accounts_by_customer()
# ---------------------------------------------------------------------------

class TestCustomerRepoListAccountsByCustomer:
    async def test_with_accounts(self, mock_session, mock_db):
        rows = [
            _make_customer_account_row(id=1, account_number="SAV-001"),
            _make_customer_account_row(id=2, account_number="CHK-001", product_code="CHECKING"),
        ]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = CustomerRepo(mock_db)
        result = await repo.list_accounts_by_customer(mock_session, "CUST-001")

        assert len(result) == 2
        assert result[0].id == 1
        assert result[0].account_number == "SAV-001"
        assert result[1].product_code == "CHECKING"

    async def test_empty_result(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = CustomerRepo(mock_db)
        result = await repo.list_accounts_by_customer(mock_session, "CUST-999")

        assert result == []
        assert isinstance(result, list)

    async def test_maps_all_fields(self, mock_session, mock_db):
        rows = [_make_customer_account_row(
            id=5, account_number="LOAN-042", product_code="PERSONAL_LOAN",
            currency="EUR", status="active", ownership_type="sole", role="borrower",
        )]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = CustomerRepo(mock_db)
        result = await repo.list_accounts_by_customer(mock_session, "CUST-010")

        assert len(result) == 1
        r = result[0]
        assert r.id == 5
        assert r.currency == "EUR"
        assert r.ownership_type == "sole"
        assert r.role == "borrower"

    async def test_sends_ref_param(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = CustomerRepo(mock_db)
        await repo.list_accounts_by_customer(mock_session, "CUST-XYZ")

        params = mock_session.execute.call_args[0][1]
        assert params["ref"] == "CUST-XYZ"

    async def test_uses_correct_joins(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = CustomerRepo(mock_db)
        await repo.list_accounts_by_customer(mock_session, "CUST-001")

        sql = str(mock_session.execute.call_args[0][0])
        assert "customer_accounts ca" in sql
        assert "JOIN accounts a" in sql
        assert "JOIN products p" in sql
