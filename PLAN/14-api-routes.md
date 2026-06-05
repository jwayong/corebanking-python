# Issue 14: API Route Handlers

**Phase:** 3 - Core API
**Priority:** High
**Labels:** `phase-3`, `api`
**Depends on:** #11 (Core Services), #12 (Transfer Services), #13 (Middleware)

## Summary

Implement all HTTP route handlers using Litestar's `@get`/`@post`/`@patch`
decorators with `Provide` DI. Each route module mirrors the Go handler files.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/api/router.py` | Route registration (collects all routers) |
| `src/cbs/api/routes/__init__.py` | Routes package |
| `src/cbs/api/routes/health.py` | `GET /health/live`, `GET /health/ready` |
| `src/cbs/api/routes/customers.py` | `POST /api/v1/customers`, `GET /api/v1/customers/{ref}` |
| `src/cbs/api/routes/accounts.py` | `POST/GET /api/v1/accounts`, `GET /api/v1/accounts/{id}`, `PATCH .../close` |
| `src/cbs/api/routes/transfers.py` | `POST /api/v1/transfers`, `GET /api/v1/transfers/{id}` |
| `src/cbs/api/routes/fx.py` | `POST /api/v1/transfers/fx` |
| `src/cbs/api/routes/holds.py` | `POST /api/v1/transfers/hold`, `.../capture`, `.../void` |
| `src/cbs/api/routes/fees.py` | `POST /api/v1/fees/charge` |
| `src/cbs/api/routes/balances.py` | `GET /api/v1/accounts/{id}/balance` |
| `src/cbs/api/routes/loans.py` | `POST /api/v1/loans/{id}/disburse`, `.../repay`, `.../repay_with_fee` |
| `src/cbs/api/routes/statements.py` | `GET /api/v1/accounts/{id}/statement` |

## Endpoint Table

| Method | Path | Handler | Service |
|--------|------|---------|---------|
| `POST` | `/api/v1/customers` | `customers::register` | CustomerService |
| `GET` | `/api/v1/customers/{ref}` | `customers::get` | CustomerService |
| `POST` | `/api/v1/accounts` | `accounts::create_account` | AccountService |
| `GET` | `/api/v1/accounts/{id}` | `accounts::get_account` | AccountService |
| `GET` | `/api/v1/accounts` | `accounts::list_accounts` | AccountService |
| `PATCH` | `/api/v1/accounts/{id}/close` | `accounts::close_account` | AccountService |
| `GET` | `/api/v1/accounts/{id}/balance` | `balances::get_balance` | BalanceService |
| `GET` | `/api/v1/accounts/{id}/transactions` | `accounts::transactions` | AccountService |
| `GET` | `/api/v1/accounts/{id}/statement` | `statements::statement` | AccountService |
| `POST` | `/api/v1/transfers` | `transfers::execute` | TransferService |
| `GET` | `/api/v1/transfers/{id}` | `transfers::get` | TransferService |
| `POST` | `/api/v1/transfers/fx` | `fx::exchange` | FXService |
| `POST` | `/api/v1/transfers/hold` | `holds::create_hold` | HoldService |
| `POST` | `/api/v1/transfers/hold/{id}/capture` | `holds::capture` | HoldService |
| `POST` | `/api/v1/transfers/hold/{id}/void` | `holds::void` | HoldService |
| `POST` | `/api/v1/fees/charge` | `fees::charge` | FeeService |
| `POST` | `/api/v1/loans/{id}/disburse` | `loans::disburse` | LoanService |
| `POST` | `/api/v1/loans/{id}/repay` | `loans::repay` | LoanService |
| `POST` | `/api/v1/loans/{id}/repay_with_fee` | `loans::repay_with_fee` | LoanService |
| `GET` | `/health/live` | `health::live` | -- |
| `GET` | `/health/ready` | `health::ready` | TB + PG |

## Litestar Route Pattern

```python
from litestar import Router, get, post
from litestar.di import Provide

@post("/", status_code=201)
async def create_account(
    data: CreateAccountRequest,
    svc: AccountService = Provide(provide_account_service),
) -> CreateAccountResponse:
    return await svc.create(data)

accounts_router = Router(path="/accounts", route_handlers=[...])
```

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/api/router.go` | Route registration |
| `../corebanking/internal/api/handler/*.go` | All handler files |
| `../corebanking/IMPLEMENTATION.md` | Full endpoint specs with request/response schemas |

## Acceptance Criteria

- [ ] All 21 endpoints are implemented and return correct status codes
- [ ] Request bodies validated via msgspec
- [ ] Responses use the standard envelope format
- [ ] Route registration collects all routers into a single list
- [ ] Health endpoints return 200 when TB and PG are reachable
- [ ] Integration tests for each endpoint group
