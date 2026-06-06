"""Unit tests for AccountRepo (PostgreSQL account metadata operations).

Tests verify SQL structure, row mapping, pagination, and edge cases
using mocked async sessions — no real database required.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call

from sqlalchemy.exc import NoResultFound

from cbs.store.postgres.account_repo import (
    AccountRepo,
    AccountRecord,
    AccountWithProduct,
    CustomerAccountRecord,
    OwnerRecord,
    _hash_prefix,
)
from tests.unit.store.postgres.fixtures import make_mock_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_account_row(
    id=1,
    tb_account_id=b"\x01\x02",
    account_number="SAV-001",
    status="active",
    opened_at=None,
    closed_at=None,
    product_id=10,
    product_code="SAVINGS",
    product_name="Savings Account",
    category="deposit",
    tb_account_code=2101,
    currency="USD",
    tb_ledger=840,
):
    """Build a 13-tuple matching _ACCOUNT_WITH_PRODUCT_COLS."""
    return (
        id, tb_account_id, account_number, status, opened_at, closed_at,
        product_id, product_code, product_name, category, tb_account_code,
        currency, tb_ledger,
    )


def _make_owner_row(
    customer_ref="CUST-001",
    name="Alice",
    ownership_type="sole",
    role="owner",
):
    return (customer_ref, name, ownership_type, role)


# ---------------------------------------------------------------------------
# _hash_prefix
# ---------------------------------------------------------------------------

class TestHashPrefix:
    def test_deterministic(self):
        assert _hash_prefix("SAV") == _hash_prefix("SAV")

    def test_different_strings(self):
        assert _hash_prefix("SAV") != _hash_prefix("CHK")

    def test_empty_string(self):
        assert _hash_prefix("") == 0

    def test_short_prefix_positive(self):
        """Short prefixes produce positive values (no overflow)."""
        h = _hash_prefix("SAV")
        assert h > 0

    def test_long_prefix_can_be_negative(self):
        """Long prefixes may overflow to negative (two's complement)."""
        # A long enough prefix will cause the hash to exceed 2^63,
        # producing a negative signed int64 value.
        long_prefix = "A" * 20
        h = _hash_prefix(long_prefix)
        # The value should fit in signed int64 range
        assert -(1 << 63) <= h < (1 << 63)

    def test_matches_go_two_complement(self):
        """Verify two's complement overflow matches Go int64 behaviour.

        In Go: h = 0; for _, c := range s { h = h*31 + int64(c) }
        Go's int64 wraps via two's complement. Python must do the same.
        """
        # "SAV" = 83, 65, 86 (ASCII)
        # h = 0*31+83 = 83
        # h = 83*31+65 = 2638
        # h = 2638*31+86 = 81864
        # All positive, no overflow — result should be 81864
        assert _hash_prefix("SAV") == 81_864

    def test_known_overflow_case(self):
        """A specific prefix that overflows in Go int64.

        We verify the Python result is negative (sign bit set), matching
        Go's two's-complement wrapping.
        """
        # Build a prefix long enough to overflow 64 bits
        prefix = "x" * 25
        h = _hash_prefix(prefix)
        # After masking to uint64 and converting to signed, this should be negative
        assert h < 0

    def test_fits_signed_int64_range(self):
        """All results must fit in signed int64 range."""
        for prefix in ["", "A", "SAV", "CHECKING", "x" * 50, "z" * 100]:
            h = _hash_prefix(prefix)
            assert -(1 << 63) <= h < (1 << 63), f"prefix '{prefix}' produced {h}"

    def test_same_prefix_same_hash(self):
        """Identical prefixes always produce the same hash."""
        for _ in range(10):
            assert _hash_prefix("SAVINGS") == 75_605_375_129


# ---------------------------------------------------------------------------
# AccountRepo.create()
# ---------------------------------------------------------------------------

class TestAccountRepoCreate:
    async def test_success(self, mock_session, mock_db):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(42, now))

        repo = AccountRepo(mock_db)
        rec = AccountRecord(tb_account_id=b"\x01", product_id=10, account_number="SAV-001")
        result = await repo.create(mock_session, rec)

        assert result.id == 42
        assert result.opened_at == now
        assert rec.id == 42  # mutated in-place

        mock_session.execute.assert_called_once()
        called_text = mock_session.execute.call_args[0][0]
        assert "INSERT INTO accounts" in str(called_text)
        assert "RETURNING id, opened_at" in str(called_text)

    async def test_mutates_original_record(self, mock_session, mock_db):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(7, now))

        repo = AccountRepo(mock_db)
        rec = AccountRecord(tb_account_id=b"\x02", product_id=5, account_number="CHK-001")
        returned = await repo.create(mock_session, rec)

        assert returned is rec  # same object
        assert rec.id == 7

    async def test_no_rows_raises_runtime_error(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AccountRepo(mock_db)
        rec = AccountRecord(tb_account_id=b"\x03", product_id=1, account_number="SAV-999")

        with pytest.raises(RuntimeError, match="account insert returned no rows"):
            await repo.create(mock_session, rec)

    async def test_sends_correct_parameters(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1, datetime.now()))

        repo = AccountRepo(mock_db)
        rec = AccountRecord(
            tb_account_id=b"\xAA\xBB",
            product_id=99,
            account_number="LOAN-42",
            status="active",
        )
        await repo.create(mock_session, rec)

        params = mock_session.execute.call_args[0][1]
        assert params["tb_account_id"] == b"\xAA\xBB"
        assert params["product_id"] == 99
        assert params["account_number"] == "LOAN-42"
        assert params["status"] == "active"

    async def test_custom_status_preserved(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1, datetime.now()))

        repo = AccountRepo(mock_db)
        rec = AccountRecord(
            tb_account_id=b"\x01",
            product_id=1,
            account_number="SAV-002",
            status="frozen",
        )
        await repo.create(mock_session, rec)

        params = mock_session.execute.call_args[0][1]
        assert params["status"] == "frozen"


# ---------------------------------------------------------------------------
# AccountRepo.create_customer_account()
# ---------------------------------------------------------------------------

class TestAccountRepoCreateCustomerAccount:
    async def test_success(self, mock_session, mock_db):
        repo = AccountRepo(mock_db)
        rec = CustomerAccountRecord(
            customer_ref="CUST-001",
            account_id=42,
            ownership_type="sole",
            role="owner",
        )
        await repo.create_customer_account(mock_session, rec)

        mock_session.execute.assert_called_once()
        called_text = mock_session.execute.call_args[0][0]
        assert "INSERT INTO customer_accounts" in str(called_text)

    async def test_sends_correct_parameters(self, mock_session, mock_db):
        repo = AccountRepo(mock_db)
        rec = CustomerAccountRecord(
            customer_ref="CUST-99",
            account_id=7,
            ownership_type="joint",
            role="signatory",
        )
        await repo.create_customer_account(mock_session, rec)

        params = mock_session.execute.call_args[0][1]
        assert params["customer_ref"] == "CUST-99"
        assert params["account_id"] == 7
        assert params["ownership_type"] == "joint"
        assert params["role"] == "signatory"


# ---------------------------------------------------------------------------
# AccountRepo.next_account_sequence()
# ---------------------------------------------------------------------------

class TestAccountRepoNextAccountSequence:
    async def test_first_account(self, mock_session, mock_db):
        mock_session.execute.side_effect = [
            make_mock_result(),  # advisory lock (no fetch needed)
            make_mock_result(fetchone_val=(0,)),  # COALESCE(MAX(...), 0)
        ]

        repo = AccountRepo(mock_db)
        seq = await repo.next_account_sequence(mock_session, "SAV")

        assert seq == 1
        assert mock_session.execute.call_count == 2

    async def test_existing_accounts_returns_next(self, mock_session, mock_db):
        mock_session.execute.side_effect = [
            make_mock_result(),  # advisory lock
            make_mock_result(fetchone_val=(99,)),  # MAX = 99
        ]

        repo = AccountRepo(mock_db)
        seq = await repo.next_account_sequence(mock_session, "CHK")

        assert seq == 100

    async def test_uses_advisory_lock(self, mock_session, mock_db):
        mock_session.execute.side_effect = [
            make_mock_result(),  # advisory lock
            make_mock_result(fetchone_val=(0,)),
        ]

        repo = AccountRepo(mock_db)
        await repo.next_account_sequence(mock_session, "SAV")

        first_call = mock_session.execute.call_args_list[0]
        assert "pg_advisory_xact_lock" in str(first_call[0][0])

    async def test_uses_coalesce_max_pattern(self, mock_session, mock_db):
        mock_session.execute.side_effect = [
            make_mock_result(),  # advisory lock
            make_mock_result(fetchone_val=(0,)),
        ]

        repo = AccountRepo(mock_db)
        await repo.next_account_sequence(mock_session, "SAV")

        second_call = mock_session.execute.call_args_list[1]
        sql = str(second_call[0][0])
        assert "COALESCE" in sql
        assert "MAX" in sql
        assert "SUBSTRING" in sql

    async def test_uses_transaction_begin(self, mock_session, mock_db):
        mock_session.execute.side_effect = [
            make_mock_result(),  # advisory lock
            make_mock_result(fetchone_val=(0,)),
        ]

        repo = AccountRepo(mock_db)
        await repo.next_account_sequence(mock_session, "SAV")

        mock_session.begin.assert_called_once()

    async def test_lock_key_from_prefix_hash(self, mock_session, mock_db):
        mock_session.execute.side_effect = [
            make_mock_result(),  # advisory lock
            make_mock_result(fetchone_val=(0,)),
        ]

        repo = AccountRepo(mock_db)
        await repo.next_account_sequence(mock_session, "SAV")

        first_call = mock_session.execute.call_args_list[0]
        lock_key = first_call[0][1]["lock_key"]
        expected = _hash_prefix("SAV")
        assert lock_key == expected

    async def test_pattern_parameter(self, mock_session, mock_db):
        mock_session.execute.side_effect = [
            make_mock_result(),  # advisory lock
            make_mock_result(fetchone_val=(0,)),
        ]

        repo = AccountRepo(mock_db)
        await repo.next_account_sequence(mock_session, "LOAN")

        second_call = mock_session.execute.call_args_list[1]
        assert second_call[0][1]["pattern"] == "LOAN-%"


# ---------------------------------------------------------------------------
# AccountRepo.get_by_tb_account_id()
# ---------------------------------------------------------------------------

class TestAccountRepoGetByTbAccountId:
    async def test_found(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_account_row(opened_at=now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = AccountRepo(mock_db)
        result = await repo.get_by_tb_account_id(mock_session, b"\x01\x02")

        assert result is not None
        assert result.id == 1
        assert result.tb_account_id == b"\x01\x02"
        assert result.account_number == "SAV-001"
        assert result.status == "active"
        assert result.product_code == "SAVINGS"
        assert result.currency == "USD"

    async def test_not_found_none(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AccountRepo(mock_db)
        result = await repo.get_by_tb_account_id(mock_session, b"\xFF")

        assert result is None

    async def test_noresultfound_exception(self, mock_session, mock_db):
        mock_session.execute.side_effect = NoResultFound()

        repo = AccountRepo(mock_db)
        result = await repo.get_by_tb_account_id(mock_session, b"\xFF")

        assert result is None

    async def test_uses_join_query(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AccountRepo(mock_db)
        await repo.get_by_tb_account_id(mock_session, b"\x01")

        called_text = mock_session.execute.call_args[0][0]
        sql = str(called_text)
        assert "FROM accounts a" in sql
        assert "JOIN products p" in sql

    async def test_sends_tb_account_id_param(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AccountRepo(mock_db)
        await repo.get_by_tb_account_id(mock_session, b"\xAA\xBB")

        params = mock_session.execute.call_args[0][1]
        assert params["tb_account_id"] == b"\xAA\xBB"

    async def test_maps_all_product_fields(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_account_row(
            id=5, tb_account_id=b"\x99", account_number="CHK-042",
            product_id=20, product_code="CHECKING", product_name="Checking Account",
            category="deposit", tb_account_code=2001, currency="EUR", tb_ledger=978,
            opened_at=now, closed_at=None, status="active",
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        repo = AccountRepo(mock_db)
        result = await repo.get_by_tb_account_id(mock_session, b"\x99")

        assert result.product_name == "Checking Account"
        assert result.category == "deposit"
        assert result.tb_account_code == 2001
        assert result.tb_ledger == 978


# ---------------------------------------------------------------------------
# AccountRepo.get_owners_by_account_id()
# ---------------------------------------------------------------------------

class TestAccountRepoGetOwnersByAccountId:
    async def test_with_owners(self, mock_session, mock_db):
        rows = [
            _make_owner_row("CUST-001", "Alice", "sole", "owner"),
            _make_owner_row("CUST-002", "Bob", "joint", "signatory"),
        ]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = AccountRepo(mock_db)
        result = await repo.get_owners_by_account_id(mock_session, 42)

        assert len(result) == 2
        assert result[0].customer_ref == "CUST-001"
        assert result[0].name == "Alice"
        assert result[0].ownership_type == "sole"
        assert result[1].role == "signatory"

    async def test_empty_result(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        result = await repo.get_owners_by_account_id(mock_session, 99)

        assert result == []
        assert isinstance(result, list)

    async def test_single_owner(self, mock_session, mock_db):
        rows = [_make_owner_row("CUST-10", "Charlie", "sole", "owner")]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = AccountRepo(mock_db)
        result = await repo.get_owners_by_account_id(mock_session, 1)

        assert len(result) == 1
        assert result[0].name == "Charlie"

    async def test_uses_correct_join(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.get_owners_by_account_id(mock_session, 5)

        sql = str(mock_session.execute.call_args[0][0])
        assert "customer_accounts ca" in sql
        assert "JOIN customers c" in sql

    async def test_sends_account_id_param(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.get_owners_by_account_id(mock_session, 77)

        params = mock_session.execute.call_args[0][1]
        assert params["account_id"] == 77


# ---------------------------------------------------------------------------
# AccountRepo.close_account()
# ---------------------------------------------------------------------------

class TestAccountRepoCloseAccount:
    async def test_active_to_closed(self, mock_session, mock_db):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(now,))

        repo = AccountRepo(mock_db)
        closed_at = await repo.close_account(mock_session, 42)

        assert closed_at == now
        mock_session.execute.assert_called_once()

    async def test_already_closed_returns_none(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        repo = AccountRepo(mock_db)
        result = await repo.close_account(mock_session, 42)

        assert result is None

    async def test_noresultfound_returns_none(self, mock_session, mock_db):
        mock_session.execute.side_effect = NoResultFound()

        repo = AccountRepo(mock_db)
        result = await repo.close_account(mock_session, 42)

        assert result is None

    async def test_uses_status_guard(self, mock_session, mock_db):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(now,))

        repo = AccountRepo(mock_db)
        await repo.close_account(mock_session, 99)

        sql = str(mock_session.execute.call_args[0][0])
        assert "status = 'closed'" in sql
        assert "AND status = 'active'" in sql

    async def test_sends_account_id(self, mock_session, mock_db):
        now = datetime.now()
        mock_session.execute.return_value = make_mock_result(fetchone_val=(now,))

        repo = AccountRepo(mock_db)
        await repo.close_account(mock_session, 123)

        params = mock_session.execute.call_args[0][1]
        assert params["id"] == 123

    async def test_returns_closed_at_value(self, mock_session, mock_db):
        now = datetime(2025, 6, 15, 10, 30, 0)
        mock_session.execute.return_value = make_mock_result(fetchone_val=(now,))

        repo = AccountRepo(mock_db)
        result = await repo.close_account(mock_session, 1)

        assert result == now


# ---------------------------------------------------------------------------
# AccountRepo.list_by_customer_ref()
# ---------------------------------------------------------------------------

class TestAccountRepoListByCustomerRef:
    async def test_first_page(self, mock_session, mock_db):
        rows = [
            _make_account_row(id=1, account_number="SAV-001"),
            _make_account_row(id=2, account_number="SAV-002"),
        ]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = AccountRepo(mock_db)
        result = await repo.list_by_customer_ref(mock_session, "CUST-001")

        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].account_number == "SAV-002"

    async def test_cursor_pagination(self, mock_session, mock_db):
        rows = [_make_account_row(id=3, account_number="SAV-003")]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = AccountRepo(mock_db)
        result = await repo.list_by_customer_ref(mock_session, "CUST-001", cursor=2)

        assert len(result) == 1
        assert result[0].id == 3

        params = mock_session.execute.call_args[0][1]
        assert params["cursor"] == 2

    async def test_limit_plus_one(self, mock_session, mock_db):
        rows = [_make_account_row(id=i) for i in range(1, 22)]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        repo = AccountRepo(mock_db)
        result = await repo.list_by_customer_ref(mock_session, "CUST-001", limit=20)

        assert len(result) == 21
        params = mock_session.execute.call_args[0][1]
        assert params["limit"] == 21

    async def test_empty_result(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        result = await repo.list_by_customer_ref(mock_session, "CUST-999")

        assert result == []

    async def test_default_limit_is_20(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.list_by_customer_ref(mock_session, "CUST-001")

        params = mock_session.execute.call_args[0][1]
        assert params["limit"] == 21

    async def test_custom_limit(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.list_by_customer_ref(mock_session, "CUST-001", limit=5)

        params = mock_session.execute.call_args[0][1]
        assert params["limit"] == 6

    async def test_sends_customer_ref(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.list_by_customer_ref(mock_session, "CUST-ABC")

        params = mock_session.execute.call_args[0][1]
        assert params["customer_ref"] == "CUST-ABC"

    async def test_uses_customer_accounts_join(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.list_by_customer_ref(mock_session, "CUST-001")

        sql = str(mock_session.execute.call_args[0][0])
        assert "customer_accounts ca" in sql
        assert "JOIN accounts a" in sql
        assert "JOIN products p" in sql

    async def test_uses_cursor_gt_filter(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.list_by_customer_ref(mock_session, "CUST-001", cursor=5)

        sql = str(mock_session.execute.call_args[0][0])
        assert "a.id > :cursor" in sql

    async def test_uses_order_by_id_asc(self, mock_session, mock_db):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        repo = AccountRepo(mock_db)
        await repo.list_by_customer_ref(mock_session, "CUST-001")

        sql = str(mock_session.execute.call_args[0][0])
        assert "ORDER BY a.id ASC" in sql

    async def test_maps_all_fields(self, mock_session, mock_db):
        now = datetime.now()
        row = _make_account_row(
            id=10, tb_account_id=b"\xCC", account_number="SAV-010",
            status="active", opened_at=now, closed_at=None,
            product_id=3, product_code="SAVINGS", product_name="Savings",
            category="deposit", tb_account_code=2101, currency="SGD", tb_ledger=702,
        )
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        repo = AccountRepo(mock_db)
        result = await repo.list_by_customer_ref(mock_session, "CUST-001")

        assert len(result) == 1
        r = result[0]
        assert r.id == 10
        assert r.tb_account_id == b"\xCC"
        assert r.currency == "SGD"
        assert r.tb_ledger == 702
