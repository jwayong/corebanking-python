# Issue 05: Domain Constants, Types, and Error Classes

**Phase:** 2 - Domain Model + CLI
**Priority:** High
**Labels:** `phase-2`, `domain`
**Depends on:** #01 (Project Bootstrap)

## Summary

Create the complete domain model layer: account codes, transfer codes,
currency/ledger mappings, product model, loan model, settlement model,
and all domain exceptions.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/domain/__init__.py` | Domain package |
| `src/cbs/domain/accounts.py` | `AccountCode` enum, `is_debit_balance()`, `compute_balance()`, request/response models |
| `src/cbs/domain/transfers.py` | `TransferCode` enum, transfer request/response models, FX types, hold types |
| `src/cbs/domain/currency.py` | `CurrencyInfo` dataclass, `CURRENCIES` dict, `lookup_currency()`, `ledger_to_currency()` |
| `src/cbs/domain/products.py` | Product domain model |
| `src/cbs/domain/loans.py` | Loan lifecycle model, request/response types |
| `src/cbs/domain/settlements.py` | Settlement domain model |
| `src/cbs/domain/errors.py` | Domain exceptions: `InsufficientBalanceError`, `AccountNotFoundError`, `AccountClosedError`, `ValidationError`, `IdempotencyConflictError`, etc. |

## Key Patterns

- Account codes follow the chart of accounts (1000-1999 assets, 2000-2999 liabilities, etc.)
- Balance computation differs for debit-balance vs credit-balance accounts
- Transfer codes 1-20 map to specific financial operations
- All domain exceptions should include enough context for the error envelope
- Use `IntEnum` for codes, `dataclass(frozen=True)` for immutable value objects
- Use `msgspec.Struct` for request/response models (Litestar native)

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/domain/account.go` | Account model, `ComputeBalance` |
| `../corebanking/internal/domain/transfer.go` | Transfer types, codes, validation |
| `../corebanking/internal/domain/currency.go` | Currency info, ledger mapping |
| `../corebanking/internal/domain/product.go` | Product model |
| `../corebanking/internal/domain/settlement.go` | Settlement model |
| `../corebanking/internal/domain/errors.go` | Domain errors |
| `../corebanking/DOMAIN-RETAIL.md` | Chart of accounts, transaction flows |

## Acceptance Criteria

- [ ] All account codes from the Go version are present
- [ ] `compute_balance()` returns correct results for both debit and credit accounts
- [ ] All 20 transfer codes are defined
- [ ] 10 currencies are in the CURRENCIES dict
- [ ] Domain exceptions can be caught and mapped to HTTP status codes
- [ ] Unit tests for `compute_balance()`, `is_debit_balance()`, `lookup_currency()`
