# Issue 12: Service Layer — Transfers, FX, Holds, Loans, Fees, Settlements

**Phase:** 3 - Core API
**Priority:** High
**Labels:** `phase-3`, `service`
**Depends on:** #07 (PG Repos), #09 (Caches), #10 (TB Repos)

## Summary

Implement the service layer for all transfer-related operations:
standard transfers, FX transfers, two-phase holds, loan operations,
fee charging, and settlement batch operations.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/service/transfer_service.py` | Transfer orchestration (validate → execute TB → write PG) |
| `src/cbs/service/fx_service.py` | FX transfer (two linked TB transfers across ledgers) |
| `src/cbs/service/hold_service.py` | Two-phase holds (create pending → capture/void) |
| `src/cbs/service/loan_service.py` | Loan disbursement, repayment, repayment with fee |
| `src/cbs/service/fee_service.py` | Fee charging |
| `src/cbs/service/settlement_service.py` | Settlement batch operations |

## Key Patterns

### TransferService

The core orchestration flow:
1. Resolve debit and credit accounts from TB
2. Validate: both accounts active, currency matches ledger, sufficient balance
3. Build TB transfer with UUIDv7 ID, correct code, ledger, flags
4. Execute `create_transfers` in TigerBeetle
5. Write metadata to PostgreSQL (async, fire-and-forget with error logging)
6. Return transfer response

### FXService

- Two linked TB transfers across two ledgers (e.g., USD ledger + EUR ledger)
- Transfer 1: debit source account, credit FX suspense (linked flag set)
- Transfer 2: debit FX suspense, credit destination account
- Both transfers are atomically linked — either both succeed or both fail
- Uses FX rate from cache to compute amounts

### HoldService

- **Create hold:** Create transfer with `flags.pending = True`
- **Capture:** Post the pending transfer (TB `post_pending_transfer`)
- **Void:** Expire the pending transfer (TB `post_pending_transfer` with void)

### LoanService

- **Disburse:** Create transfer from loan account to customer account
- **Repay:** Create transfer from customer account to loan account
- **Repay with fee:** Two linked transfers (repayment + fee charge)

### FeeService

- Single transfer from customer account to fee income account
- Uses fee schedule from PG to determine amount

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/service/transfer_service.go` | Transfer orchestration |
| `../corebanking/internal/service/fx_service.go` | FX transfers |
| `../corebanking/internal/service/hold_service.go` | Two-phase holds |
| `../corebanking/internal/service/loan_service.go` | Loan operations |
| `../corebanking/internal/service/settlement_service.go` | Settlements |
| `../corebanking/IMPLEMENTATION.md` | Endpoint specs, request/response schemas |
| `../corebanking/DOMAIN-RETAIL.md` | Transaction walkthroughs |

## Acceptance Criteria

- [ ] Standard transfers work for all transfer codes (1-20)
- [ ] FX transfers atomically move funds across currency ledgers
- [ ] Holds can be created, captured, and voided
- [ ] Loan disbursement creates correct TB transfers
- [ ] Loan repayment handles interest + principal
- [ ] Fee charging creates correct income transfers
- [ ] All services follow the dual-write pattern (TB first, PG second)
- [ ] Unit tests with mock repos for all services
