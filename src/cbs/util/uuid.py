"""UUIDv7 helpers — generation, byte conversion, and TigerBeetle ID adapters.

Mirrors the Go `tigerbeetleutil` package for UUID operations. Uses Python 3.14+
stdlib `uuid.uuid7()` (RFC 9562) instead of the Go `google/uuid` library.

TigerBeetle stores IDs as 16-byte little-endian Uint128. UUIDs are big-endian,
so each 8-byte half must be reversed when converting between the two formats.
"""

from __future__ import annotations

import uuid as _uuid


def generate_uuidv7() -> _uuid.UUID:
    """Generate a new UUIDv7 (RFC 9562).

    Returns:
        A time-ordered UUIDv7 instance.
    """
    return _uuid.uuid7()


def uuidv7_bytes() -> bytes:
    """Generate a new UUIDv7 and return the raw 16-byte representation.

    Returns:
        16 bytes in big-endian order (standard UUID byte layout).
    """
    return _uuid.uuid7().bytes


def uuidv7_str() -> str:
    """Generate a new UUIDv7 and return the standard 36-character string form.

    Returns:
        String like "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e".
    """
    return str(_uuid.uuid7())


def uuidv7_to_tb_id(u: _uuid.UUID) -> bytes:
    """Convert a UUID to 16 bytes for TigerBeetle storage.

    This is a simple big-endian byte dump — suitable when the consumer
    expects standard UUID byte order. For TigerBeetle's little-endian
    Uint128 format, use :func:`uuid_to_uint128` instead.

    Args:
        u: The UUID to convert.

    Returns:
        16 bytes in big-endian order.
    """
    return u.bytes


def tb_id_to_uuid(raw: bytes) -> _uuid.UUID:
    """Convert 16 bytes from TigerBeetle back to a UUID.

    Args:
        raw: 16 bytes in big-endian order (standard UUID layout).

    Returns:
        The reconstructed UUID.

    Raises:
        ValueError: If *raw* is not exactly 16 bytes.
    """
    if len(raw) != 16:
        raise ValueError(f"expected 16 bytes, got {len(raw)}")
    return _uuid.UUID(bytes=raw)


def uuid_to_uint128(u: _uuid.UUID) -> bytes:
    """Convert a UUID to TigerBeetle's Uint128 byte representation.

    TigerBeetle uses little-endian byte order; UUIDs are big-endian,
    so each 8-byte half is reversed in the conversion.

    Args:
        u: The UUID to convert.

    Returns:
        16 bytes in little-endian order (Uint128 format).
    """
    b = u.bytes  # big-endian: [0..7] = time_high, [8..15] = clock_seq + node
    result = bytearray(16)
    for i in range(8):
        result[i] = b[7 - i]
    for i in range(8):
        result[8 + i] = b[15 - i]
    return bytes(result)


def uint128_to_uuid(raw: bytes) -> _uuid.UUID:
    """Convert TigerBeetle Uint128 bytes back to a UUID.

    Inverse of :func:`uuid_to_uint128`. Reverses each 8-byte half
    to restore big-endian UUID byte order.

    Args:
        raw: 16 bytes in little-endian order (Uint128 format).

    Returns:
        The reconstructed UUID.

    Raises:
        ValueError: If *raw* is not exactly 16 bytes.
    """
    if len(raw) != 16:
        raise ValueError(f"expected 16 bytes, got {len(raw)}")
    result = bytearray(16)
    for i in range(8):
        result[i] = raw[7 - i]
    for i in range(8):
        result[8 + i] = raw[15 - i]
    return _uuid.UUID(bytes=bytes(result))


def uint64_to_tb_bytes(v: int) -> bytes:
    """Convert a non-negative integer to TigerBeetle's 16-byte little-endian format.

    Mirrors Go's ``Uint64ToBytes`` — encodes the value in the first 8 bytes
    (little-endian), with the upper 8 bytes zeroed.

    Args:
        v: A non-negative integer (must fit in uint64).

    Returns:
        16 bytes with the value encoded in little-endian order.

    Raises:
        ValueError: If *v* is negative or exceeds uint64 range.
    """
    if v < 0:
        raise ValueError(f"uint64 value must be non-negative, got {v}")
    if v > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"uint64 value too large, got {v}")
    return v.to_bytes(16, byteorder="little")


def parse_uuid(s: str) -> _uuid.UUID:
    """Parse a UUID string and return the UUID object.

    Args:
        s: A UUID string (e.g., "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e").

    Returns:
        The parsed UUID.

    Raises:
        ValueError: If the string is not a valid UUID format.
    """
    return _uuid.UUID(s)
