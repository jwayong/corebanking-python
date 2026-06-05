# Issue 10: TigerBeetle Store Repositories

**Phase:** 3 - Core API
**Priority:** High
**Labels:** `phase-3`, `store`
**Depends on:** #03 (TB Client), #05 (Domain Model), #06 (Utilities)

## Summary

Implement TigerBeetle repository modules for account and transfer operations.
These repos translate between domain models and TigerBeetle's native types.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/store/tigerbeetle/account_repo.py` | Account CRUD against TB (create, lookup, get balances) |
| `src/cbs/store/tigerbeetle/transfer_repo.py` | Transfer operations against TB (create, lookup, get by account) |

## Key Patterns

- Account repo translates domain `AccountCode` to TB account flags
- Transfer repo handles TB transfer construction (id, debit/credit accounts, amount, ledger, code, flags)
- Batch operations: up to 8191 transfers per `create_transfers` call
- UUIDv7 bytes for all TB IDs (via `cbs.util.uuid`)
- Linked transfers use TB's `flags.linked` for atomicity
- Pending transfers use `flags.pending` for two-phase holds

### TB Account Flags Mapping

| Flag | Meaning |
|------|---------|
| `debits_must_not_exceed_credits` | Credit-balance accounts (liabilities, equity, income) |
| `credits_must_not_exceed_debits` | Debit-balance accounts (assets, expenses) |
| `history` | Enable balance history tracking |

### Transfer Construction

```python
{
    "id": uuidv7_bytes(),
    "debit_account_id": debit_account_tb_id,
    "credit_account_id": credit_account_tb_id,
    "amount": amount_int,           # Integer, not float
    "ledger": ledger_number,        # ISO 4217 numeric
    "code": transfer_code_int,      # TransferCode enum value
    "flags": flags_int,             # Bitfield (linked, pending, etc.)
    "user_data_128": correlation_bytes,
    "user_data_64": value_date_nanos,
}
```

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/store/tigerbeetle/account_repo.go` | TB account operations |
| `../corebanking/internal/store/tigerbeetle/transfer_repo.go` | TB transfer operations |
| `../corebanking/DESIGN.md` | TB data model |
| `../corebanking/DOMAIN-RETAIL.md` | Account codes and transfer flows |

## Acceptance Criteria

- [ ] Account repo creates TB accounts with correct flags
- [ ] Transfer repo creates single and batched transfers
- [ ] Linked transfers work atomically (both succeed or both fail)
- [ ] Pending transfers created for holds
- [ ] Lookup operations return correct data with UUID conversion
- [ ] Batch operations respect 8191 transfer limit per call
