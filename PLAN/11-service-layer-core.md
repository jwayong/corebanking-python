# Issue 11: Service Layer — Accounts, Customers, Balances

**Phase:** 3 - Core API
**Priority:** High
**Labels:** `phase-3`, `service`
**Depends on:** #07 (PG Repos), #09 (Caches), #10 (TB Repos)

## Summary

Implement the service layer for account management, customer reference
management, and balance queries. Services orchestrate TB and PG operations.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/service/__init__.py` | Service package, `build_services()` factory |
| `src/cbs/service/account_service.py` | Account creation, listing, closure |
| `src/cbs/service/customer_service.py` | Customer reference management |
| `src/cbs/service/balance_service.py` | Balance queries (TB lookup + computation) |

## Key Patterns

### AccountService

- **Create:** Validate request → create TB account (with correct flags/ledger) → write PG metadata → return response
- **List:** Query PG for metadata (filter by customer_ref, paginate with cursor) → enrich with TB balances
- **Close:** Validate account exists and is active → check zero balance via TB → mark closed in PG
- **Dual-write:** TB first (source of truth), then PG metadata

### CustomerService

- Simple PG-only service (customer references are metadata)
- Register customer → associate with accounts
- Get customer → return customer details + list of associated accounts

### BalanceService

- Query TB for account's debit/credit posted and pending totals
- Compute `posted`, `pending`, `available` using `domain.accounts.compute_balance()`
- Return structured balance response with currency info

### build_services() Factory

```python
def build_services(tb: TBClient, db: Database, config: CBSConfig) -> dict:
    """Wire all services with their dependencies."""
    # Create repos, caches, then services
    # Return dict keyed by service name for DI registration
```

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/service/account_service.go` | Account service |
| `../corebanking/internal/service/balance_service.go` | Balance service |
| `../corebanking/IMPLEMENTATION.md` | API endpoint specs |
| `../corebanking/DOMAIN-RETAIL.md` | Account creation walkthrough |

## Acceptance Criteria

- [ ] Account creation writes to both TB and PG
- [ ] Account listing supports cursor pagination
- [ ] Account closure rejects non-zero balances
- [ ] Balance queries return correct posted/pending/available
- [ ] Customer registration and lookup works
- [ ] `build_services()` correctly wires all dependencies
- [ ] Unit tests with mock repos
