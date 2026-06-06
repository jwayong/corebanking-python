"""Tests for TigerBeetle Uint128 type adapters."""

import pytest

from cbs.util.tb_types import (
    int_to_uint128,
    uint128_to_int,
    uint128_to_uint64,
    uint64_to_uint128,
)


class TestIntToUint128:
    """Test converting Python int to 16-byte Uint128."""

    def test_returns_16_bytes(self):
        result = int_to_uint128(1000)
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_zero(self):
        result = int_to_uint128(0)
        assert result == b"\x00" * 16

    def test_little_endian(self):
        """Value should be encoded in little-endian byte order."""
        result = int_to_uint128(0x0102)
        assert result[0] == 0x02
        assert result[1] == 0x01

    def test_max_uint64(self):
        """Values fitting in uint64 should have upper 8 bytes zeroed."""
        result = int_to_uint128(0xFFFFFFFFFFFFFFFF)
        assert result[0:8] == b"\xff" * 8
        assert result[8:16] == b"\x00" * 8

    def test_full_uint128_range(self):
        """Max uint128 value (2^128 - 1) should fill all bytes."""
        max_val = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
        result = int_to_uint128(max_val)
        assert result == b"\xff" * 16

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            int_to_uint128(-1)

    def test_exceeds_128_bit_raises(self):
        with pytest.raises(ValueError, match="too large"):
            int_to_uint128(0x100000000000000000000000000000000)

    def test_typical_amount(self):
        """10,000 cents encodes correctly."""
        result = int_to_uint128(10_000)
        assert result[0] == 0x10  # 16
        assert result[1] == 0x27  # 39


class TestUint128ToInt:
    """Test converting 16-byte Uint128 to Python int."""

    def test_zero(self):
        assert uint128_to_int(b"\x00" * 16) == 0

    def test_small_value(self):
        """Little-endian: 10 27 = 0x2710 = 10,000."""
        raw = bytes([0x10, 0x27]) + b"\x00" * 14
        assert uint128_to_int(raw) == 10_000

    def test_max_uint64(self):
        raw = b"\xff" * 8 + b"\x00" * 8
        assert uint128_to_int(raw) == 0xFFFFFFFFFFFFFFFF

    def test_full_uint128_max(self):
        raw = b"\xff" * 16
        assert uint128_to_int(raw) == 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="expected 16 bytes"):
            uint128_to_int(b"\x00" * 8)

    def test_empty_bytes_raises(self):
        with pytest.raises(ValueError):
            uint128_to_int(b"")

    def test_round_trip(self):
        """uint128_to_int(int_to_uint128(v)) should return v."""
        for value in [0, 1, 1000, 0xFFFFFFFFFFFFFFFF, 0x10000000000000001]:
            raw = int_to_uint128(value)
            assert uint128_to_int(raw) == value


class TestUint64ToUint128:
    """Test uint64 → Uint128 conversion (mirrors Go Uint64ToBytes)."""

    def test_returns_16_bytes(self):
        result = uint64_to_uint128(1000)
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_zero(self):
        result = uint64_to_uint128(0)
        assert result == b"\x00" * 16

    def test_little_endian(self):
        result = uint64_to_uint128(0x0102)
        assert result[0] == 0x02
        assert result[1] == 0x01

    def test_upper_bytes_zeroed(self):
        """Upper 8 bytes should always be zero."""
        result = uint64_to_uint128(0xFFFFFFFFFFFFFFFF)
        assert result[8:16] == b"\x00" * 8

    def test_max_uint64(self):
        result = uint64_to_uint128(0xFFFFFFFFFFFFFFFF)
        assert result[0:8] == b"\xff" * 8

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            uint64_to_uint128(-1)

    def test_exceeds_uint64_raises(self):
        with pytest.raises(ValueError, match="too large"):
            uint64_to_uint128(0x10000000000000000)

    def test_matches_go_implementation(self):
        """Verify byte-by-byte against Go's Uint64ToBytes logic.

        Go sets each byte individually:
            b[0] = byte(v)       → lowest byte first (little-endian)
            b[1] = byte(v >> 8)
            ...
        """
        # Test with value where each byte is distinct
        result = uint64_to_uint128(0x0706050403020100)
        expected = bytes([0, 1, 2, 3, 4, 5, 6, 7]) + b"\x00" * 8
        assert result == expected


class TestUint128ToUint64:
    """Test extracting uint64 from Uint128 bytes."""

    def test_zero(self):
        assert uint128_to_uint64(b"\x00" * 16) == 0

    def test_small_value(self):
        raw = bytes([0x10, 0x27]) + b"\x00" * 14
        assert uint128_to_uint64(raw) == 10_000

    def test_max_uint64(self):
        raw = b"\xff" * 8 + b"\x00" * 8
        assert uint128_to_uint64(raw) == 0xFFFFFFFFFFFFFFFF

    def test_upper_nonzero_raises(self):
        """If upper bytes are non-zero, value exceeds uint64."""
        raw = b"\x00" * 8 + bytes([1]) + b"\x00" * 7
        with pytest.raises(ValueError, match="exceeds uint64"):
            uint128_to_uint64(raw)

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="expected 16 bytes"):
            uint128_to_uint64(b"\x00" * 8)

    def test_round_trip_with_uint64_to(self):
        """uint128_to_uint64(uint64_to_uint128(v)) should return v."""
        for value in [0, 1, 1000, 10_000, 0xFFFFFFFFFFFFFFFF]:
            raw = uint64_to_uint128(value)
            assert uint128_to_uint64(raw) == value


class TestUint64AndIntConsistency:
    """Verify uint64_to_uint128 and int_to_uint128 produce identical output."""

    @pytest.mark.parametrize(
        "value",
        [0, 1, 1000, 10_000, 0xFFFFFFFFFFFFFFFF],
    )
    def test_same_output_for_uint64_range(self, value):
        """For values fitting in uint64, both functions should produce the same bytes."""
        result1 = uint64_to_uint128(value)
        result2 = int_to_uint128(value)
        assert result1 == result2
