"""Tests for amount/scale conversion helpers."""

import pytest

from cbs.util.amount import (
    format_amount,
    parse_amount,
    scale_down,
    scale_up,
)


class TestScaleUp:
    """Test scaling amounts from lower to higher precision."""

    def test_jpy_to_cents(self):
        """100 JPY (scale 0) → 10,000 at scale 2."""
        assert scale_up(100, 0, 2) == 10_000

    def test_usd_no_change(self):
        """Same scale returns unchanged."""
        assert scale_up(1234, 2, 2) == 1234

    def test_higher_to_lower_returns_same(self):
        """to_scale < from_scale returns amount unchanged."""
        assert scale_up(10_000, 2, 0) == 10_000

    def test_zero_amount(self):
        assert scale_up(0, 0, 2) == 0

    def test_negative_amount(self):
        assert scale_up(-100, 0, 2) == -10_000

    def test_large_diff(self):
        """Scale from 0 to 4 (e.g., for high-precision currencies)."""
        assert scale_up(1, 0, 4) == 10_000

    def test_single_digit_diff(self):
        assert scale_up(5, 1, 2) == 50

    def test_max_int64_safe(self):
        """Large values should not overflow (Python handles big ints)."""
        result = scale_up(9_223_372_036_854_775, 0, 2)
        assert result == 922_337_203_685_477_500


class TestScaleDown:
    """Test scaling amounts from higher to lower precision."""

    def test_cents_to_whole(self):
        """10,050 cents (scale 2) → 100 whole units (scale 0)."""
        assert scale_down(10_050, 2, 0) == 100

    def test_same_scale(self):
        assert scale_down(500, 2, 2) == 500

    def test_lower_to_higher_returns_same(self):
        """from_scale < to_scale returns amount unchanged."""
        assert scale_down(100, 0, 2) == 100

    def test_zero_amount(self):
        assert scale_down(0, 2, 0) == 0

    def test_truncation(self):
        """scale_down truncates (matches Go int64 division)."""
        assert scale_down(10_999, 2, 0) == 109

    def test_negative_truncation(self):
        """Negative values truncate toward zero (not floor)."""
        assert scale_down(-10_999, 2, 0) == -109

    def test_large_diff(self):
        assert scale_down(1_234_567, 4, 0) == 123

    def test_single_digit_diff(self):
        assert scale_down(95, 2, 1) == 9


class TestFormatAmount:
    """Test human-readable formatting."""

    def test_positive_two_decimal(self):
        assert format_amount(1234567, 2) == "12345.67"

    def test_negative_two_decimal(self):
        assert format_amount(-500, 2) == "-5.00"

    def test_zero_scale(self):
        assert format_amount(100, 0) == "100"

    def test_zero_scale_negative(self):
        assert format_amount(-50, 0) == "-50"

    def test_zero_value(self):
        assert format_amount(0, 2) == "0.00"

    def test_small_fraction(self):
        assert format_amount(5, 2) == "0.05"

    def test_large_value(self):
        assert format_amount(1_234_567_890, 2) == "12345678.90"

    def test_jpy_zero_scale(self):
        assert format_amount(1500, 0) == "1500"

    def test_four_decimal_scale(self):
        assert format_amount(12345, 4) == "1.2345"


class TestParseAmount:
    """Test parsing human-readable strings back to integers."""

    def test_basic_two_decimal(self):
        assert parse_amount("12345.67", 2) == 1_234_567

    def test_negative(self):
        assert parse_amount("-5.00", 2) == -500

    def test_no_decimal(self):
        assert parse_amount("100", 2) == 10_000

    def test_zero(self):
        assert parse_amount("0", 2) == 0

    def test_zero_with_decimals(self):
        assert parse_amount("0.00", 2) == 0

    def test_single_digit_fraction(self):
        """Missing trailing zero should be padded."""
        assert parse_amount("10.5", 2) == 1050

    def test_zero_scale(self):
        assert parse_amount("100", 0) == 100

    def test_negative_zero_scale(self):
        assert parse_amount("-50", 0) == -50

    def test_too_many_digits_raises(self):
        with pytest.raises(ValueError, match="too many fractional digits"):
            parse_amount("1.234", 2)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_amount("", 2)

    def test_four_decimal_scale(self):
        assert parse_amount("1.2345", 4) == 12_345

    def test_round_trip_positive(self):
        """format_amount → parse_amount should be identity."""
        original = 1234567
        formatted = format_amount(original, 2)
        assert parse_amount(formatted, 2) == original

    def test_round_trip_negative(self):
        original = -500
        formatted = format_amount(original, 2)
        assert parse_amount(formatted, 2) == original

    def test_round_trip_zero(self):
        formatted = format_amount(0, 2)
        assert parse_amount(formatted, 2) == 0


class TestScaleRoundTrip:
    """Verify scale_up and scale_down are inverse operations."""

    @pytest.mark.parametrize(
        "amount,from_scale,to_scale",
        [
            (10_000, 2, 0),
            (100, 0, 2),
            (-5_000, 2, 0),
            (1_234_567, 4, 2),
            (99, 0, 3),
        ],
    )
    def test_up_then_down(self, amount, from_scale, to_scale):
        scaled = scale_up(amount, from_scale, to_scale)
        back = scale_down(scaled, to_scale, from_scale)
        assert back == amount

    @pytest.mark.parametrize(
        "amount,from_scale,to_scale",
        [
            (10_000, 2, 0),
            (100, 0, 2),
            (-5_000, 2, 0),
        ],
    )
    def test_down_then_up(self, amount, from_scale, to_scale):
        scaled = scale_down(amount, from_scale, to_scale)
        back = scale_up(scaled, to_scale, from_scale)
        # Note: may lose precision due to truncation in scale_down
        assert back == scaled * (10 ** max(0, from_scale - to_scale))
