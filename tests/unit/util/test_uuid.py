"""Tests for UUIDv7 generation, byte conversion, and TigerBeetle ID adapters."""

import uuid as _uuid
from uuid import UUID

import pytest

from cbs.util.uuid import (
    generate_uuidv7,
    parse_uuid,
    tb_id_to_uuid,
    uint128_to_uuid,
    uint64_to_tb_bytes,
    uuid_to_uint128,
    uuidv7_bytes,
    uuidv7_str,
    uuidv7_to_tb_id,
)


class TestGenerateUUIDv7:
    """Verify UUIDv7 generation returns valid instances."""

    def test_returns_uuid_instance(self):
        result = generate_uuidv7()
        assert isinstance(result, UUID)

    def test_version_is_seven(self):
        result = generate_uuidv7()
        assert result.version == 7

    def test_unique_calls(self):
        """Two consecutive calls should produce different UUIDs."""
        u1 = generate_uuidv7()
        u2 = generate_uuidv7()
        assert u1 != u2

    def test_valid_nibbles(self):
        """UUID string should only contain hex chars and dashes."""
        s = str(generate_uuidv7())
        assert len(s) == 36
        # Check dash positions: 8, 13, 18, 23
        assert s[8] == "-"
        assert s[13] == "-"
        assert s[18] == "-"
        assert s[23] == "-"

    def test_time_ordered(self):
        """UUIDv7 should be time-ordered: later UUID > earlier UUID."""
        u1 = generate_uuidv7()
        import time

        time.sleep(0.01)
        u2 = generate_uuidv7()
        assert u1 < u2


class TestUUIDv7Bytes:
    def test_returns_16_bytes(self):
        result = uuidv7_bytes()
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_unique_bytes(self):
        b1 = uuidv7_bytes()
        b2 = uuidv7_bytes()
        assert b1 != b2


class TestUUIDv7Str:
    def test_returns_string(self):
        result = uuidv7_str()
        assert isinstance(result, str)
        assert len(result) == 36

    def test_valid_format(self):
        result = uuidv7_str()
        # Should be parseable back to UUID
        parsed = _uuid.UUID(result)
        assert str(parsed) == result


class TestUUIDv7ToTBId:
    def test_returns_16_bytes(self):
        u = generate_uuidv7()
        result = uuidv7_to_tb_id(u)
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_deterministic(self):
        """Same UUID should always produce the same bytes."""
        u = _uuid.UUID("0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")
        b1 = uuidv7_to_tb_id(u)
        b2 = uuidv7_to_tb_id(u)
        assert b1 == b2

    def test_matches_uuid_bytes(self):
        """uuidv7_to_tb_id should return u.bytes (big-endian)."""
        u = _uuid.UUID("0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")
        assert uuidv7_to_tb_id(u) == u.bytes


class TestTBIdToUUID:
    def test_round_trip(self):
        """tb_id_to_uuid(uuidv7_bytes()) should reconstruct the UUID."""
        original = generate_uuidv7()
        raw = uuidv7_to_tb_id(original)
        restored = tb_id_to_uuid(raw)
        assert restored == original

    def test_known_value(self):
        u = _uuid.UUID("0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")
        restored = tb_id_to_uuid(u.bytes)
        assert restored == u

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="expected 16 bytes"):
            tb_id_to_uuid(b"\x00" * 8)

    def test_empty_bytes_raises(self):
        with pytest.raises(ValueError):
            tb_id_to_uuid(b"")


class TestUUIDToUint128:
    """Test UUID → Uint128 conversion with byte-order reversal."""

    def test_returns_16_bytes(self):
        u = generate_uuidv7()
        result = uuid_to_uint128(u)
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_byte_order_reversal(self):
        """Each 8-byte half should be reversed (big-endian → little-endian)."""
        # Use a UUID with known byte pattern: 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10
        u = _uuid.UUID(bytes=bytes(range(1, 17)))
        result = uuid_to_uint128(u)
        # First half reversed: 08 07 06 05 04 03 02 01
        # Second half reversed: 10 0F 0E 0D 0C 0B 0A 09
        assert result == bytes([8, 7, 6, 5, 4, 3, 2, 1, 0x10, 0x0F, 0x0E, 0x0D, 0x0C, 0x0B, 0x0A, 9])

    def test_round_trip(self):
        """uint128_to_uuid(uuid_to_uint128(u)) should return the original UUID."""
        u = generate_uuidv7()
        raw = uuid_to_uint128(u)
        restored = uint128_to_uuid(raw)
        assert restored == u

    def test_multiple_round_trips(self):
        """Verify round-trip works for multiple distinct UUIDs."""
        uuids = [generate_uuidv7() for _ in range(10)]
        for u in uuids:
            restored = uint128_to_uuid(uuid_to_uint128(u))
            assert restored == u

    def test_different_from_raw_bytes(self):
        """Uint128 bytes should differ from raw UUID bytes (due to reversal)."""
        u = _uuid.UUID("0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")
        uint128 = uuid_to_uint128(u)
        raw = u.bytes
        assert uint128 != raw


class TestUint128ToUUID:
    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="expected 16 bytes"):
            uint128_to_uuid(b"\x00" * 8)

    def test_empty_bytes_raises(self):
        with pytest.raises(ValueError):
            uint128_to_uuid(b"")

    def test_known_value_round_trip(self):
        """Test with a specific UUID value."""
        u = _uuid.UUID("0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")
        raw = uuid_to_uint128(u)
        restored = uint128_to_uuid(raw)
        assert restored == u


class TestUint64ToTBBytes:
    def test_returns_16_bytes(self):
        result = uint64_to_tb_bytes(1000)
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_zero(self):
        result = uint64_to_tb_bytes(0)
        assert result == b"\x00" * 16

    def test_little_endian_encoding(self):
        """Value should be encoded in little-endian order."""
        # 0x0102 = 258 → bytes: 02 01
        result = uint64_to_tb_bytes(0x0102)
        assert result[0] == 0x02
        assert result[1] == 0x01

    def test_max_uint64(self):
        """Max uint64 fills lower 8 bytes, upper 8 are zero."""
        result = uint64_to_tb_bytes(0xFFFFFFFFFFFFFFFF)
        assert result[0:8] == b"\xff" * 8
        assert result[8:16] == b"\x00" * 8

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            uint64_to_tb_bytes(-1)

    def test_exceeds_uint64_raises(self):
        with pytest.raises(ValueError, match="too large"):
            uint64_to_tb_bytes(0x10000000000000000)

    def test_typical_amount(self):
        """10000 cents (=$100.00) encodes correctly."""
        result = uint64_to_tb_bytes(10000)
        # 10000 = 0x2710 → LE bytes: 10 27
        assert result[0] == 0x10
        assert result[1] == 0x27


class TestParseUUID:
    def test_valid_uuid_string(self):
        result = parse_uuid("0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e")
        assert isinstance(result, UUID)
        assert str(result) == "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"

    def test_uppercase(self):
        result = parse_uuid("0194E7C3-8F4A-7B2D-9C1E-4F5A6B7C8D9E")
        assert str(result) == "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"

    def test_nil_uuid(self):
        result = parse_uuid("00000000-0000-0000-0000-000000000000")
        assert result.bytes == b"\x00" * 16

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_uuid("not-a-uuid")

    def test_wrong_version(self):
        """A valid UUID v4 string should still parse (we don't enforce v7)."""
        result = parse_uuid("550e8400-e29b-41d4-a716-446655440000")
        assert result.version == 4
