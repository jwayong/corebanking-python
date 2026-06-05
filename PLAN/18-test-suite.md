# Issue 18: Test Suite (Unit, Integration, E2E)

**Phase:** Cross-Cutting
**Priority:** High
**Labels:** `testing`
**Depends on:** #15 (App Factory), #17 (Batch Jobs)

## Summary

Implement the full test pyramid: unit tests for domain/services/utils,
integration tests for API endpoints with real databases, and end-to-end
tests for complete banking scenarios.

## Files to Create

| File | Description |
|------|-------------|
| `tests/__init__.py` | Test package |
| `tests/conftest.py` | Shared fixtures, test client, mock stores |
| `tests/unit/domain/` | Domain logic unit tests |
| `tests/unit/service/` | Service layer unit tests |
| `tests/unit/util/` | Utility unit tests |
| `tests/integration/test_accounts_api.py` | Account API integration tests |
| `tests/integration/test_transfers_api.py` | Transfer API integration tests |
| `tests/integration/test_fx_api.py` | FX API integration tests |
| `tests/integration/test_batch_jobs.py` | Batch job integration tests |
| `tests/e2e/test_full_flow.py` | End-to-end banking scenarios |

## Test Pyramid

```
          +---------------------+
          |   E2E Scenarios     |  ~10 tests  (full banking flows)
          +---------------------+
          |  Integration Tests  |  ~30 tests  (API + real DB)
          +---------------------+
          |    Unit Tests       |  ~100 tests (services, domain, utils)
          +---------------------+
```

## Key Patterns

### Unit Tests

- **Domain:** `compute_balance()` for all account types, currency lookups, transfer code mapping
- **Services:** Mock TB and PG repos, verify orchestration logic and error handling
- **Utilities:** UUIDv7 generation/conversion, amount conversion, Uint128 handling

### Integration Tests

- Use `pytest-asyncio` with real TigerBeetle + PostgreSQL (Docker Compose test env)
- Test each API endpoint via Litestar's `TestClient` and `httpx.AsyncClient`
- Verify dual-write consistency (TB state matches PG state after operations)

### E2E Scenarios

Full banking flows that exercise multiple endpoints:
1. Customer registration → open current account → deposit → withdraw → check balance
2. Loan disbursement → repayment with interest → closure
3. FX conversion between USD and EUR accounts
4. Two-phase hold → capture/void
5. Interest accrual batch → capitalisation

### Test Fixtures (conftest.py)

```python
@pytest.fixture
async def app():
    """Create test Litestar app with Docker TB + PG."""

@pytest.fixture
async def client(app):
    """httpx.AsyncClient for API testing."""

@pytest.fixture
def mock_tb():
    """Mock TBClient for unit tests."""

@pytest.fixture
def mock_db():
    """Mock Database for unit tests."""
```

## Test Commands

```bash
make test              # All tests
make test-unit         # Unit tests only
make test-integration  # Integration tests (requires Docker)
make test-e2e          # E2E scenarios
```

## Acceptance Criteria

- [ ] `pytest` runs all tests successfully
- [ ] Unit tests cover domain logic, services, and utilities
- [ ] Integration tests cover all API endpoints
- [ ] E2E tests cover the 5 core banking scenarios
- [ ] Test coverage > 80% for `src/cbs/`
- [ ] Integration tests use real TB + PG (Docker Compose)
