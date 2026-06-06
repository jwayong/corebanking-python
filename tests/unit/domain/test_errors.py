"""Tests for domain exceptions and HTTP status mapping."""

import pytest

from cbs.domain.errors import (
    AccountClosedError,
    AccountFrozenError,
    AccountNotFoundError,
    DomainError,
    ErrInsufficientBalance,
    ErrNotFound,
    FXRateStaleError,
    HoldAlreadyResolvedError,
    HoldExpiredError,
    IdempotencyConflictError,
    InsufficientBalanceError,
    LiquidityPoolUnavailableError,
    LoanNotActiveError,
    NonZeroBalanceError,
    NotFoundError,
    PendingHoldsError,
    ProductInactiveError,
    RepaymentExceedsOutstandingError,
    ServiceUnavailableError,
    TransferError,
    ValidationError as DomainValidationError,
)


class TestDomainErrorBase:
    def test_base_error_attributes(self):
        err = DomainError("test error")
        assert str(err) == "test error"
        assert err.error_code == "DOMAIN_ERROR"

    def test_base_error_http_status(self):
        """Base DomainError defaults to 400."""
        err = DomainError("test")
        assert err.status_code.value == 400


class TestAccountNotFoundError:
    def test_status_404(self):
        err = AccountNotFoundError("acc-123")
        assert err.status_code.value == 404
        assert err.error_code == "ACCOUNT_NOT_FOUND"
        assert "acc-123" in str(err)


class TestAccountClosedError:
    def test_status_409(self):
        err = AccountClosedError("acc-123")
        assert err.status_code.value == 409
        assert err.error_code == "ACCOUNT_CLOSED"


class TestAccountFrozenError:
    def test_status_409(self):
        err = AccountFrozenError("acc-123")
        assert err.status_code.value == 409
        assert err.error_code == "ACCOUNT_FROZEN"


class TestInsufficientBalanceError:
    def test_status_409(self):
        err = InsufficientBalanceError()
        assert err.status_code.value == 409
        assert err.error_code == "INSUFFICIENT_BALANCE"

    def test_with_details(self):
        err = InsufficientBalanceError(available=500, required=1000)
        assert err.details["available"] == 500
        assert err.details["required"] == 1000


class TestValidationError:
    def test_status_400(self):
        err = DomainValidationError("invalid input")
        assert str(err) == "invalid input"


class TestNotFoundError:
    def test_base_not_found_404(self):
        err = NotFoundError("missing")
        assert err.status_code.value == 404


class TestIdempotencyConflictError:
    def test_status_409(self):
        err = IdempotencyConflictError("key exists")
        assert err.status_code.value == 409


class TestProductInactiveError:
    def test_status_409(self):
        err = ProductInactiveError("product disabled")
        assert err.status_code.value == 409


class TestHoldExpiredError:
    def test_status_410(self):
        err = HoldExpiredError("hold expired")
        assert err.status_code.value == 410


class TestHoldAlreadyResolvedError:
    def test_status_409(self):
        err = HoldAlreadyResolvedError("already captured")
        assert err.status_code.value == 409


class TestNonZeroBalanceError:
    def test_status_409(self):
        err = NonZeroBalanceError("balance not zero")
        assert err.status_code.value == 409


class TestPendingHoldsError:
    def test_status_409(self):
        err = PendingHoldsError("holds exist")
        assert err.status_code.value == 409


class TestLoanNotActiveError:
    def test_status_409(self):
        err = LoanNotActiveError("loan closed")
        assert err.status_code.value == 409


class TestRepaymentExceedsOutstandingError:
    def test_status_400(self):
        err = RepaymentExceedsOutstandingError("too much")
        assert err.status_code.value == 400


class TestFXRateStaleError:
    def test_status_503(self):
        err = FXRateStaleError("rate expired")
        assert err.status_code.value == 503


class TestLiquidityPoolUnavailableError:
    def test_status_503(self):
        err = LiquidityPoolUnavailableError("pool empty")
        assert err.status_code.value == 503


class TestServiceUnavailableError:
    def test_status_503(self):
        err = ServiceUnavailableError("downstream down")
        assert err.status_code.value == 503


class TestTransferError:
    def test_transfer_error_attributes(self):
        err = TransferError("INSUFFICIENT_BALANCE", "transfer failed")
        assert err.code == "INSUFFICIENT_BALANCE"
        assert err.message == "transfer failed"

    def test_is_target_matching(self):
        """TransferError.is_target() should match sentinel errors."""
        err = TransferError("INSUFFICIENT_BALANCE", "fail")
        assert err.is_target(ErrInsufficientBalance) is True

    def test_is_target_no_match(self):
        err = TransferError("INSUFFICIENT_BALANCE", "fail")
        assert err.is_target(ErrNotFound) is False

    def test_transfer_error_with_details(self):
        err = TransferError(
            "FX_FAIL",
            "fx fail",
            tb_status="tb_conflict",
            details={"pair": "USD/EUR"},
        )
        assert err.tb_status == "tb_conflict"
        assert err.details == {"pair": "USD/EUR"}


class TestSentinelErrors:
    def test_err_not_found(self):
        """ErrNotFound mirrors errno.ENOENT (value 2)."""
        assert ErrNotFound == 2

    def test_err_insufficient_balance(self):
        assert "insufficient balance" in str(ErrInsufficientBalance)


class TestErrorInheritance:
    """Verify all specific errors inherit from DomainError."""

    @pytest.mark.parametrize(
        "err_class,args",
        [
            (AccountNotFoundError, ("acc-1",)),
            (AccountClosedError, ("acc-1",)),
            (AccountFrozenError, ("acc-1",)),
            (NotFoundError, ("missing",)),
            (IdempotencyConflictError, ("key exists",)),
            (ProductInactiveError, ("inactive",)),
            (HoldExpiredError, ("expired",)),
            (HoldAlreadyResolvedError, ("resolved",)),
            (NonZeroBalanceError, ("balance",)),
            (PendingHoldsError, ("holds",)),
            (LoanNotActiveError, ("closed",)),
            (RepaymentExceedsOutstandingError, ("excess",)),
            (FXRateStaleError, ("stale",)),
            (LiquidityPoolUnavailableError, ("unavailable",)),
            (ServiceUnavailableError, ("down",)),
        ],
    )
    def test_all_inherit_domain_error(self, err_class, args):
        err = err_class(*args)
        assert isinstance(err, DomainError)
