"""Product domain model — definitions, validation, and YAML loading."""

from __future__ import annotations

import msgspec
import yaml  # pyright: ignore[reportMissingImports]

from cbs.domain.errors import ValidationError


# Product category constants
CategoryDeposit: str = "deposit"
CategoryLoan: str = "loan"


class Fee(msgspec.Struct, frozen=True):
    """Fee entry within a product definition."""

    type: str
    description: str
    amount: int


class Product(msgspec.Struct):
    """Banking product definition for seeding.

    Loaded from YAML product files and validated before insertion into PostgreSQL.
    """

    code: str
    name: str
    category: str
    account_code: int
    currency: str
    interest_rate: float = 0.0
    interest_basis: str | None = None
    min_balance: int | None = None
    max_balance: int | None = None
    fees: list[Fee] = []
    is_active: bool = True

    def validate(self) -> None:
        """Validate that the product fields are consistent.

        Raises:
            ValidationError: If any field is invalid or inconsistent.
        """
        if not self.code:
            raise ValidationError("product code is required")
        if not self.name:
            raise ValidationError(f"product name is required for {self.code!r}")
        if self.category not in (CategoryDeposit, CategoryLoan):
            raise ValidationError(
                f"product {self.code!r}: invalid category {self.category!r} "
                f"(must be deposit or loan)"
            )
        if not self.currency:
            raise ValidationError(f"product {self.code!r}: currency is required")

        _validate_account_code(self.code, self.category, self.account_code)

        if not (-0.05 <= self.interest_rate <= 1.0):
            raise ValidationError(
                f"product {self.code!r}: interest_rate {self.interest_rate} "
                f"out of bounds (expected -0.05 to 1.0)"
            )

        if (
            self.min_balance is not None
            and self.max_balance is not None
            and self.min_balance > self.max_balance
        ):
            raise ValidationError(
                f"product {self.code!r}: min_balance ({self.min_balance}) "
                f"exceeds max_balance ({self.max_balance})"
            )

        for i, fee in enumerate(self.fees):
            if not fee.type:
                raise ValidationError(
                    f"product {self.code!r}: fee[{i}] type is required"
                )
            if fee.amount <= 0:
                raise ValidationError(
                    f"product {self.code!r}: fee[{i}] amount must be positive, got {fee.amount}"
                )


def _validate_account_code(code: str, category: str, account_code: int) -> None:
    """Ensure the account_code matches the product category.

    Deposit products use 2000–2999, loan products use 1400–1499.
    """
    if category == CategoryDeposit:
        if not (2000 <= account_code <= 2999):
            raise ValidationError(
                f"product {code!r}: deposit account_code must be in 2000–2999 range, got {account_code}"
            )
    elif category == CategoryLoan:
        if not (1400 <= account_code <= 1499):
            raise ValidationError(
                f"product {code!r}: loan account_code must be in 1400–1499 range, got {account_code}"
            )


def load_products_from_yaml(file_path: str) -> list[Product]:
    """Load and validate products from a YAML file.

    Args:
        file_path: Path to the YAML product definitions file.

    Returns:
        List of validated Product objects.

    Raises:
        ValidationError: If any product fails validation.
    """
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)

    if not data or "products" not in data:
        raise ValidationError("YAML file must contain a 'products' key")

    products: list[Product] = []
    for item in data["products"]:
        product = Product(
            code=item.get("code", ""),
            name=item.get("name", ""),
            category=item.get("category", ""),
            account_code=item.get("account_code", 0),
            currency=item.get("currency", ""),
            interest_rate=float(item.get("interest_rate", 0)),
            interest_basis=item.get("interest_basis"),
            min_balance=item.get("min_balance"),
            max_balance=item.get("max_balance"),
            is_active=bool(item.get("is_active", True)),
        )

        # Parse fees if present
        fee_items = item.get("fees") or []
        product.fees = [
            Fee(type=f["type"], description=f.get("description", ""), amount=int(f["amount"]))
            for f in fee_items
        ]

        product.validate()
        products.append(product)

    return products
