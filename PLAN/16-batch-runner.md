# Issue 16: Batch Runner and Interest Accrual Job

**Phase:** 4 - Batch Operations
**Priority:** High
**Labels:** `phase-4`, `batch`
**Depends on:** #07 (PG Repos), #10 (TB Repos)

## Summary

Implement the batch job orchestration framework and the daily interest
accrual job. The runner provides a common framework; the accrual job
is the most complex batch pipeline.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/service/batch/__init__.py` | Batch package |
| `src/cbs/service/batch/runner.py` | Batch job registry and orchestration |
| `src/cbs/service/batch/interest_accrual.py` | Daily interest accrual job |

## Key Patterns

### Batch Runner

- Registry of named batch jobs
- Common interface: `run(business_date, dry_run)`
- Tracks batch runs in PG (`batch_runs` table)
- CLI invocation: `cbs batch run interest-accrual --business-date 2026-06-04`

### Interest Accrual Pipeline

Three-phase pipeline (ported from Go):

1. **Fetch:**
   - Query all interest-bearing accounts from PG
   - Batch-lookup their TB balances (up to 8191 per call)

2. **Compute:**
   - Calculate daily interest in-memory
   - `daily_interest = balance * annual_rate / 365`
   - Parallelise with `asyncio.gather` or `concurrent.futures`

3. **Write:**
   - Batch `create_transfers` to TB (8191 per call)
   - Debit: accrued interest receivable account
   - Credit: interest income account
   - Write audit log to PG
   - Mark batch run as complete

### Dry Run Mode

When `dry_run=True`:
- Compute all accruals but don't write to TB
- Print summary (total accounts, total interest, transfers that would be created)

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/service/batch_runner.go` | Batch framework |
| `../corebanking/internal/service/interest_accrual.go` | Interest accrual |
| `../corebanking/DOMAIN-RETAIL.md` | Batch process descriptions |
| `../corebanking/IMPLEMENTATION.md` | Batch CLI spec |

## Acceptance Criteria

- [ ] Batch runner can execute named jobs by CLI command
- [ ] Interest accrual fetches all interest-bearing accounts
- [ ] Accrual computation matches Go version's formula
- [ ] TB transfers are batched (8191 per call)
- [ ] Audit log is written to PG after completion
- [ ] Batch run status is tracked in PG
- [ ] Dry run mode computes without writing
- [ ] Integration test with real TB + PG
