"""TigerBeetle type adapters — Uint128 conversion and account flag helpers.

Provides utilities for converting between Python types and TigerBeetle's
binary wire format. TigerBeetle uses little-endian byte order for all
numeric fields.
"""

from __future__ import annotations


def int_to_uint128(v: int) -> bytes:
    """Convert a non-negative integer to TigerBeetle's 16-byte Uint128 format.

    Encodes the value as a little-endian 16-byte sequence. Python's
    arbitrary-precision ``int`` handles values up to 2^128 naturally.

    Args:
        v: A non-negative integer (must fit in unsigned 128-bit range).

    Returns:
        16 bytes in little-endian order.

    Raises:
        ValueError: If *v* is negative or exceeds 2^128 - 1.
    """
    if v < 0:
        raise ValueError(f"Uint128 value must be non-negative, got {v}")
    if v > 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:  # 2^128 - 1
        raise ValueError(f"Uint128 value too large (exceeds 2^128-1), got {v}")
    return v.to_bytes(16, byteorder="little")


def uint128_to_int(raw: bytes) -> int:
    """Convert TigerBeetle Uint128 bytes to a Python integer.

    Inverse of :func:`int_to_uint128`. Reads the 16-byte little-endian
    value as an unsigned integer.

    Args:
        raw: 16 bytes in little-endian order.

    Returns:
        The unsigned integer value.

    Raises:
        ValueError: If *raw* is not exactly 16 bytes.
    """
    if len(raw) != 16:
        raise ValueError(f"expected 16 bytes, got {len(raw)}")
    return int.from_bytes(raw, byteorder="little")


def uint64_to_uint128(v: int) -> bytes:
    """Convert a uint64 value to TigerBeetle's 16-byte Uint128 format.

    Mirrors Go's ``Uint64ToBytes`` — encodes the value in little-endian
    byte order with the upper 8 bytes zeroed.

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


def uint128_to_uint64(raw: bytes) -> int:
    """Extract a uint64 value from TigerBeetle Uint128 bytes.

    Reads the lower 8 bytes as a little-endian uint64. The upper 8
    bytes are discarded.

    Args:
        raw: 16 bytes in little-endian order (Uint128 format).

    Returns:
        The uint64 value from the lower 8 bytes.

    Raises:
        ValueError: If *raw* is not exactly 16 bytes or the upper 8
            bytes are non-zero (value exceeds uint64 range).
    """
    if len(raw) != 16:
        raise ValueError(f"expected 16 bytes, got {len(raw)}")
    upper = int.from_bytes(raw[8:16], byteorder="little")
    if upper != 0:
        raise ValueError("Uint128 value exceeds uint64 range (upper bytes non-zero)")
    return int.from_bytes(raw[0:8], byteorder="little")
