"""Tests for system account definitions."""

import pytest

from cbs.domain.system_accounts import SYSTEM_ACCOUNTS, SystemAccountDef


class TestSystemAccounts:
    """Test system account definitions."""

    def test_count(self):
        """18 system accounts per currency (matching Go)."""
        assert len(SYSTEM_ACCOUNTS) == 18

    def test_all_have_required_fields(self):
        """Each account has code, name, and flags."""
        for sa in SYSTEM_ACCOUNTS:
            assert isinstance(sa, SystemAccountDef)
            assert sa.code > 0
            assert sa.name
            assert isinstance(sa.tb_flags_debits, bool)
            assert isinstance(sa.tb_flags_credits, bool)
            assert isinstance(sa.tb_flags_history, bool)

    def test_asset_accounts_have_debits_flag(self):
        """Asset accounts (1000-1999) use CreditsMustNotExceedDebits."""
        cash_vault = next(sa for sa in SYSTEM_ACCOUNTS if sa.code == 1101)
        assert cash_vault.tb_flags_debits is True
        assert cash_vault.name == "Cash Vault"

    def test_liability_accounts_have_credits_flag(self):
        """Liability accounts (2000-2999) use DebitsMustNotExceedCredits."""
        accrued_interest_payable = next(sa for sa in SYSTEM_ACCOUNTS if sa.code == 2201)
        assert accrued_interest_payable.tb_flags_credits is True

    def test_equity_accounts_bidirectional(self):
        """Equity accounts (3000-3999) are bidirectional."""
        share_capital = next(sa for sa in SYSTEM_ACCOUNTS if sa.code == 3101)
        assert share_capital.tb_flags_debits is False
        assert share_capital.tb_flags_credits is False

    def test_all_have_history_except_liquidity(self):
        """All accounts have History except Liquidity Pool."""
        for sa in SYSTEM_ACCOUNTS:
            if sa.code == 1601:  # Liquidity Pool
                assert sa.tb_flags_history is False
            else:
                assert sa.tb_flags_history is True

    def test_unique_codes(self):
        """All account codes are unique."""
        codes = [sa.code for sa in SYSTEM_ACCOUNTS]
        assert len(codes) == len(set(codes))

    def test_expected_accounts_present(self):
        """Verify key accounts from Go spec are present."""
        codes = {sa.code for sa in SYSTEM_ACCOUNTS}
        expected = {
            1101,  # Cash Vault
            1110,  # Central Bank Reserve
            1120,  # Correspondent Nostro
            1201,  # Settlement Account
            1301,  # Suspense Asset
            1501,  # Accrued Interest Receivable (Loans)
            1601,  # Liquidity Pool
            2201,  # Accrued Interest Payable (Deposits)
            2301,  # Customer Payables
            3101,  # Share Capital
            3130,  # Current Year P&L
            4101,  # Interest Income (Loans)
            4110,  # Fee Income
            5101,  # Interest Expense (Deposits)
            5130,  # Loan Write-Off Expense
            6101,  # Suspense Transaction
            6201,  # Clearing Outbound
            6202,  # Clearing Inbound
        }
        assert codes == expected
