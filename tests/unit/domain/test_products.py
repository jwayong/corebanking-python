"""Tests for Product validation and YAML loading."""

import pytest

from cbs.domain.errors import ValidationError
from cbs.domain.products import (
    CategoryDeposit,
    CategoryLoan,
    Fee,
    load_products_from_yaml,
    Product,
)


class TestCategoryConstants:
    def test_deposit_category(self):
        assert CategoryDeposit == "deposit"

    def test_loan_category(self):
        assert CategoryLoan == "loan"


class TestProductValidation:
    def test_valid_deposit_product(self):
        p = Product(
            code="SAVINGS",
            name="Savings Account",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
            interest_rate=0.04,
        )
        p.validate()  # should not raise

    def test_valid_loan_product(self):
        p = Product(
            code="PERSONAL_LOAN",
            name="Personal Loan",
            category=CategoryLoan,
            account_code=1401,
            currency="USD",
            interest_rate=0.12,
        )
        p.validate()  # should not raise

    def test_empty_code_raises(self):
        p = Product(
            code="",
            name="Bad",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
        )
        with pytest.raises(ValidationError, match="code"):
            p.validate()

    def test_empty_name_raises(self):
        p = Product(
            code="BAD",
            name="",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
        )
        with pytest.raises(ValidationError, match="name"):
            p.validate()

    def test_invalid_category_raises(self):
        """Category must be 'deposit' or 'loan'."""
        p = Product(
            code="BAD",
            name="Bad",
            category="equity",
            account_code=3101,
            currency="USD",
        )
        with pytest.raises(ValidationError, match="category"):
            p.validate()

    def test_deposit_wrong_account_code_raises(self):
        """Deposit account_code must be 2000-2999."""
        p = Product(
            code="BAD",
            name="Bad",
            category=CategoryDeposit,
            account_code=3101,  # equity range
            currency="USD",
        )
        with pytest.raises(ValidationError, match="account_code"):
            p.validate()

    def test_loan_wrong_account_code_raises(self):
        """Loan account_code must be 1400-1499."""
        p = Product(
            code="BAD",
            name="Bad",
            category=CategoryLoan,
            account_code=2101,  # liability range
            currency="USD",
        )
        with pytest.raises(ValidationError, match="account_code"):
            p.validate()

    def test_negative_interest_rate_raises(self):
        """Interest rate below -5% is invalid."""
        p = Product(
            code="BAD",
            name="Bad",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
            interest_rate=-0.10,  # -10% — below floor
        )
        with pytest.raises(ValidationError, match="interest_rate"):
            p.validate()

    def test_excessive_interest_rate_raises(self):
        """Interest rate above 100% is invalid."""
        p = Product(
            code="BAD",
            name="Bad",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
            interest_rate=1.50,  # 150% — above ceiling
        )
        with pytest.raises(ValidationError, match="interest_rate"):
            p.validate()

    def test_max_below_min_balance_raises(self):
        p = Product(
            code="BAD",
            name="Bad",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
            min_balance=1000,
            max_balance=500,  # less than min
        )
        with pytest.raises(ValidationError, match="min_balance"):
            p.validate()

    def test_empty_currency_raises(self):
        p = Product(
            code="BAD",
            name="Bad",
            category=CategoryDeposit,
            account_code=2101,
            currency="",
        )
        with pytest.raises(ValidationError, match="currency"):
            p.validate()

    def test_fee_validation(self):
        """Product with valid fees should pass."""
        fee = Fee(type="monthly", description="Monthly Maintenance", amount=500)
        p = Product(
            code="SAVINGS",
            name="Savings",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
            interest_rate=0.04,
            fees=[fee],
        )
        p.validate()  # should not raise

    def test_fee_with_zero_amount_raises(self):
        """Fee with amount=0 is invalid."""
        fee = Fee(type="monthly", description="Bad", amount=0)
        p = Product(
            code="SAVINGS",
            name="Savings",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
            fees=[fee],
        )
        with pytest.raises(ValidationError, match="amount"):
            p.validate()

    def test_fee_missing_type_raises(self):
        fee = Fee(type="", description="Bad", amount=500)
        p = Product(
            code="SAVINGS",
            name="Savings",
            category=CategoryDeposit,
            account_code=2101,
            currency="USD",
            fees=[fee],
        )
        with pytest.raises(ValidationError, match="type"):
            p.validate()


class TestLoadProductsFromYaml:
    def test_load_from_yaml_file(self, tmp_path):
        """Test loading products from a YAML file."""
        yaml_file = tmp_path / "products.yaml"
        data = {
            "products": [
                {
                    "code": "SAVINGS",
                    "name": "Savings Account",
                    "category": "deposit",
                    "account_code": 2101,
                    "currency": "USD",
                    "interest_rate": 0.04,
                }
            ]
        }
        with open(yaml_file, "w") as f:
            import yaml

            yaml.dump(data, f)

        products = load_products_from_yaml(str(yaml_file))
        assert len(products) == 1
        assert products[0].code == "SAVINGS"
        assert products[0].name == "Savings Account"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_products_from_yaml("/nonexistent/path.yaml")
