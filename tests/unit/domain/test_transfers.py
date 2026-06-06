"""Tests for transfer codes, request validation, and mapping functions."""

import pytest

from cbs.domain.errors import ValidationError
from cbs.domain.transfers import (
    CaptureRequest,
    DEFAULT_HOLD_TIMEOUT_SECONDS,
    FeeChargeRequest,
    FXRequest,
    HoldRequest,
    MAX_HOLD_TIMEOUT_SECONDS,
    TransferCode,
    map_transfer_code,
    transfer_code_to_string,
)


class TestTransferCodeEnum:
    """Verify all 20 transfer codes are defined."""

    def test_twenty_codes(self):
        assert len(TransferCode) == 20

    def test_deposit_is_one(self):
        assert TransferCode.DEPOSIT.value == 1

    def test_interest_capitalisation_is_twenty(self):
        assert TransferCode.INTEREST_CAPITALISATION.value == 20

    @pytest.mark.parametrize(
        "member_name,value",
        [
            ("DEPOSIT", 1),
            ("WITHDRAWAL", 2),
            ("TRANSFER", 3),
            ("FX_DEBIT", 4),
            ("FX_CREDIT", 5),
            ("PAYMENT_OUT", 6),
            ("PAYMENT_IN", 7),
            ("HOLD", 8),
            ("CAPTURE", 9),
            ("VOID", 10),
            ("FEE", 11),
            ("INTEREST_CREDIT", 12),
            ("INTEREST_DEBIT", 13),
            ("CORRECTION", 14),
            ("SETTLEMENT", 15),
            ("LOAN_DISBURSEMENT", 16),
            ("LOAN_REPAYMENT", 17),
            ("WRITE_OFF", 18),
            ("PENALTY", 19),
            ("INTEREST_CAPITALISATION", 20),
        ],
    )
    def test_all_code_values(self, member_name, value):
        assert getattr(TransferCode, member_name).value == value


class TestMapTransferCode:
    def test_map_deposit(self):
        assert map_transfer_code("deposit") == TransferCode.DEPOSIT

    def test_map_withdrawal(self):
        assert map_transfer_code("withdrawal") == TransferCode.WITHDRAWAL

    def test_map_transfer(self):
        assert map_transfer_code("transfer") == TransferCode.TRANSFER

    def test_map_unknown_raises(self):
        with pytest.raises(ValidationError, match="unsupported transfer_type"):
            map_transfer_code("unknown")

    def test_map_empty_raises(self):
        with pytest.raises(ValidationError):
            map_transfer_code("")


class TestTransferCodeToString:
    def test_deposit_string(self):
        assert transfer_code_to_string(TransferCode.DEPOSIT) == "deposit"

    def test_withdrawal_string(self):
        assert transfer_code_to_string(TransferCode.WITHDRAWAL) == "withdrawal"

    def test_hold_string(self):
        assert transfer_code_to_string(TransferCode.HOLD) == "hold"

    def test_unknown_code(self):
        """Unknown code returns 'unknown_N'."""
        assert transfer_code_to_string(99) == "unknown_99"

    def test_all_codes_map(self):
        """Verify all 20 codes have string mappings."""
        for code in TransferCode:
            result = transfer_code_to_string(code)
            assert "unknown" not in result


class TestHoldTimeoutConstants:
    def test_default_hold_timeout(self):
        """Default hold timeout is 24 hours."""
        assert DEFAULT_HOLD_TIMEOUT_SECONDS == 86_400

    def test_max_hold_timeout(self):
        """Max hold timeout is 7 days."""
        assert MAX_HOLD_TIMEOUT_SECONDS == 604_800


class TestHoldRequest:
    def test_valid_hold_request(self):
        req = HoldRequest(
            debit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=10000,
            currency="USD",
        )
        req.validate()  # should not raise
        assert req.timeout_seconds == DEFAULT_HOLD_TIMEOUT_SECONDS

    def test_hold_request_custom_timeout(self):
        req = HoldRequest(
            debit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e",
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=5000,
            currency="USD",
            timeout_seconds=3600,
        )
        assert req.timeout_seconds == 3600

    def test_hold_missing_debit_raises(self):
        req = HoldRequest(
            debit_account_id="",
            credit_account_id="0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f",
            amount=1000,
            currency="USD",
        )
        with pytest.raises(ValidationError, match="debit_account_id"):
            req.validate()

    def test_hold_same_accounts_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        req = HoldRequest(
            debit_account_id=uid,
            credit_account_id=uid,
            amount=1000,
            currency="USD",
        )
        with pytest.raises(ValidationError, match="same"):
            req.validate()

    def test_hold_zero_amount_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        uid2 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
        req = HoldRequest(
            debit_account_id=uid,
            credit_account_id=uid2,
            amount=0,
            currency="USD",
        )
        with pytest.raises(ValidationError, match="amount"):
            req.validate()

    def test_hold_excessive_timeout_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        uid2 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
        req = HoldRequest(
            debit_account_id=uid,
            credit_account_id=uid2,
            amount=1000,
            currency="USD",
            timeout_seconds=700000,  # exceeds max
        )
        with pytest.raises(ValidationError, match="timeout"):
            req.validate()

    def test_hold_bad_currency_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        uid2 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
        req = HoldRequest(
            debit_account_id=uid,
            credit_account_id=uid2,
            amount=1000,
            currency="XYZ",
        )
        with pytest.raises(ValidationError, match="currency"):
            req.validate()


class TestCaptureRequest:
    def test_valid_capture_request(self):
        req = CaptureRequest(amount=8000)
        req.validate()  # should not raise

    def test_capture_zero_amount(self):
        """Zero amount means full capture."""
        req = CaptureRequest()
        assert req.amount == 0
        req.validate()

    def test_capture_negative_raises(self):
        req = CaptureRequest(amount=-100)
        with pytest.raises(ValidationError, match="amount"):
            req.validate()


class TestFXRequest:
    def test_valid_fx_request(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        uid2 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
        req = FXRequest(
            debit_account_id=uid,
            credit_account_id=uid2,
            sell_amount=10000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )
        req.validate()  # should not raise

    def test_fx_same_currency_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        uid2 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
        req = FXRequest(
            debit_account_id=uid,
            credit_account_id=uid2,
            sell_amount=10000,
            sell_currency="USD",
            buy_currency="USD",
            rate=1.0,
        )
        with pytest.raises(ValidationError, match="differ"):
            req.validate()

    def test_fx_negative_rate_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        uid2 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
        req = FXRequest(
            debit_account_id=uid,
            credit_account_id=uid2,
            sell_amount=10000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=-0.5,
        )
        with pytest.raises(ValidationError, match="rate"):
            req.validate()

    def test_fx_bad_sell_currency_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        uid2 = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"
        req = FXRequest(
            debit_account_id=uid,
            credit_account_id=uid2,
            sell_amount=10000,
            sell_currency="XYZ",
            buy_currency="EUR",
            rate=0.85,
        )
        with pytest.raises(ValidationError):
            req.validate()


class TestFeeChargeRequest:
    def test_valid_fee_request(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        req = FeeChargeRequest(
            customer_account_id=uid,
            amount=500,
            currency="USD",
            description="Monthly fee",
        )
        req.validate()  # should not raise

    def test_fee_zero_amount_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        req = FeeChargeRequest(
            customer_account_id=uid,
            amount=0,
            currency="USD",
            description="Monthly fee",
        )
        with pytest.raises(ValidationError, match="amount"):
            req.validate()

    def test_fee_missing_description_raises(self):
        uid = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
        req = FeeChargeRequest(
            customer_account_id=uid,
            amount=500,
            currency="USD",
            description="",
        )
        with pytest.raises(ValidationError, match="description"):
            req.validate()
