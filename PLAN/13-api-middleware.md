# Issue 13: API Middleware

**Phase:** 3 - Core API
**Priority:** High
**Labels:** `phase-3`, `middleware`
**Depends on:** #04 (PG Pool)

## Summary

Implement Litestar middleware for idempotency, request ID generation,
request/response logging, and global error handling.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/api/__init__.py` | API package |
| `src/cbs/api/middleware/__init__.py` | Middleware package |
| `src/cbs/api/middleware/idempotency.py` | Idempotency-Key middleware |
| `src/cbs/api/middleware/request_id.py` | X-Request-ID generation middleware |
| `src/cbs/api/middleware/logging.py` | Request/response structured logging |
| `src/cbs/api/middleware/error_handler.py` | Global exception handler |
| `src/cbs/api/responses.py` | Standard envelope, error response helpers |
| `src/cbs/api/deps.py` | Litestar DI providers (`Provide` functions) |

## Key Patterns

### Idempotency Middleware (Litestar `AbstractMiddleware`)

1. Read `Idempotency-Key` header
2. Check PG idempotency_repo for cached result → return cached response if found
3. Reserve key (INSERT with `pending` status)
4. Execute request, capture response
5. On success (status < 500): store response body, update to `completed`
6. On failure: update to `failed`

### Request ID Middleware

- Generate UUIDv7 per request
- Set `X-Request-ID` response header
- Inject into `structlog` context for correlation

### Error Handler

- Catch domain exceptions and map to HTTP status codes:
  - `ValidationError` → 400
  - `NotFoundError` → 404
  - `InsufficientBalanceError` → 409
  - `AccountClosedError` → 409
  - `IdempotencyConflictError` → 409
- Format as standard error envelope

### Standard Envelope

```python
def success_response(data: dict, request_id: str) -> dict:
    return {"status": "success", "data": data, "request_id": request_id, "timestamp": ...}

def error_response(code: str, message: str, request_id: str, details: dict | None = None) -> dict:
    return {"status": "error", "error": {"code": code, "message": message, "details": details}, ...}
```

### DI Providers (deps.py)

```python
from litestar.di import Provide

async def provide_account_service(request: Request) -> AccountService:
    return request.app.state.services["account"]

# ... similar for all services
```

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/api/middleware/idempotency.go` | Idempotency |
| `../corebanking/internal/api/middleware/requestid.go` | Request ID |
| `../corebanking/internal/api/middleware/logging.go` | Logging |
| `../corebanking/internal/api/middleware/recovery.go` | Panic recovery |
| `../corebanking/internal/api/middleware/cors.go` | CORS |
| `../corebanking/pkg/httputil/respond.go` | Response helpers |

## Acceptance Criteria

- [ ] Idempotency middleware returns cached response for duplicate keys
- [ ] Idempotency middleware reserves keys and prevents concurrent execution
- [ ] Request ID middleware generates UUIDv7 and sets header
- [ ] Error handler maps all domain exceptions to correct HTTP codes
- [ ] Error responses use the standard envelope format
- [ ] CORS is configured via Litestar's built-in CORSMiddleware
- [ ] DI providers resolve services from app state
- [ ] Integration tests for middleware behaviour
