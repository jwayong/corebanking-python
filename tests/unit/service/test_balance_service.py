"""Unit tests for BalanceService (business logic layer).

Tests verify UUID validation, error propagation from repository layers,
balance computation for both debit and credit accounts, and the factory
constructor — all using mocked dependencies.

Mirrors the style of :mod:`tests.unit.service.test_customer_service`.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cbs.domain.accounts import ComputeBalanceResult
from cbs.domain.errors import ErrNotFound, ValidationError
from cbs.service.balance_service import BalanceService, NewBalanceService
from cbs.store.postgres.account_repo import AccountWithProduct


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_UUID = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"


def _make_pg_account(
    tb_account_code=2110,  # DEPOSIT_SAVINGS — credit balance
    currency="USD",
):
    """Build an AccountWithProduct dataclass for test fixtures."""
    return AccountWithProduct(
        id=1,
        tb_account_id=b"",
        account_number="SAV-001",
        status="active",
        opened_at=None,
        closed_at=None,
        product_id=1,
        product_code="SAVINGS",
        product_name="Savings Account",
        category="deposit",
        tb_account_code=tb_account_code,
        currency=currency,
        tb_ledger=840,
    )


def _make_mock_tb_repo():
    """Create a mock TB account repo with AsyncMock methods."""
    repo = MagicMock()
    repo.lookup_account = AsyncMock()
    return repo


def _make_mock_pg_repo():
    """Create a mock PG account repo with AsyncMock methods."""
    repo = MagicMock()
    repo.get_by_tb_account_id = AsyncMock()
    return repo


# ---------------------------------------------------------------------------
# BalanceService.get()
# ---------------------------------------------------------------------------


class TestBalanceServiceGet:
    """Tests for ``BalanceService.get()``."""

    @patch("cbs.service.balance_service.compute_balance")
    @patch("cbs.util.uuid.uuid_to_uint128")
    async def test_success_with_tb_account(
        self, mock_uuid_to_uint128, mock_compute_balance, mock_session
    ):
        """Happy path: TB account found, balance computed for credit-balance account."""
        import uuid as _uuid

        acct_uuid = _uuid.UUID(SAMPLE_UUID)
        tb_id_bytes = (
            b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        )
        mock_uuid_to_uint128.return_value = tb_id_bytes

        # Credit-balance account (liability): posted = credits - debits
        mock_compute_balance.return_value = ComputeBalanceResult(
            posted=400, pending=10, available=390
        )

        pg_acct = _make_pg_account(tb_account_code=2110, currency="USD")
        pg_repo = _make_mock_pg_repo()
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_acct = {
            "debits_posted": 100,
            "credits_posted": 500,
            "debits_pending": 20,
            "credits_pending": 10,
        }
        tb_repo = _make_mock_tb_repo()
        tb_repo.lookup_account.return_value = tb_acct

        svc = BalanceService(tb_repo, pg_repo)
        result = await svc.get(mock_session, SAMPLE_UUID)

        # Verify BalanceResponse fields
        assert result.account_id == SAMPLE_UUID
        assert result.posted_balance == 400
        assert result.pending_amount == 10
        assert result.available_balance == 390
        assert result.currency == "USD"
        assert result.scale == 2

        # Verify PG repo was called with uuid bytes (big-endian)
        pg_repo.get_by_tb_account_id.assert_awaited_once_with(
            mock_session, acct_uuid.bytes
        )

        # Verify TB lookup was called with uint128 bytes
        mock_uuid_to_uint128.assert_called_once_with(acct_uuid)
        tb_repo.lookup_account.assert_awaited_once_with(tb_id_bytes)

        # Verify compute_balance was called with correct args
        mock_compute_balance.assert_called_once_with(100, 500, 20, 10, 2110)

    @patch("cbs.service.balance_service.compute_balance")
    @patch("cbs.util.uuid.uuid_to_uint128")
    async def test_success_debit_balance_account(
        self, mock_uuid_to_uint128, mock_compute_balance, mock_session
    ):
        """Balance computed correctly for debit-balance account (asset)."""
        tb_id_bytes = b"\x00" * 16
        mock_uuid_to_uint128.return_value = tb_id_bytes

        # Debit-balance account (asset): posted = debits - credits
        mock_compute_balance.return_value = ComputeBalanceResult(
            posted=200, pending=50, available=150
        )

        pg_acct = _make_pg_account(tb_account_code=1101, currency="EUR")  # CASH_VAULT
        pg_repo = _make_mock_pg_repo()
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_acct = {
            "debits_posted": 1000,
            "credits_posted": 800,
            "debits_pending": 100,
            "credits_pending": 50,
        }
        tb_repo = _make_mock_tb_repo()
        tb_repo.lookup_account.return_value = tb_acct

        svc = BalanceService(tb_repo, pg_repo)
        result = await svc.get(mock_session, SAMPLE_UUID)

        assert result.posted_balance == 200
        assert result.pending_amount == 50
        assert result.available_balance == 150
        assert result.currency == "EUR"
        assert result.scale == 2

        # Verify compute_balance received the correct account code
        mock_compute_balance.assert_called_once_with(1000, 800, 100, 50, 1101)

    @patch("cbs.util.uuid.uuid_to_uint128")
    async def test_success_tb_account_not_found(
        self, mock_uuid_to_uint128, mock_session
    ):
        """When TB account is None, returns zero balances."""
        mock_uuid_to_uint128.return_value = b"\x00" * 16

        pg_acct = _make_pg_account(tb_account_code=2110, currency="USD")
        pg_repo = _make_mock_pg_repo()
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_repo = _make_mock_tb_repo()
        tb_repo.lookup_account.return_value = None

        svc = BalanceService(tb_repo, pg_repo)
        result = await svc.get(mock_session, SAMPLE_UUID)

        assert result.account_id == SAMPLE_UUID
        assert result.posted_balance == 0
        assert result.pending_amount == 0
        assert result.available_balance == 0
        assert result.currency == "USD"
        assert result.scale == 2

    async def test_validation_error_empty_id(self, mock_session):
        """Empty id string raises ValidationError before touching repos."""
        pg_repo = _make_mock_pg_repo()
        tb_repo = _make_mock_tb_repo()

        svc = BalanceService(tb_repo, pg_repo)

        with pytest.raises(ValidationError, match="account id is required"):
            await svc.get(mock_session, "")
        pg_repo.get_by_tb_account_id.assert_not_called()
        tb_repo.lookup_account.assert_not_called()

    async def test_validation_error_invalid_uuid(self, mock_session):
        """Non-UUID string raises ValidationError before touching repos."""
        pg_repo = _make_mock_pg_repo()
        tb_repo = _make_mock_tb_repo()

        svc = BalanceService(tb_repo, pg_repo)

        with pytest.raises(ValidationError, match="account id must be a valid UUID"):
            await svc.get(mock_session, "not-a-uuid")
        pg_repo.get_by_tb_account_id.assert_not_called()
        tb_repo.lookup_account.assert_not_called()

    async def test_err_not_found(self, mock_session):
        """When PG repo returns None, service raises ErrNotFound."""
        pg_repo = _make_mock_pg_repo()
        pg_repo.get_by_tb_account_id.return_value = None

        tb_repo = _make_mock_tb_repo()

        svc = BalanceService(tb_repo, pg_repo)

        with pytest.raises(Exception) as exc_info:
            await svc.get(mock_session, SAMPLE_UUID)
        assert exc_info.value is ErrNotFound
        tb_repo.lookup_account.assert_not_called()

    async def test_runtime_error_pg_repo_failure(self, mock_session):
        """Unexpected PG repo exception is wrapped in RuntimeError."""
        pg_repo = _make_mock_pg_repo()
        db_error = ConnectionError("connection refused")
        pg_repo.get_by_tb_account_id.side_effect = db_error

        tb_repo = _make_mock_tb_repo()

        svc = BalanceService(tb_repo, pg_repo)

        with pytest.raises(RuntimeError, match="get account: connection refused"):
            await svc.get(mock_session, SAMPLE_UUID)
        tb_repo.lookup_account.assert_not_called()

    @patch("cbs.util.uuid.uuid_to_uint128")
    async def test_runtime_error_tb_lookup_failure(
        self, mock_uuid_to_uint128, mock_session
    ):
        """When TB lookup fails, wrapped in RuntimeError."""
        mock_uuid_to_uint128.return_value = b"\x00" * 16

        pg_acct = _make_pg_account(tb_account_code=2110, currency="USD")
        pg_repo = _make_mock_pg_repo()
        pg_repo.get_by_tb_account_id.return_value = pg_acct

        tb_error = ConnectionError("tb cluster unreachable")
        tb_repo = _make_mock_tb_repo()
        tb_repo.lookup_account.side_effect = tb_error

        svc = BalanceService(tb_repo, pg_repo)

        with pytest.raises(
            RuntimeError, match="lookup tb account: tb cluster unreachable"
        ):
            await svc.get(mock_session, SAMPLE_UUID)


# ---------------------------------------------------------------------------
# NewBalanceService factory
# ---------------------------------------------------------------------------


class TestNewBalanceService:
    """Tests for the ``NewBalanceService`` factory function."""

    def test_creates_service_with_all_args(self):
        """Factory returns BalanceService instance with all dependencies."""
        tb_repo = MagicMock()
        pg_repo = MagicMock()
        logger = MagicMock()

        svc = NewBalanceService(tb_repo, pg_repo, logger)

        assert isinstance(svc, BalanceService)
        assert svc._tb_account_repo is tb_repo
        assert svc._pg_account_repo is pg_repo

    def test_creates_service_without_logger(self):
        """Factory works with logger omitted — defaults to structlog."""
        tb_repo = MagicMock()
        pg_repo = MagicMock()

        svc = NewBalanceService(tb_repo, pg_repo)

        assert isinstance(svc, BalanceService)
        assert svc._tb_account_repo is tb_repo
        assert svc._pg_account_repo is pg_repo


# ---------------------------------------------------------------------------
# _uint128_to_int helper (used internally by BalanceService.get)
# ---------------------------------------------------------------------------


class TestUint128ToInt:
    """Tests for the ``_uint128_to_int`` helper."""

    def test_int_value_passthrough(self):
        """Int values are returned as-is (mock-friendly)."""
        from cbs.service.balance_service import _uint128_to_int

        assert _uint128_to_int(42) == 42
        assert _uint128_to_int(0) == 0

    def test_bytes_little_endian_conversion(self):
        """16-byte little-endian bytes are converted to int."""
        from cbs.service.balance_service import _uint128_to_int

        # 0x0A = 10 in little-endian: first byte is LSB
        value = bytes([0x0A]) + b"\x00" * 15
        assert _uint128_to_int(value) == 10

    def test_bytes_zero(self):
        """All-zero bytes convert to 0."""
        from cbs.service.balance_service import _uint128_to_int

        assert _uint128_to_int(b"\x00" * 16) == 0
