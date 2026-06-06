"""Tests for loan EMI calculation and request validation."""

import pytest
from datetime import date

from cbs.domain.errors import ValidationError
from cbs.domain.loans import (
    calculate_emi,
    calculate_maturity_date,
    calculate_next_payment_due,
)


class TestCalculateEMI:
    """Test EMI (Equated Monthly Instalment) calculation."""

    def test_basic_emi(self):
        """Standard loan: P=10000 (cents=$100), 8% annual, 12 months."""
        emi = calculate_emi(10000, 0.08, 12)
        # EMI = P × r × (1+r)^n / ((1+r)^n - 1)
        monthly_rate = 0.08 / 12
        expected = int(round(10000 * monthly_rate * ((1 + monthly_rate) ** 12) / (((1 + monthly_rate) ** 12) - 1)))
        assert emi == expected

    def test_zero_interest_rate(self):
        """Zero interest: EMI = principal // months."""
        emi = calculate_emi(12000, 0.0, 12)
        assert emi == 1000

    def test_emi_returns_int(self):
        """EMI should return integer (cents)."""
        emi = calculate_emi(10000, 0.12, 24)
        assert isinstance(emi, int)

    def test_zero_principal(self):
        """Zero principal returns zero."""
        emi = calculate_emi(0, 0.08, 12)
        assert emi == 0

    def test_zero_term(self):
        """Zero term returns principal."""
        emi = calculate_emi(10000, 0.08, 0)
        assert emi == 10000

    def test_long_tenure_mortgage(self):
        """30-year mortgage produces reasonable monthly payment."""
        # $500,000 at 6% for 360 months
        emi = calculate_emi(50000000, 0.06, 360)
        assert 250000 < emi < 310000  # in cents: $2500-$3100

    def test_single_month(self):
        """One-month loan: principal + one month interest."""
        emi = calculate_emi(10000, 0.24, 1)
        # At 24% annual (2% monthly), EMI should be ~10200
        assert 10190 < emi < 10210


class TestCalculateMaturityDate:
    def test_12_month_maturity(self):
        start = date(2025, 1, 15)
        maturity = calculate_maturity_date(start, 12)
        assert maturity == date(2026, 1, 15)

    def test_36_month_maturity(self):
        start = date(2025, 6, 30)
        maturity = calculate_maturity_date(start, 36)
        assert maturity == date(2028, 6, 30)

    def test_cross_year_boundary(self):
        start = date(2025, 11, 1)
        maturity = calculate_maturity_date(start, 6)
        assert maturity == date(2026, 5, 1)

    def test_day_clamping(self):
        """Jan 31 + 1 month should clamp to Feb 28/29."""
        start = date(2025, 1, 31)
        maturity = calculate_maturity_date(start, 1)
        assert maturity == date(2025, 2, 28)

    def test_zero_months(self):
        start = date(2025, 3, 15)
        maturity = calculate_maturity_date(start, 0)
        assert maturity == start


class TestCalculateNextPaymentDue:
    def test_first_payment(self):
        """Next payment is 1 month from start."""
        start = date(2025, 3, 15)
        next_due = calculate_next_payment_due(start)
        assert next_due == date(2025, 4, 15)

    def test_cross_year(self):
        start = date(2025, 12, 15)
        next_due = calculate_next_payment_due(start)
        assert next_due == date(2026, 1, 15)
