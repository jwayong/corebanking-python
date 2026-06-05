# Issue 06: Utility Modules (UUIDv7, Amount, TB Types)

**Phase:** 2 - Domain Model + CLI
**Priority:** High
**Labels:** `phase-2`, `utility`
**Depends on:** #01 (Project Bootstrap)

## Summary

Create utility modules for UUIDv7 generation/conversion, amount/scale
helpers, and TigerBeetle Uint128 type adapters.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/util/__init__.py` | Utility package |
| `src/cbs/util/uuid.py` | UUIDv7 helpers using stdlib `uuid.uuid7()` (Python 3.14+) |
| `src/cbs/util/amount.py` | Amount/scale conversion helpers |
| `src/cbs/util/tb_types.py` | TigerBeetle type adapters (Uint128 <-> bytes) |

## Detailed Spec

### uuid.py

```python
from __future__ import annotations
from uuid import UUID, uuid7

def generate_uuidv7() -> UUID:
    return uuid7()

def uuidv7_bytes() -> bytes:
    return uuid7().bytes

def uuidv7_to_tb_id(u: UUID) -> bytes:
    return u.bytes

def tb_id_to_uuid(raw: bytes) -> UUID:
    return UUID(bytes=raw)

def uuidv7_str() -> str:
    return str(uuid7())
```

### amount.py

Helpers for converting between human-readable amounts and TigerBeetle's
integer representation (amount * 10^scale).

### tb_types.py

Adapters for TigerBeetle's Uint128 type. Python's arbitrary-precision `int`
handles 128-bit values natively, but conversion to/from 16-byte representation
needs care (big-endian byte order).

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/pkg/tigerbeetleutil/id.go` | ID generation |
| `../corebanking/pkg/tigerbeetleutil/amount.go` | Amount conversion |
| `../corebanking/pkg/tigerbeetleutil/uuid.go` | UUID/TB type conversion |
| `../corebanking/pkg/types/money.go` | Money type |

## Acceptance Criteria

- [ ] `generate_uuidv7()` returns valid UUIDv7 instances
- [ ] `uuidv7_bytes()` returns exactly 16 bytes
- [ ] `tb_id_to_uuid(uuidv7_bytes())` round-trips correctly
- [ ] Amount conversions handle edge cases (zero, max values)
- [ ] Uint128 byte conversion handles full 128-bit range
- [ ] Unit tests for all utility functions
