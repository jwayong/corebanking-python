"""Utility modules — UUIDv7, amount conversion, and TigerBeetle type adapters."""

from cbs.util.amount import (
    format_amount,
    parse_amount,
    scale_down,
    scale_up,
)
from cbs.util.tb_types import (
    int_to_uint128,
    uint128_to_int,
    uint128_to_uint64,
    uint64_to_uint128,
)
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

__all__ = [
    # UUIDv7
    "generate_uuidv7",
    "parse_uuid",
    "tb_id_to_uuid",
    "uint128_to_uuid",
    "uint64_to_tb_bytes",
    "uuid_to_uint128",
    "uuidv7_bytes",
    "uuidv7_str",
    "uuidv7_to_tb_id",
    # Amount
    "format_amount",
    "parse_amount",
    "scale_down",
    "scale_up",
    # TB types
    "int_to_uint128",
    "uint128_to_int",
    "uint128_to_uint64",
    "uint64_to_uint128",
]
