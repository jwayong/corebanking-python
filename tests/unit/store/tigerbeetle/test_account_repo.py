"""Tests for TigerBeetle account repository."""

import pytest

from cbs.domain.accounts import AccountCode
from cbs.store.tigerbeetle.account_repo import (
    AccountRepo,
    _dedup_balances,
    _extract_uuidv7_timestamp_nano,
)


class TestBuildAccountFlags:
    """Test account flag construction from AccountCode."""

    def test_asset_account_flags(self):
        repo = AccountRepo.__new__(AccountRepo)

        # Cash Vault (1101) — asset, debit balance.
        flags = repo.build_account_flags(AccountCode.CASH_VAULT)

        assert flags & 0x01  # debits_must_not_exceed_credits
        assert flags & 0x04  # history
        assert not (flags & 0x02)  # no credits_must_not_exceed_debits

    def test_liability_account_flags(self):
        repo = AccountRepo.__new__(AccountRepo)

        # Deposit Savings (2110) — liability, credit balance.
        flags = repo.build_account_flags(AccountCode.DEPOSIT_SAVINGS)

        assert flags & 0x02  # credits_must_not_exceed_debits
        assert flags & 0x04  # history
        assert not (flags & 0x01)  # no debits_must_not_exceed_credits

    def test_income_account_flags(self):
        repo = AccountRepo.__new__(AccountRepo)

        # Interest Income (4101) — income, credit balance.
        flags = repo.build_account_flags(AccountCode.INC_INTEREST_LOAN)

        assert flags & 0x02  # credits_must_not_exceed_debits
        assert flags & 0x04  # history

    def test_expense_account_flags(self):
        repo = AccountRepo.__new__(AccountRepo)

        # Interest Expense (5101) — expense, debit balance.
        flags = repo.build_account_flags(AccountCode.EXP_INTEREST_DEP)

        assert flags & 0x01  # debits_must_not_exceed_credits
        assert flags & 0x04  # history

    def test_suspense_account_flags(self):
        repo = AccountRepo.__new__(AccountRepo)

        # Suspense (6101) — clearing, credit balance per is_debit_balance rules.
        # 6000-6999 is not in the debit balance range, so gets credits_must_not_exceed_debits.
        flags = repo.build_account_flags(AccountCode.SUSPENSE_TXN)

        assert flags & 0x02  # credits_must_not_exceed_debits
        assert flags & 0x04  # history


class TestCreateAccount:
    """Test account creation."""

    @pytest.mark.asyncio
    async def test_create_account_success(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        tb_id = b"\x01" * 16
        account = {"id": tb_id, "ledger": 840, "flags": 5}
        mock_tb_client.create_accounts.return_value = [{"status": 0}]

        result = await repo.create_account(account)

        assert len(result) == 1
        assert result[0]["status"] == 0

    @pytest.mark.asyncio
    async def test_create_account_exists(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        tb_id = b"\x01" * 16
        account = {"id": tb_id, "ledger": 840, "flags": 5}
        mock_tb_client.create_accounts.return_value = [{"status": 1}]  # AccountExists

        result = await repo.create_account(account)

        assert len(result) == 1
        # AccountExists (status=1) is acceptable.

    @pytest.mark.asyncio
    async def test_create_account_failure(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        tb_id = b"\x01" * 16
        account = {"id": tb_id, "ledger": 840, "flags": 5}
        mock_tb_client.create_accounts.return_value = [{"status": 2}]  # Failure

        with pytest.raises(ValueError, match="TB create account failed"):
            await repo.create_account(account)


class TestLookupAccount:
    """Test account lookup."""

    @pytest.mark.asyncio
    async def test_lookup_account_found(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        tb_id = b"\x01" * 16
        account = {"id": tb_id, "ledger": 840}
        mock_tb_client.lookup_accounts.return_value = [account]

        result = await repo.lookup_account(tb_id)

        assert result is not None
        assert result["id"] == tb_id
        assert result["ledger"] == 840

    @pytest.mark.asyncio
    async def test_lookup_account_not_found(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        tb_id = b"\x01" * 16
        mock_tb_client.lookup_accounts.return_value = []

        result = await repo.lookup_account(tb_id)

        assert result is None


class TestLookupAccounts:
    """Test batch account lookup."""

    @pytest.mark.asyncio
    async def test_lookup_accounts_empty(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        result = await repo.lookup_accounts([])

        assert result == {}
        mock_tb_client.lookup_accounts.assert_not_called()

    @pytest.mark.asyncio
    async def test_lookup_accounts_multiple(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        id1 = b"\x01" * 16
        id2 = b"\x02" * 16
        accounts = [
            {"id": id1, "ledger": 840},
            {"id": id2, "ledger": 978},
        ]
        mock_tb_client.lookup_accounts.return_value = accounts

        result = await repo.lookup_accounts([id1, id2])

        assert len(result) == 2
        assert result[id1]["ledger"] == 840
        assert result[id2]["ledger"] == 978


class TestExtractUUIDV7Timestamp:
    """Test UUIDv7 timestamp extraction."""

    def test_valid_uuidv7_bytes(self):
        # Simulate UUIDv7 with timestamp in first 6 bytes (big-endian).
        b = bytes([0x01, 0x95, 0x00, 0x00, 0x00, 0x00])
        # 0x019500000000 = 684884275200 ms
        ts_nano = _extract_uuidv7_timestamp_nano(b)

        expected_ms = 0x019500000000
        assert ts_nano == expected_ms * 1_000_000

    def test_none_input(self):
        assert _extract_uuidv7_timestamp_nano(None) == 0

    def test_short_input(self):
        assert _extract_uuidv7_timestamp_nano(b"\x01\x02") == 0


class TestDedupBalances:
    """Test balance deduplication."""

    def test_dedup_removes_duplicates(self):
        from tests.unit.store.tigerbeetle.conftest import make_tb_balance

        b1 = make_tb_balance(debits_posted=100, credits_posted=50)
        b2 = make_tb_balance(debits_posted=100, credits_posted=50)  # duplicate
        b3 = make_tb_balance(debits_posted=200, credits_posted=100)

        seen: set[tuple[bytes, bytes]] = set()
        filtered = _dedup_balances([b1, b2, b3], seen)

        assert len(filtered) == 2
        assert filtered[0] is b1
        assert filtered[1] is b3

    def test_dedup_keeps_all_unique(self):
        from tests.unit.store.tigerbeetle.conftest import make_tb_balance

        b1 = make_tb_balance(debits_posted=100, credits_posted=50)
        b2 = make_tb_balance(debits_posted=200, credits_posted=100)
        b3 = make_tb_balance(debits_posted=300, credits_posted=150)

        seen: set[tuple[bytes, bytes]] = set()
        filtered = _dedup_balances([b1, b2, b3], seen)

        assert len(filtered) == 3


class TestGetAccountBalances:
    """Test account balance retrieval with pagination."""

    @pytest.mark.asyncio
    async def test_get_balances_single_page(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        from tests.unit.store.tigerbeetle.conftest import make_tb_balance

        tb_id = b"\x01" * 16
        balances = [
            make_tb_balance(debits_posted=200, timestamp=1700000003_000000000),
            make_tb_balance(debits_posted=100, timestamp=1700000002_000000000),
        ]
        mock_tb_client.get_account_balances.return_value = balances

        result = await repo.get_account_balances(tb_id, limit=10)

        assert len(result) == 2
        # First call should have returned all results.
        assert mock_tb_client.get_account_balances.call_count == 1

    @pytest.mark.asyncio
    async def test_get_balances_with_cursor(self, mock_tb_client):
        repo = AccountRepo(mock_tb_client)

        from tests.unit.store.tigerbeetle.conftest import make_tb_balance

        tb_id = b"\x01" * 16
        # Cursor with timestamp in first 6 bytes.
        cursor = bytes([0x01, 0x95, 0x00, 0x00, 0x00, 0x00] + [0] * 10)

        balances = [
            make_tb_balance(debits_posted=200, timestamp=1700000003_000000000),
        ]
        mock_tb_client.get_account_balances.return_value = balances

        result = await repo.get_account_balances(tb_id, cursor=cursor, limit=10)

        assert len(result) == 1
        # Verify timestamp_max was set from cursor.
        call_kwargs = mock_tb_client.get_account_balances.call_args[1]
        assert "timestamp_max" in call_kwargs
