"""Tests for account balance computation and account code classification."""

import pytest

from cbs.domain.accounts import compute_balance, is_debit_balance


class TestIsDebitBalance:
    """Test account code classification."""

    # Assets (1000-1999) — debit balance
    @pytest.mark.parametrize("code", [1000, 1101, 1401, 1501, 1999])
    def test_asset_codes_are_debit(self, code):
        assert is_debit_balance(code) is True

    # Liabilities (2000-2999) — credit balance
    @pytest.mark.parametrize("code", [2000, 2101, 2201, 2999])
    def test_liability_codes_are_credit(self, code):
        assert is_debit_balance(code) is False

    # Equity (3000-3999) — credit balance
    @pytest.mark.parametrize("code", [3000, 3101, 3999])
    def test_equity_codes_are_credit(self, code):
        assert is_debit_balance(code) is False

    # Income (4000-4999) — credit balance
    @pytest.mark.parametrize("code", [4000, 4101, 4999])
    def test_income_codes_are_credit(self, code):
        assert is_debit_balance(code) is False

    # Expenses (5000-5999) — debit balance
    @pytest.mark.parametrize("code", [5000, 5101, 5999])
    def test_expense_codes_are_debit(self, code):
        assert is_debit_balance(code) is True

    # Suspense (6000-6999) — credit balance
    @pytest.mark.parametrize("code", [6000, 6101, 6999])
    def test_suspense_codes_are_credit(self, code):
        assert is_debit_balance(code) is False

    # Edge cases
    def test_boundary_999(self):
        assert is_debit_balance(999) is False

    def test_boundary_2000(self):
        assert is_debit_balance(2000) is False

    def test_boundary_4999(self):
        assert is_debit_balance(4999) is False

    def test_boundary_6000(self):
        assert is_debit_balance(6000) is False


class TestComputeBalance:
    """Test balance computation for debit and credit accounts."""

    # Debit-balance account (e.g., asset)
    def test_debit_account_basic(self):
        """Debit account: posted = debits - credits."""
        result = compute_balance(
            debits_posted=1000,
            credits_posted=200,
            debits_pending=100,
            credits_pending=50,
            code=1101,  # Cash Vault (debit)
        )
        assert result.posted == 800  # 1000 - 200
        assert result.available == 850  # 800 + 100 - 50
        assert result.pending == -50  # 800 - 850

    def test_debit_account_zero(self):
        """Debit account with zero balances."""
        result = compute_balance(0, 0, 0, 0, code=1101)
        assert result.posted == 0
        assert result.available == 0
        assert result.pending == 0

    def test_debit_account_credits_exceed(self):
        """Debit account where credits exceed debits (negative balance)."""
        result = compute_balance(
            debits_posted=100,
            credits_posted=500,
            debits_pending=0,
            credits_pending=0,
            code=1101,
        )
        assert result.posted == -400
        assert result.available == -400
        assert result.pending == 0

    # Credit-balance account (e.g., liability)
    def test_credit_account_basic(self):
        """Credit account: posted = credits - debits."""
        result = compute_balance(
            debits_posted=200,
            credits_posted=1000,
            debits_pending=50,
            credits_pending=100,
            code=2101,  # Current Account (credit)
        )
        assert result.posted == 800  # 1000 - 200
        assert result.available == 850  # 800 - 50 + 100
        assert result.pending == -50  # 800 - 850

    def test_credit_account_zero(self):
        """Credit account with zero balances."""
        result = compute_balance(0, 0, 0, 0, code=2101)
        assert result.posted == 0
        assert result.available == 0
        assert result.pending == 0

    def test_credit_account_debits_exceed(self):
        """Credit account where debits exceed credits (overdraft)."""
        result = compute_balance(
            debits_posted=500,
            credits_posted=100,
            debits_pending=0,
            credits_pending=0,
            code=2101,
        )
        assert result.posted == -400
        assert result.available == -400
        assert result.pending == 0

    # Realistic scenarios
    def test_savings_account_deposit(self):
        """Savings account after deposit."""
        result = compute_balance(
            debits_posted=0,
            credits_posted=50000,  # $500.00 deposited
            debits_pending=0,
            credits_pending=10000,  # $100.00 pending deposit
            code=2110,  # Savings (credit)
        )
        assert result.posted == 50000
        assert result.available == 60000
        assert result.pending == -10000

    def test_loan_account_repayment(self):
        """Loan account after partial repayment."""
        result = compute_balance(
            debits_posted=1000000,  # $10,000 principal
            credits_posted=200000,  # $2,000 repaid
            debits_pending=0,
            credits_pending=50000,  # $500 pending repayment
            code=1401,  # Personal Loan (debit)
        )
        assert result.posted == 800000  # $8,000 outstanding
        assert result.available == 750000  # $7,500 available
        assert result.pending == 50000

    def test_interest_income_account(self):
        """Interest income accumulation."""
        result = compute_balance(
            debits_posted=0,
            credits_posted=15000,  # $150 accrued
            debits_pending=0,
            credits_pending=5000,  # $50 pending accrual
            code=4101,  # Interest Income (credit)
        )
        assert result.posted == 15000
        assert result.available == 20000
        assert result.pending == -5000

    def test_expense_account(self):
        """Expense account (debit balance)."""
        result = compute_balance(
            debits_posted=30000,  # $300 incurred
            credits_posted=5000,  # $50 reversed
            debits_pending=2000,  # $20 pending
            credits_pending=0,
            code=5101,  # Interest Expense (debit)
        )
        assert result.posted == 25000
        assert result.available == 27000
        assert result.pending == -2000
