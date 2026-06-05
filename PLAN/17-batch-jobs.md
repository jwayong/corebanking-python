# Issue 17: Remaining Batch Jobs (Capitalise, Fees, Arrears)

**Phase:** 4 - Batch Operations
**Priority:** Medium
**Labels:** `phase-4`, `batch`
**Depends on:** #16 (Batch Runner + Interest Accrual)

## Summary

Implement the remaining three batch jobs: monthly interest capitalisation,
scheduled fee collection, and daily arrears check.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/service/batch/interest_capitalise.py` | Monthly: move accrued interest to customer accounts |
| `src/cbs/service/batch/fee_collection.py` | Monthly/daily: charge scheduled fees |
| `src/cbs/service/batch/arrears_check.py` | Daily: detect missed loan payments, update arrears status |

## Key Patterns

Each follows the same pipeline as interest accrual:
**read PG config → read TB balances → compute → write TB → audit PG**

### Interest Capitalisation (Monthly)

- Moves accumulated interest from accrual accounts to customer accounts
- Reads accrued interest balances from TB for all interest-bearing accounts
- Creates transfers: debit accrued interest receivable, credit customer deposit
- Runs monthly (typically month-end)

### Fee Collection (Monthly/Daily)

- Reads fee schedules from PG
- For each account with scheduled fees:
  - Compute fee amount
  - Create TB transfer: debit customer account, credit fee income account
- Handles insufficient balance: skip and log, or create partial charge

### Arrears Check (Daily)

- Reads all active loans from PG
- For each loan, checks TB balance to detect missed payments
- If balance > expected (payments behind):
  - Update loan status to `in_arrears` in PG
  - Optionally create penalty transfer
- Reports arrears summary to audit log

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/service/interest_capitalise.go` | Capitalisation logic |
| `../corebanking/internal/service/fee_collection.go` | Fee collection |
| `../corebanking/internal/service/arrears_check.go` | Arrears detection |
| `../corebanking/DOMAIN-RETAIL.md` | Batch process descriptions |

## Acceptance Criteria

- [ ] Interest capitalisation moves accrued interest to customer accounts
- [ ] Fee collection charges all scheduled fees
- [ ] Fee collection handles insufficient balance gracefully
- [ ] Arrears check detects missed loan payments
- [ ] Arrears check updates loan status in PG
- [ ] All batch jobs write audit logs to PG
- [ ] All batch jobs track runs via batch_repo
- [ ] Integration tests for each job
