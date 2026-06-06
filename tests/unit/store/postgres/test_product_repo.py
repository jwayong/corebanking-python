"""Unit tests for product_repo module-level functions.

Tests verify idempotent seeding, currency lookup, and row mapping
using mocked async sessions — no real database required.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from cbs.domain.errors import ErrNotFound
from cbs.store.postgres.product_repo import (
    ProductRecord,
    count_products,
    get_by_code,
    seed_products,
    system_accounts_exist_for_currency,
)
from tests.unit.store.postgres.fixtures import make_mock_result, make_mock_row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_product_row(
    id=1, code="SAVINGS", name="Savings Account", category="deposit",
    tb_account_code=2101, currency="USD", tb_ledger=840,
    interest_rate=None, is_active=True,
):
    return make_mock_row(
        id=id, code=code, name=name, category=category,
        tb_account_code=tb_account_code, currency=currency, tb_ledger=tb_ledger,
        interest_rate=interest_rate, is_active=is_active,
    )


def _make_currency_info(code="USD", ledger=840):
    from cbs.domain.currency import CurrencyInfo
    return CurrencyInfo(code=code, ledger=ledger, scale=2, name="US Dollar")


# ---------------------------------------------------------------------------
# system_accounts_exist_for_currency()
# ---------------------------------------------------------------------------

class TestSystemAccountsExistForCurrency:
    async def test_exists(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=3)

        result = await system_accounts_exist_for_currency(mock_session, "USD")
        assert result is True

    async def test_does_not_exist(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        result = await system_accounts_exist_for_currency(mock_session, "EUR")
        assert result is False

    async def test_sends_currency_param(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        await system_accounts_exist_for_currency(mock_session, "GBP")
        params = mock_session.execute.call_args[0][1]
        assert params["currency"] == "GBP"

    async def test_uses_count_query(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        await system_accounts_exist_for_currency(mock_session, "USD")
        sql = str(mock_session.execute.call_args[0][0])
        assert "COUNT(*)" in sql
        assert "system_accounts" in sql


# ---------------------------------------------------------------------------
# seed_products()
# ---------------------------------------------------------------------------

class TestSeedProducts:
    def _make_product(
        self, code="SAVINGS", name="Savings", fees=None,
        currency="USD", interest_rate=0.0,
    ):
        from cbs.domain.products import Fee, Product
        return Product(
            code=code, name=name, category="deposit", account_code=2101,
            currency=currency, fees=fees or [], is_active=True,
            interest_rate=interest_rate,
        )

    async def test_insert_new_product_no_fees(self, mock_session):
        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),  # EXISTS — not exists
            make_mock_result(),                    # product INSERT
        ]

        count = await seed_products(mock_session, [self._make_product()])
        assert count == 1

    async def test_skip_existing_product(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=True)

        count = await seed_products(mock_session, [self._make_product()])
        assert count == 0
        assert mock_session.execute.call_count == 1

    async def test_insert_with_fees(self, mock_session):
        from cbs.domain.products import Fee

        fees = [Fee(type="monthly", description="Monthly fee", amount=500)]
        product = self._make_product(fees=fees)

        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),  # EXISTS — not exists
            make_mock_result(scalar_val=10),     # fee_schedule INSERT returns id 10
            make_mock_result(),                   # product INSERT
        ]

        count = await seed_products(mock_session, [product])
        assert count == 1
        assert mock_session.execute.call_count == 3

    async def test_fee_schedule_name_format(self, mock_session):
        from cbs.domain.products import Fee

        fees = [Fee(type="monthly", description="Monthly fee", amount=500)]
        product = self._make_product(code="GOLD_SAVINGS", fees=fees)

        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),
            make_mock_result(scalar_val=5),
            make_mock_result(),
        ]

        await seed_products(mock_session, [product])

        second_call = mock_session.execute.call_args_list[1][0][1]
        assert second_call["name"] == "GOLD_SAVINGS_fees"

    async def test_multiple_products_mixed(self, mock_session):
        products = [
            self._make_product(code="SAVINGS"),   # new — needs insert
            self._make_product(code="CHECKING"),  # exists — skip
        ]

        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),  # SAVINGS — not exists
            make_mock_result(),                   # SAVINGS INSERT
            make_mock_result(scalar_val=True),   # CHECKING — exists, skip
        ]

        count = await seed_products(mock_session, products)
        assert count == 1

    async def test_empty_list(self, mock_session):
        count = await seed_products(mock_session, [])
        assert count == 0
        assert mock_session.execute.call_count == 0

    async def test_uses_transaction_begin(self, mock_session):
        product = self._make_product()
        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),
            make_mock_result(),
        ]

        await seed_products(mock_session, [product])
        mock_session.begin.assert_called_once()

    async def test_looks_up_currency(self, mock_session):
        product = self._make_product(currency="EUR")
        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),
            make_mock_result(),
        ]

        with patch("cbs.store.postgres.product_repo.lookup_currency") as mock_lookup:
            mock_lookup.return_value = _make_currency_info(code="EUR", ledger=978)

            await seed_products(mock_session, [product])

            mock_lookup.assert_called_once_with("EUR")

    async def test_interest_rate_zero_becomes_none(self, mock_session):
        product = self._make_product(interest_rate=0.0)
        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),
            make_mock_result(),
        ]

        with patch("cbs.store.postgres.product_repo.lookup_currency") as mock_lookup:
            mock_lookup.return_value = _make_currency_info()

            await seed_products(mock_session, [product])

        third_call = mock_session.execute.call_args_list[1][0][1]
        assert third_call["interest_rate"] is None

    async def test_interest_rate_non_zero_preserved(self, mock_session):
        product = self._make_product(interest_rate=0.05)
        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),
            make_mock_result(),
        ]

        with patch("cbs.store.postgres.product_repo.lookup_currency") as mock_lookup:
            mock_lookup.return_value = _make_currency_info()

            await seed_products(mock_session, [product])

        third_call = mock_session.execute.call_args_list[1][0][1]
        assert third_call["interest_rate"] == 0.05

    async def test_fee_schedule_json_format(self, mock_session):
        import json
        from cbs.domain.products import Fee

        fees = [Fee(type="monthly", description="Monthly fee", amount=500)]
        product = self._make_product(fees=fees)

        mock_session.execute.side_effect = [
            make_mock_result(scalar_val=False),
            make_mock_result(scalar_val=10),
            make_mock_result(),
        ]

        await seed_products(mock_session, [product])

        second_call = mock_session.execute.call_args_list[1][0][1]
        fees_json = json.loads(second_call["fees"])
        assert len(fees_json) == 1
        assert fees_json[0]["type"] == "monthly"
        assert fees_json[0]["amount"] == 500


# ---------------------------------------------------------------------------
# count_products()
# ---------------------------------------------------------------------------

class TestCountProducts:
    async def test_returns_count(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=42)

        result = await count_products(mock_session)
        assert result == 42

    async def test_zero_count(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        result = await count_products(mock_session)
        assert result == 0

    async def test_uses_count_query(self, mock_session):
        mock_session.execute.return_value = make_mock_result(scalar_val=0)

        await count_products(mock_session)
        sql = str(mock_session.execute.call_args[0][0])
        assert "COUNT(*)" in sql
        assert "products" in sql


# ---------------------------------------------------------------------------
# get_by_code()
# ---------------------------------------------------------------------------

class TestGetByCode:
    async def test_found(self, mock_session):
        row = _make_product_row(
            id=5, code="SAVINGS", name="Savings Account", category="deposit",
            tb_account_code=2101, currency="USD", tb_ledger=840,
            interest_rate=0.035, is_active=True,
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_by_code(mock_session, "SAVINGS")

        assert result.id == 5
        assert result.code == "SAVINGS"
        assert result.name == "Savings Account"
        assert result.category == "deposit"
        assert result.tb_account_code == 2101
        assert result.currency == "USD"
        assert result.tb_ledger == 840
        assert result.interest_rate == 0.035
        assert result.is_active is True

    async def test_found_null_interest_rate(self, mock_session):
        row = _make_product_row(interest_rate=None)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_by_code(mock_session, "CHECKING")
        assert result.interest_rate is None

    async def test_not_found_raises_err_not_found(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        with pytest.raises(Exception) as exc_info:
            await get_by_code(mock_session, "NONEXISTENT")
        assert exc_info.value is ErrNotFound

    async def test_sends_code_param(self, mock_session):
        row = _make_product_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_by_code(mock_session, "GOLD_SAVINGS")
        params = mock_session.execute.call_args[0][1]
        assert params["code"] == "GOLD_SAVINGS"

    async def test_uses_correct_select(self, mock_session):
        row = _make_product_row()
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_by_code(mock_session, "SAVINGS")
        sql = str(mock_session.execute.call_args[0][0])
        assert "FROM products" in sql
        assert "WHERE code = :code" in sql
