# Issue 07: PostgreSQL Store Repositories

**Phase:** 2 - Domain Model + CLI
**Priority:** High
**Labels:** `phase-2`, `store`
**Depends on:** #04 (PostgreSQL Pool), #05 (Domain Model)

## Summary

Implement all PostgreSQL repository modules for metadata queries and writes.
Each repo uses SQLAlchemy Core (not ORM) with async sessions.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/store/postgres/account_repo.py` | Account metadata (create, get, list, close) |
| `src/cbs/store/postgres/customer_repo.py` | Customer-account relationships |
| `src/cbs/store/postgres/product_repo.py` | Product catalogue queries |
| `src/cbs/store/postgres/fx_rate_repo.py` | FX rate queries |
| `src/cbs/store/postgres/idempotency_repo.py` | Idempotency key store (reserve, get, complete) |
| `src/cbs/store/postgres/transfer_repo.py` | Transfer metadata writes/reads |
| `src/cbs/store/postgres/system_account_repo.py` | System account registry |
| `src/cbs/store/postgres/loan_repo.py` | Loan details queries |
| `src/cbs/store/postgres/batch_repo.py` | Batch run tracking |
| `src/cbs/store/postgres/settlement_repo.py` | Settlement batch queries |
| `src/cbs/store/postgres/fee_repo.py` | Fee schedule queries |
| `src/cbs/store/postgres/audit_repo.py` | Audit log writes |

## Key Patterns

- All repos take a `Database` instance (or session) in their constructor
- Use SQLAlchemy Core `select()`, `insert()`, `update()` constructs
- No ORM models — tables defined via SQLAlchemy `Table` objects or raw SQL via `op.execute()`
- Async sessions from `Database.session()`
- Idempotency repo is critical: `reserve()` inserts with `pending` status, `complete()` updates on success

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/store/postgres/account_meta_repo.go` | Account metadata |
| `../corebanking/internal/store/postgres/customer_repo.go` | Customer relationships |
| `../corebanking/internal/store/postgres/product_repo.go` | Product catalogue |
| `../corebanking/internal/store/postgres/fx_rate_repo.go` | FX rates |
| `../corebanking/internal/store/postgres/idempotency_repo.go` | Idempotency keys |
| `../corebanking/internal/store/postgres/system_account.go` | System accounts |
| `../corebanking/internal/store/postgres/loan_repo.go` | Loan details |
| `../corebanking/internal/store/postgres/batch_run_repo.go` | Batch tracking |
| `../corebanking/internal/store/postgres/settlement_repo.go` | Settlements |
| `../corebanking/internal/store/postgres/audit_repo.go` | Audit log |
| `../corebanking/internal/store/postgres/fee_collection_repo.go` | Fee schedules |

## Acceptance Criteria

- [ ] All repos can execute CRUD operations against PostgreSQL
- [ ] Idempotency repo correctly handles reserve -> complete/failed lifecycle
- [ ] Queries use parameterised statements (no SQL injection)
- [ ] All repos work with async sessions
- [ ] Unit tests with mock database or integration tests with real PG
