# Issue 08: CLI Commands and Product Loader

**Phase:** 2 - Domain Model + CLI
**Priority:** High
**Labels:** `phase-2`, `cli`
**Depends on:** #02 (Migrations), #03 (TB Client), #04 (PG Pool), #05 (Domain Model)

## Summary

Implement the Typer CLI application with all commands: serve, setup,
migrate, batch, and status. Also implement the product YAML loader.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/cli/__init__.py` | CLI package |
| `src/cbs/cli/app.py` | Typer app root, registers sub-commands |
| `src/cbs/cli/serve.py` | `cbs serve` — starts uvicorn with the Litestar app |
| `src/cbs/cli/setup.py` | `cbs setup init/ledger/product/status` — bootstrap bank |
| `src/cbs/cli/migrate.py` | `cbs migrate up/down/status` — runs Alembic |
| `src/cbs/cli/batch.py` | `cbs batch run/list/status` — runs batch jobs |
| `src/cbs/cli/status.py` | `cbs status` — prints system status report |

## Key Details

### Setup Command

The `cbs setup init` command performs full bootstrap:
1. Verify TB and PG connections
2. Run Alembic migrations
3. Create TB system accounts (19 per currency)
4. Seed product catalogue from YAML into PG

### System Accounts (19 per currency)

Created in TigerBeetle per currency ledger:
- Cash Vault, Central Bank Reserve, Correspondent Nostro
- Settlement Account, Suspense Asset
- Loan accounts (Personal, Mortgage, Auto, Credit Card, Overdraft)
- Accrued Interest (Loan + Deposit)
- Liquidity Pool
- Deposit accounts (Current, Savings, Fixed, Escrow)
- Payable Customer
- (See `../corebanking/DOMAIN-RETAIL.md` for full list)

### Product YAML Loader

- Loads `products.example.yaml` with `pyyaml`
- Validates against a msgspec model
- Inserts into PG products table

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/cli/root.go` | CLI root, sub-commands |
| `../corebanking/internal/cli/serve.go` | Server startup |
| `../corebanking/internal/cli/setup.go` | Bank bootstrap |
| `../corebanking/internal/cli/migrate.go` | Migration runner |
| `../corebanking/internal/cli/batch.go` | Batch job runner |
| `../corebanking/internal/cli/version.go` | Version command |
| `../corebanking/internal/cli/status_print.go` | Status printer |
| `../corebanking/IMPLEMENTATION.md` | CLI spec |

## Acceptance Criteria

- [ ] `cbs --help` shows all sub-commands
- [ ] `cbs serve` starts uvicorn on the configured port
- [ ] `cbs setup init --currency USD` creates system accounts and seeds products
- [ ] `cbs migrate up` runs all pending Alembic migrations
- [ ] `cbs status` prints a system status report
- [ ] Product YAML loader validates and inserts products
