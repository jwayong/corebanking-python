# Issue 02: Alembic Migrations

**Phase:** 1 - Foundation
**Priority:** High
**Labels:** `phase-1`, `foundation`
**Depends on:** #01 (Project Bootstrap)

## Summary

Port all 10 Go SQL migration files to Alembic Python scripts. Each migration
has `upgrade()` and `downgrade()` functions. SQL is preserved verbatim using
`op.execute()` for complex SQL.

## Files to Create

| File | Description |
|------|-------------|
| `alembic.ini` | Alembic configuration |
| `alembic/env.py` | Migration environment (async-capable) |
| `alembic/versions/001_init_schema.py` | Core tables (accounts, customers, transfers metadata) |
| `alembic/versions/002_system_accounts.py` | System account seed data |
| `alembic/versions/003_products.py` | Product catalogue table |
| `alembic/versions/004_idempotency.py` | Idempotency key store |
| `alembic/versions/005_indexes.py` | Performance indexes |
| `alembic/versions/006_fx_rates.py` | FX rate table |
| `alembic/versions/007_batch_runs.py` | Batch run tracking |
| `alembic/versions/008_interest_capitalisation.py` | Interest capitalisation tracking |
| `alembic/versions/009_fee_collection.py` | Fee schedule tables |
| `alembic/versions/010_loan_repayments.py` | Loan repayment tracking |

## Go Source References

| Go Migration | Alembic Script |
|--------------|----------------|
| `../corebanking/migrations/000001_init_schema.up.sql` | `001_init_schema.py` |
| `../corebanking/migrations/000002_system_accounts.up.sql` | `002_system_accounts.py` |
| `../corebanking/migrations/000003_products.up.sql` | `003_products.py` |
| `../corebanking/migrations/000004_idempotency.up.sql` | `004_idempotency.py` |
| `../corebanking/migrations/000005_indexes.up.sql` | `005_indexes.py` |
| `../corebanking/migrations/000006_fx_rates.up.sql` | `006_fx_rates.py` |
| `../corebanking/migrations/000007_batch_runs.up.sql` | `007_batch_runs.py` |
| `../corebanking/migrations/000008_interest_capitalisation.up.sql` | `008_interest_capitalisation.py` |
| `../corebanking/migrations/000009_fee_collection.up.sql` | `009_fee_collection.py` |
| `../corebanking/migrations/000010_loan_repayments.up.sql` | `010_loan_repayments.py` |

Also consult the corresponding `.down.sql` files for downgrade logic.

## Acceptance Criteria

- [ ] `alembic upgrade head` creates all tables in PostgreSQL
- [ ] `alembic downgrade base` drops all tables cleanly
- [ ] Schema matches the Go version's migration output exactly
- [ ] `cbs migrate up` CLI command runs migrations (or will once CLI is built)
