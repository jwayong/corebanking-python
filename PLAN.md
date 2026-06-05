# Core Banking System (Python) — Implementation Plan

> **Source:** [github.com/jwayong/corebanking](https://github.com/jwayong/corebanking) (Go, TigerBeetle, PostgreSQL)
> **Target:** Python 3.14+ port with identical architecture and domain model
> **Status:** Planning

---

## 1. Executive Summary

This plan describes how to port the Go-based core banking system (CBS) to Python while
preserving the original architecture, domain model, and all functional behaviour.

The source system is a **~29,300-line Go application** with:
- HTTP REST API (chi router)
- TigerBeetle OLTP database for financial state (balances, transfers)
- PostgreSQL OLGP database for metadata (accounts, products, audit)
- CLI for setup, migrations, and batch operations
- Stateless service design with dual-write consistency

The Python port will be a **functionally equivalent** system using modern Python tooling
while retaining the same TigerBeetle + PostgreSQL database architecture.

---

## 2. Technology Stack

| Concern | Go (Source) | Python (Target) | Rationale |
|---------|-------------|-----------------|-----------|
| **Language** | Go 1.25 | Python 3.14+ | Latest Python with native `uuid.uuid7()`, match/case, generics, improved error messages |
| **Web Framework** | chi router | **FastAPI** | Async-first, automatic OpenAPI, Pydantic validation, high performance |
| **ASGI Server** | net/http | **uvicorn** | Standard ASGI server for FastAPI |
| **TigerBeetle Client** | tigerbeetle-go | **tigerbeetle-python** | Official Python client (same API surface) |
| **PostgreSQL Driver** | pgx/v5 | **asyncpg** + **psycopg3** | asyncpg for performance; psycopg3 for migrations/CLI |
| **ORM / Query Builder** | raw SQL (pgx) | **SQLAlchemy 2.0** (async, Core only) | Type-safe queries without ORM overhead; async-native |
| **Migrations** | golang-migrate | **Alembic** | Standard migration tool, integrates with SQLAlchemy |
| **CLI Framework** | cobra | **typer** | Modern, type-hinted CLI framework |
| **UUID v7** | google/uuid | **stdlib `uuid.uuid7()`** (Python 3.14+) | Native RFC 9562 UUIDv7 in stdlib; no third-party dependency |
| **Config** | env/flag (custom) | **pydantic-settings** | Type-safe environment/config loading |
| **Validation** | custom structs | **Pydantic v2** | Integrated with FastAPI; schema generation |
| **Logging** | log/slog | **structlog** | Structured JSON logging, middleware integration |
| **Testing** | go test | **pytest** + **pytest-asyncio** | Standard Python testing, async support |
| **Linting** | golangci-lint | **ruff** | Fast linter/formatter, single tool |
| **Type Checking** | go build | **mypy** | Static type checking for Python |
| **Containerization** | Alpine multi-stage | **slim Python image** | Docker deployment parity |

---

## 3. Project Structure

```
corebanking-python/
├── pyproject.toml              # Project metadata, dependencies, tool config
├── alembic.ini                 # Alembic migration config
├── alembic/
│   ├── env.py                  # Migration environment
│   └── versions/               # Migration scripts (mirrors Go migrations/)
│       ├── 001_init_schema.py
│       ├── 002_system_accounts.py
│       ├── 003_products.py
│       ├── 004_idempotency.py
│       ├── 005_indexes.py
│       ├── 006_fx_rates.py
│       ├── 007_batch_runs.py
│       ├── 008_interest_capitalisation.py
│       ├── 009_fee_collection.py
│       └── 010_loan_repayments.py
├── docker-compose.yml          # Local dev stack (TB + PG + API)
├── Dockerfile                  # Multi-stage Python build
├── Makefile                    # Dev convenience targets
├── .env.example                # Environment variable template
├── products.example.yaml       # Product seed file
│
├── src/
│   └── cbs/
│       ├── __init__.py
│       ├── main.py             # FastAPI app factory, lifespan, dependency injection
│       │
│       ├── config.py           # pydantic-settings config (env vars)
│       │
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── app.py          # typer app root
│       │   ├── serve.py        # `cbs serve` — start uvicorn
│       │   ├── setup.py        # `cbs setup` — bootstrap ledgers/products
│       │   ├── migrate.py      # `cbs migrate` — run alembic
│       │   ├── batch.py        # `cbs batch` — run batch jobs
│       │   └── status.py       # `cbs status` — print system status
│       │
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── accounts.py     # Account codes, balance computation, types
│       │   ├── transfers.py    # Transfer types, codes, request/response models
│       │   ├── currency.py     # Currency info, ledger mapping, scale
│       │   ├── products.py     # Product domain model
│       │   ├── loans.py        # Loan lifecycle, request/response models
│       │   ├── settlements.py  # Settlement domain model
│       │   └── errors.py       # Domain exceptions (InsufficientBalance, etc.)
│       │
│       ├── api/
│       │   ├── __init__.py
│       │   ├── deps.py         # FastAPI dependencies (get services from app state)
│       │   ├── router.py       # Route registration
│       │   ├── responses.py    # Standard envelope, error response helpers
│       │   ├── middleware/
│       │   │   ├── __init__.py
│       │   │   ├── request_id.py   # X-Request-ID middleware
│       │   │   ├── idempotency.py  # Idempotency-Key middleware
│       │   │   ├── logging.py      # Request/response logging
│       │   │   └── error_handler.py# Global exception handler
│       │   └── routes/
│       │       ├── __init__.py
│       │       ├── health.py       # /health/live, /health/ready
│       │       ├── customers.py    # /api/v1/customers
│       │       ├── accounts.py     # /api/v1/accounts
│       │       ├── transfers.py    # /api/v1/transfers
│       │       ├── fx.py           # /api/v1/transfers/fx
│       │       ├── holds.py        # /api/v1/transfers/hold/*
│       │       ├── fees.py         # /api/v1/fees/charge
│       │       ├── balances.py     # /api/v1/accounts/{id}/balance
│       │       ├── loans.py        # /api/v1/loans/*
│       │       └── statements.py   # /api/v1/accounts/{id}/statement
│       │
│       ├── service/
│       │   ├── __init__.py
│       │   ├── account_service.py      # Account creation, closure
│       │   ├── transfer_service.py     # Transfer orchestration
│       │   ├── fx_service.py           # FX transfer (cross-currency)
│       │   ├── hold_service.py         # Two-phase holds (create/capture/void)
│       │   ├── balance_service.py      # Balance queries
│       │   ├── loan_service.py         # Loan disbursement, repayment
│       │   ├── fee_service.py          # Fee charging
│       │   ├── customer_service.py     # Customer reference management
│       │   ├── settlement_service.py   # Settlement batch operations
│       │   └── batch/
│       │       ├── __init__.py
│       │       ├── runner.py           # Batch job orchestration
│       │       ├── interest_accrual.py # Daily interest accrual
│       │       ├── interest_capitalise.py  # Monthly capitalisation
│       │       ├── fee_collection.py   # Scheduled fee collection
│       │       └── arrears_check.py    # Loan arrears detection
│       │
│       ├── store/
│       │   ├── __init__.py
│       │   ├── tigerbeetle/
│       │   │   ├── __init__.py
│       │   │   ├── client.py           # TB client wrapper, lifecycle
│       │   │   ├── account_repo.py     # Account CRUD against TB
│       │   │   └── transfer_repo.py    # Transfer operations against TB
│       │   └── postgres/
│       │       ├── __init__.py
│       │       ├── database.py         # asyncpg pool setup, session factory
│       │       ├── account_repo.py     # Account metadata queries
│       │       ├── customer_repo.py    # Customer-account relationships
│       │       ├── product_repo.py     # Product catalogue queries
│       │       ├── fx_rate_repo.py     # FX rate queries
│       │       ├── idempotency_repo.py # Idempotency key store
│       │       ├── transfer_repo.py    # Transfer metadata writes/reads
│       │       ├── system_account_repo.py  # System account registry
│       │       ├── loan_repo.py        # Loan details queries
│       │       ├── batch_repo.py       # Batch run tracking
│       │       ├── settlement_repo.py  # Settlement batch queries
│       │       ├── fee_repo.py         # Fee schedule queries
│       │       └── audit_repo.py       # Audit log writes
│       │
│       ├── cache/
│       │   ├── __init__.py
│       │   ├── fx_cache.py         # In-memory FX rate cache (TTL-based)
│       │   ├── product_cache.py    # In-memory product cache (TTL-based)
│       │   └── ledger_cache.py     # Currency↔ledger, code mappings (immutable)
│       │
│       └── util/
│           ├── __init__.py
│           ├── uuid.py             # UUIDv7 helpers (stdlib uuid.uuid7), byte conversion for TB
│           ├── amount.py           # Amount/scale conversion helpers
│           └── tb_types.py         # TigerBeetle type adapters (Uint128 ↔ bytes)
│
└── tests/
    ├── __init__.py
    ├── conftest.py                 # Shared fixtures, test client, mock stores
    ├── unit/
    │   ├── domain/                 # Domain logic unit tests
    │   ├── service/                # Service layer unit tests
    │   └── util/                   # Utility unit tests
    ├── integration/
    │   ├── test_accounts_api.py    # Account API integration tests
    │   ├── test_transfers_api.py   # Transfer API integration tests
    │   ├── test_fx_api.py          # FX API integration tests
    │   └── test_batch_jobs.py      # Batch job integration tests
    └── e2e/
        └── test_full_flow.py       # End-to-end banking scenarios
```

---

## 4. Implementation Phases

### Phase 1: Foundation (Infrastructure + Skeleton)

**Goal:** Bootable FastAPI app connected to TigerBeetle and PostgreSQL with migrations applied.

#### 4.1.1 Project Bootstrap

```toml
# pyproject.toml
[project]
name = "corebanking"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "asyncpg>=0.30",
    "psycopg[binary]>=3.2",       # For alembic (sync)
    "sqlalchemy[asyncio]>=2.0",
    "alembic>=1.13",
    "tigerbeetle>=0.16",          # Official Python client
    "typer>=0.12",
    "structlog>=24.4",
    "pyyaml>=6.0",
    "httpx>=0.27",                # For testing
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "mypy>=1.11",
    "respx>=0.21",                # HTTP mocking
]

[project.scripts]
cbs = "cbs.cli.app:cli_app"

[tool.ruff]
target-version = "py314"

[tool.mypy]
python_version = "3.14"
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

#### 4.1.2 Configuration (pydantic-settings)

```python
# src/cbs/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class CBSConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CBS_")

    tb_addresses: str                          # "tigerbeetle:3001"
    pg_dsn: str                                # "postgres://cbs:cbs_dev@..."
    port: int = 8080
    log_level: str = "info"
    pg_pool_max: int = 10
    cache_ttl_fx: int = 30                     # seconds
    cache_ttl_product: int = 300               # seconds
```

#### 4.1.3 Docker Compose (identical to Go version)

The `docker-compose.yml` will define the same four services:
- `tigerbeetle` (single replica, port 3001)
- `postgres` (PostgreSQL 16, port 5432)
- `cbs-migrate` (runs alembic, exits)
- `cbs-api` (FastAPI app, port 8080)

#### 4.1.4 Dockerfile

```dockerfile
# Build stage
FROM python:3.14-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .

# Runtime stage
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app
WORKDIR /app
EXPOSE 8080
CMD ["uvicorn", "cbs.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

#### 4.1.5 Migrations (Alembic)

Port all 10 Go migration files (`.sql`) to Alembic Python scripts.
Each migration is a pair: `upgrade()` and `downgrade()`.

The SQL is preserved verbatim where possible; Alembic's `op.execute()` is used
for complex SQL that doesn't fit Alembic's declarative API.

**Migration mapping:**

| Go Migration | Alembic Script |
|--------------|----------------|
| `000001_init_schema.up.sql` | `001_init_schema.py` |
| `000002_system_accounts.up.sql` | `002_system_accounts.py` |
| `000003_products.up.sql` | `003_products.py` |
| `000004_idempotency.up.sql` | `004_idempotency.py` |
| `000005_indexes.up.sql` | `005_indexes.py` |
| `000006_fx_rates.up.sql` | `006_fx_rates.py` |
| `000007_batch_runs.up.sql` | `007_batch_runs.py` |
| `000008_interest_capitalisation.up.sql` | `008_interest_capitalisation.py` |
| `000009_fee_collection.up.sql` | `009_fee_collection.py` |
| `000010_loan_repayments.up.sql` | `010_loan_repayments.py` |

#### 4.1.6 TigerBeetle Client Wrapper

```python
# src/cbs/store/tigerbeetle/client.py
from tigerbeetle import Client, ClusterCredential

class TBClient:
    def __init__(self, addresses: list[str], cluster_id: int = 0):
        self._client = Client(cluster_id=cluster_id, replica_addresses=addresses)

    async def create_accounts(self, accounts: list[dict]) -> list[dict]:
        return self._client.create_accounts(accounts)

    async def create_transfers(self, transfers: list[dict]) -> list[dict]:
        return self._client.create_transfers(transfers)

    async def lookup_accounts(self, ids: list[bytes]) -> list[dict]:
        return self._client.lookup_accounts(ids)

    async def lookup_transfers(self, ids: list[bytes]) -> list[dict]:
        return self._client.lookup_transfers(ids)

    async def get_account_transfers(self, account_id: bytes, **kwargs) -> list[dict]:
        return self._client.get_account_transfers(account_id, **kwargs)

    async def get_account_balances(self, account_id: bytes, **kwargs) -> list[dict]:
        return self._client.get_account_balances(account_id, **kwargs)
```

> **Note:** The TigerBeetle Python client is synchronous. For true async concurrency,
> wrap calls in `asyncio.to_thread()` or use the synchronous client inside FastAPI's
> sync endpoint handlers. Benchmark both approaches.

#### 4.1.7 PostgreSQL Connection Pool

```python
# src/cbs/store/postgres/database.py
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

class Database:
    def __init__(self, dsn: str, max_size: int = 10):
        self._engine = create_async_engine(
            dsn.replace("postgres://", "postgresql+asyncpg://"),
            pool_size=max_size,
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    def session(self):
        return self._session_factory()

    async def close(self):
        await self._engine.dispose()
```

---

### Phase 2: Domain Model + CLI Setup

**Goal:** Complete domain layer with all constants, types, and balance computation;
`cbs setup` CLI bootstraps the bank.

#### 4.2.1 Domain Constants

```python
# src/cbs/domain/accounts.py
from enum import IntEnum

class AccountCode(IntEnum):
    # Assets (debit balance)
    CASH_VAULT = 1101
    CENTRAL_BANK_RESERVE = 1110
    CORRESPONDENT_NOSTRO = 1120
    SETTLEMENT_ACCOUNT = 1201
    SUSPENSE_ASSET = 1301
    LOAN_PERSONAL = 1401
    LOAN_MORTGAGE = 1410
    LOAN_AUTO = 1420
    LOAN_CREDIT_CARD = 1430
    LOAN_OVERDRAFT = 1440
    ACCRUED_INTEREST_LOAN = 1501
    LIQUIDITY_POOL = 1601

    # Liabilities (credit balance)
    DEPOSIT_CURRENT = 2101
    DEPOSIT_SAVINGS = 2110
    DEPOSIT_FIXED = 2120
    DEPOSIT_ESCROW = 2130
    ACCRUED_INTEREST_DEP = 2201
    PAYABLE_CUSTOMER = 2301

    # Equity
    SHARE_CAPITAL = 3101
    RETAINED_EARNINGS = 3110
    GENERAL_RESERVE = 3120
    CURRENT_YEAR_PL = 3130

    # Income
    INC_INTEREST_LOAN = 4101
    INC_FEE_SERVICE = 4110
    INC_FEE_ACCOUNT = 4111
    INC_FEE_TRANSACTION = 4112
    INC_FX_GAIN = 4120
    INC_PENALTY = 4130

    # Expenses
    EXP_INTEREST_DEP = 5101
    EXP_OPERATIONS = 5110
    EXP_FX_LOSS = 5120
    EXP_LOAN_WRITE_OFF = 5130

    # Suspense / Clearing
    SUSPENSE_TXN = 6101
    CLEARING_OUTBOUND = 6201
    CLEARING_INBOUND = 6202

def is_debit_balance(code: int) -> bool:
    return (1000 <= code < 2000) or (5000 <= code < 6000)

def compute_balance(
    debits_posted: int, credits_posted: int,
    debits_pending: int, credits_pending: int,
    code: int,
) -> dict:
    if is_debit_balance(code):
        posted = debits_posted - credits_posted
        available = posted + debits_pending - credits_pending
    else:
        posted = credits_posted - debits_posted
        available = posted - debits_pending + credits_pending
    return {"posted": posted, "pending": posted - available, "available": available}
```

#### 4.2.2 Currency/Ledger Mapping

```python
# src/cbs/domain/currency.py
from dataclasses import dataclass

@dataclass(frozen=True)
class CurrencyInfo:
    code: str        # ISO 4217 alpha
    ledger: int      # ISO 4217 numeric
    scale: int       # decimal places
    name: str

CURRENCIES: dict[str, CurrencyInfo] = {
    "USD": CurrencyInfo("USD", 840, 2, "US Dollar"),
    "EUR": CurrencyInfo("EUR", 978, 2, "Euro"),
    "GBP": CurrencyInfo("GBP", 826, 2, "British Pound"),
    "SGD": CurrencyInfo("SGD", 702, 2, "Singapore Dollar"),
    "MYR": CurrencyInfo("MYR", 458, 2, "Malaysian Ringgit"),
    "JPY": CurrencyInfo("JPY", 392, 0, "Japanese Yen"),
    "THB": CurrencyInfo("THB", 764, 2, "Thai Baht"),
    "IDR": CurrencyInfo("IDR", 360, 2, "Indonesian Rupiah"),
    "AUD": CurrencyInfo("AUD", 36, 2, "Australian Dollar"),
    "CHF": CurrencyInfo("CHF", 756, 2, "Swiss Franc"),
}

def lookup_currency(code: str) -> CurrencyInfo:
    if code not in CURRENCIES:
        raise ValueError(f"Unsupported currency: {code}")
    return CURRENCIES[code]

def ledger_to_currency(ledger: int) -> str | None:
    for info in CURRENCIES.values():
        if info.ledger == ledger:
            return info.code
    return None
```

#### 4.2.3 Transfer Types

```python
# src/cbs/domain/transfers.py
from enum import IntEnum

class TransferCode(IntEnum):
    DEPOSIT = 1
    WITHDRAWAL = 2
    TRANSFER = 3
    FX_DEBIT = 4
    FX_CREDIT = 5
    PAYMENT_OUT = 6
    PAYMENT_IN = 7
    HOLD = 8
    CAPTURE = 9
    VOID = 10
    FEE = 11
    INTEREST_CREDIT = 12
    INTEREST_DEBIT = 13
    CORRECTION = 14
    SETTLEMENT = 15
    LOAN_DISBURSEMENT = 16
    LOAN_REPAYMENT = 17
    WRITE_OFF = 18
    PENALTY = 19
    INTEREST_CAPITALISATION = 20
```

#### 4.2.4 CLI Setup Command

```python
# src/cbs/cli/setup.py
import typer
from cbs.domain.accounts import AccountCode
from cbs.store.tigerbeetle.client import TBClient
from cbs.store.postgres.database import Database

setup_app = typer.Typer()

# System accounts created per currency (19 accounts, same as Go version)
SYSTEM_ACCOUNTS = [
    (AccountCode.CASH_VAULT, "Cash Vault", {"credits_must_not_exceed_debits": True, "history": True}),
    (AccountCode.CENTRAL_BANK_RESERVE, "Central Bank Reserve", {"credits_must_not_exceed_debits": True, "history": True}),
    # ... all 19 accounts
]

@setup_app.command("init")
def setup_init(
    currency: list[str] = typer.Option(..., help="ISO 4217 code(s) to initialise"),
    product_file: str = typer.Option("products.yaml", help="Path to products YAML"),
):
    """Full bootstrap: verify connections → migrate → create ledgers → seed products."""
    ...

@setup_app.command("ledger")
def setup_ledger(currency: list[str] = typer.Option(...)):
    """Create TigerBeetle system accounts for given currencies."""
    ...

@setup_app.command("product")
def setup_product(file: str = typer.Option("products.yaml")):
    """Seed product catalogue into PostgreSQL."""
    ...

@setup_app.command("status")
def setup_status():
    """Print setup verification report."""
    ...
```

#### 4.2.5 Product YAML Loader

Same `products.yaml` format as the Go version. Loaded with `pyyaml` and validated
against a Pydantic model before insertion.

---

### Phase 3: Core API

**Goal:** All HTTP endpoints implemented with service layer and data stores.

#### 4.3.1 FastAPI Application Factory

```python
# src/cbs/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from cbs.config import CBSConfig
from cbs.store.tigerbeetle.client import TBClient
from cbs.store.postgres.database import Database
from cbs.api.router import create_router
from cbs.service import build_services
import structlog

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = CBSConfig()
    tb = TBClient(config.tb_addresses.split(","))
    db = Database(config.pg_dsn, config.pg_pool_max)
    services = build_services(tb, db, config)
    app.state.services = services
    yield
    await db.close()

def create_app() -> FastAPI:
    app = FastAPI(title="Core Banking System", lifespan=lifespan)
    app.include_router(create_router())
    return app

app = create_app()
```

#### 4.3.2 API Routes

Each route module mirrors the Go handler files. FastAPI's `Depends()` injects
the relevant service from app state.

```python
# src/cbs/api/routes/accounts.py
from fastapi import APIRouter, Depends, status
from cbs.service.account_service import AccountService
from cbs.api.deps import get_account_service

router = APIRouter(prefix="/accounts", tags=["accounts"])

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_account(
    req: CreateAccountRequest,
    svc: AccountService = Depends(get_account_service),
):
    return await svc.create(req)

@router.get("/{account_id}")
async def get_account(
    account_id: str,
    svc: AccountService = Depends(get_account_service),
):
    return await svc.get(account_id)

@router.get("/")
async def list_accounts(
    customer_ref: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    svc: AccountService = Depends(get_account_service),
):
    return await svc.list(customer_ref=customer_ref, limit=limit, cursor=cursor)

@router.patch("/{account_id}/close")
async def close_account(
    account_id: str,
    svc: AccountService = Depends(get_account_service),
):
    return await svc.close(account_id)
```

#### 4.3.3 Service Layer

Each service class mirrors the Go service files. Key patterns:

- **TransferService:** idempotency middleware handles PG idempotency check;
  service focuses on account resolution → validation → TB execution → PG metadata write
- **FXService:** builds two linked TB transfers atomically
- **HoldService:** creates pending transfers with `flags.pending = True`
- **AccountService:** dual-write pattern (TB first, then PG)

```python
# src/cbs/service/transfer_service.py
class TransferService:
    def __init__(self, tb_transfer_repo, tb_account_repo, account_meta_repo,
                 system_account_repo, metadata_writer, logger):
        ...

    async def execute(self, req: TransferRequest) -> TransferResponse:
        # 1. Resolve accounts from TB
        accounts = await self.tb_account_repo.lookup([req.debit_account_id, req.credit_account_id])

        # 2. Validate accounts active, currency matches ledger
        ...

        # 3. Build TB transfer
        transfer_id = generate_uuidv7_bytes()
        tb_transfer = {
            "id": transfer_id,
            "debit_account_id": accounts[0]["id"],
            "credit_account_id": accounts[1]["id"],
            "amount": req.amount,
            "ledger": accounts[0]["ledger"],
            "code": TRANSFER_TYPE_TO_CODE[req.transfer_type],
            "user_data_128": correlation_id,
            "user_data_64": value_date_to_nanos(req.value_date),
        }

        # 4. Execute in TigerBeetle
        results = await self.tb_transfer_repo.create([tb_transfer])

        # 5. Write PG metadata (async, fire-and-forget with error logging)
        await self.metadata_writer.write(transfer_id, req)

        return TransferResponse(...)
```

#### 4.3.4 Middleware

**Idempotency Middleware:**
```python
# src/cbs/api/middleware/idempotency.py
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        idem_key = request.headers.get("Idempotency-Key")
        if not idem_key:
            return await call_next(request)

        # Check PG for existing result
        existing = await self.store.get(idem_key)
        if existing:
            return JSONResponse(existing.response, status_code=existing.status_code)

        # Reserve key (pending)
        await self.store.reserve(idem_key)

        # Execute
        response = await call_next(request)

        # Store result
        if response.status_code < 500:
            body = await response.body()
            await self.store.complete(idem_key, response.status_code, body)

        return response
```

**Request ID Middleware:**
Generates UUIDv7 per request, sets `X-Request-ID` header, propagates to structlog context.

**Error Handler:**
Catches domain exceptions (`InsufficientBalanceError`, `AccountNotFoundError`, etc.)
and maps them to the standard error envelope.

#### 4.3.5 Standard Envelope

```python
# src/cbs/api/responses.py
def success_response(data: dict, request_id: str) -> dict:
    return {
        "status": "success",
        "data": data,
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

def error_response(code: str, message: str, request_id: str, details: dict | None = None) -> dict:
    return {
        "status": "error",
        "error": {"code": code, "message": message, "details": details},
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
```

#### 4.3.6 Full API Endpoint Table

| Method | Path | Python Route Handler |
|--------|------|---------------------|
| `POST` | `/api/v1/customers` | `routes/customers.py::register` |
| `GET` | `/api/v1/customers/{ref}` | `routes/customers.py::get` |
| `POST` | `/api/v1/accounts` | `routes/accounts.py::create_account` |
| `GET` | `/api/v1/accounts/{id}` | `routes/accounts.py::get_account` |
| `GET` | `/api/v1/accounts` | `routes/accounts.py::list_accounts` |
| `PATCH` | `/api/v1/accounts/{id}/close` | `routes/accounts.py::close_account` |
| `GET` | `/api/v1/accounts/{id}/balance` | `routes/balances.py::get_balance` |
| `GET` | `/api/v1/accounts/{id}/transactions` | `routes/accounts.py::transactions` |
| `GET` | `/api/v1/accounts/{id}/statement` | `routes/statements.py::statement` |
| `POST` | `/api/v1/transfers` | `routes/transfers.py::execute` |
| `GET` | `/api/v1/transfers/{id}` | `routes/transfers.py::get` |
| `POST` | `/api/v1/transfers/fx` | `routes/fx.py::exchange` |
| `POST` | `/api/v1/transfers/hold` | `routes/holds.py::create_hold` |
| `POST` | `/api/v1/transfers/hold/{id}/capture` | `routes/holds.py::capture` |
| `POST` | `/api/v1/transfers/hold/{id}/void` | `routes/holds.py::void` |
| `POST` | `/api/v1/fees/charge` | `routes/fees.py::charge` |
| `POST` | `/api/v1/loans/{loan_id}/disburse` | `routes/loans.py::disburse` |
| `POST` | `/api/v1/loans/{loan_id}/repay` | `routes/loans.py::repay` |
| `POST` | `/api/v1/loans/{loan_id}/repay_with_fee` | `routes/loans.py::repay_with_fee` |
| `GET` | `/health/live` | `routes/health.py::live` |
| `GET` | `/health/ready` | `routes/health.py::ready` |

---

### Phase 4: Batch Operations

**Goal:** Interest accrual, capitalisation, fee collection, and arrears check batch jobs.

#### 4.4.1 Batch Runner

```python
# src/cbs/cli/batch.py
import typer
from datetime import date

batch_app = typer.Typer()

@batch_app.command("run")
def run_job(
    job: str = typer.Argument(..., help="Job name"),
    business_date: str = typer.Option(None, help="YYYY-MM-DD (default: yesterday)"),
    dry_run: bool = typer.Option(False, help="Compute but don't write"),
):
    """Run a batch job."""
    ...

@batch_app.command("list")
def list_jobs():
    """List available batch jobs."""
    typer.echo("interest-accrual\ninterest-capitalise\nfee-collect\narrears-check")

@batch_app.command("status")
def batch_status(business_date: str = typer.Option(None)):
    """Show batch run status."""
    ...
```

#### 4.4.2 Interest Accrual Job

Ported directly from Go. Three-phase pipeline:

1. **Fetch:** Query all interest-bearing accounts from PG, then batch-lookup their TB balances
2. **Compute:** Calculate daily interest in-memory (parallelised with `asyncio.gather` or `concurrent.futures`)
3. **Write:** Batch `create_transfers` to TB (8191 per call), async write audit to PG

```python
# src/cbs/service/batch/interest_accrual.py
class InterestAccrualJob:
    async def run(self, business_date: date, dry_run: bool = False) -> None:
        # 1. Record batch start
        run_id = await self.batch_repo.start("interest-accrual", business_date)

        # 2. Fetch accounts with interest rates from PG
        accounts = await self.account_repo.list_active_with_interest()

        # 3. Batch-lookup TB balances
        tb_accounts = await self.tb_account_repo.lookup_batch(
            [a.tb_account_id for a in accounts]
        )

        # 4. Compute accruals in parallel
        accruals = await asyncio.gather(*[
            self._compute_one(acct, tb_acct, business_date)
            for acct, tb_acct in zip(accounts, tb_accounts)
        ])

        if dry_run:
            self._print_dry_run(accruals)
            return

        # 5. Write to TB in batches of 8191
        BATCH_SIZE = 8191
        for chunk in chunked(accruals, BATCH_SIZE):
            transfers = [self._build_transfer(a, business_date) for a in chunk if a.amount > 0]
            await self.tb_transfer_repo.create(transfers)

        # 6. Write audit to PG
        await self.audit_repo.write_accrual_log(accruals, business_date)

        # 7. Complete batch
        await self.batch_repo.complete(run_id, len(accruals), len(transfers))
```

#### 4.4.3 Other Batch Jobs

| Job | Logic |
|-----|-------|
| `interest-capitalise` | Monthly: move accrued interest to customer accounts |
| `fee-collect` | Monthly/daily: charge scheduled fees |
| `arrears-check` | Daily: detect missed loan payments, update arrears status |

Each follows the same pattern: read PG config → read TB balances → compute → write TB → audit PG.

---

## 5. Key Design Decisions

### 5.1 Sync vs Async TigerBeetle Client

The official `tigerbeetle-python` client is synchronous. Two strategies:

**Strategy A (Recommended):** Wrap sync TB calls in `asyncio.to_thread()`.
This offloads blocking I/O to a thread pool without blocking the event loop.
FastAPI handles async routes natively; TB calls run in worker threads.

```python
async def create_transfers(self, transfers):
    return await asyncio.to_thread(self._client.create_transfers, transfers)
```

**Strategy B:** Use sync FastAPI route handlers for TB-heavy endpoints.
FastAPI runs sync handlers in a threadpool automatically. Simpler but less composable.

### 5.2 SQLAlchemy Core vs ORM

Use **SQLAlchemy Core** (not ORM) for queries. This mirrors the Go approach of
raw SQL with type safety. SQLAlchemy Core provides:
- Type-safe query building
- Async support via `asyncpg`
- No ORM overhead or session management complexity

### 5.3 Dependency Injection

Use FastAPI's `Depends()` system with a service registry stored on `app.state`.
No DI framework needed — mirrors Go's constructor injection pattern.

### 5.4 Idempotency

Implemented as FastAPI middleware (same as Go version). Middleware:
1. Reads `Idempotency-Key` header
2. Checks PG for cached result
3. Reserves key (INSERT with `pending` status)
4. On success: stores response body, updates status to `completed`
5. On failure: updates status to `failed`

### 5.5 Dual-Write Strategy

Identical to Go version:
1. Write to TigerBeetle first (source of truth)
2. Write metadata to PostgreSQL second
3. On PG failure: log error, alert, reconciliation worker fixes later
4. Reconciliation worker scans for pending idempotency keys > 24h old

### 5.6 Error Envelope

Same JSON structure as Go version:
```json
{
  "status": "error",
  "error": { "code": "INSUFFICIENT_BALANCE", "message": "...", "details": {...} },
  "request_id": "0191...",
  "timestamp": "2026-05-23T10:00:00Z"
}
```

Domain exceptions map to HTTP status codes:
- `ValidationError` → 400
- `NotFoundError` → 404
- `InsufficientBalanceError` → 409
- `AccountClosedError` → 409
- `IdempotencyConflictError` → 409

### 5.7 UUIDv7 Strategy

The Go system uses `google/uuid` to generate UUIDv7 IDs for every account, transfer,
idempotency key, and request ID. These are 128-bit time-sortable identifiers (RFC 9562)
that map directly into TigerBeetle's `[16]byte` ID fields.

#### Python 3.14+ Stdlib Support

Python 3.14 added native `uuid.uuid7()` to the standard library (per RFC 9562, §5.7):

```python
>>> import uuid
>>> u = uuid.uuid7()
UUID('01960f3d-2a1f-7a3b-8c9e-123456789abc')
>>> u.time  # creation timestamp in milliseconds (Unix epoch)
1743936859822
```

Since this project targets **Python 3.14+**, we use the stdlib directly.
**No third-party UUID dependency is needed.**

#### Helper Module

All UUIDv7 usage goes through a thin helper module that provides
TigerBeetle byte-conversion helpers alongside the stdlib `uuid7()`:

```python
# src/cbs/util/uuid.py
"""UUIDv7 generation and TigerBeetle ID conversion (stdlib only, Python 3.14+)."""
from __future__ import annotations

from uuid import UUID, uuid7


def generate_uuidv7() -> UUID:
    """Generate a new UUIDv7 (time-sortable, globally unique)."""
    return uuid7()


def uuidv7_bytes() -> bytes:
    """Generate a UUIDv7 and return its 16-byte representation (for TigerBeetle)."""
    return uuid7().bytes


def uuidv7_to_tb_id(u: UUID) -> bytes:
    """Convert a UUID to TigerBeetle's 16-byte ID format."""
    return u.bytes


def tb_id_to_uuid(raw: bytes) -> UUID:
    """Convert a TigerBeetle 16-byte ID back to a UUID."""
    return UUID(bytes=raw)


def uuidv7_str() -> str:
    """Generate a UUIDv7 and return its string representation."""
    return str(uuid7())
```

#### Usage Throughout the Codebase

```python
# In services — generate IDs for TB accounts/transfers:
from cbs.util.uuid import generate_uuidv7, uuidv7_bytes

account_id = generate_uuidv7()           # UUID object
tb_id = uuidv7_bytes()                   # 16 bytes for TigerBeetle

# In middleware — request IDs:
from cbs.util.uuid import uuidv7_str

request_id = uuidv7_str()                # "0191a2b3-c4d5-7e6f-8a9b-0c1d2e3f4a5d"

# In repos — convert TB IDs back to UUIDs for API responses:
from cbs.util.uuid import tb_id_to_uuid

account_uuid = tb_id_to_uuid(tb_account["id"])
```

#### Go ↔ Python ID Equivalence

| Operation | Go (`google/uuid`) | Python 3.14+ (`uuid` stdlib) |
|-----------|--------------------|--------------------|
| Generate UUIDv7 | `uuid.Must(uuid.NewV7())` | `uuid.uuid7()` |
| Convert to 16 bytes (for TB) | `copy(tbID[:], id[:])` | `u.bytes` |
| Convert from 16 bytes (from TB) | `uuid.UUID(raw)` | `uuid.UUID(bytes=raw)` |
| String representation | `id.String()` | `str(u)` |
| Extract timestamp | `id.Time()` (v1 semantics) | `u.time` (ms since epoch) |

---

## 6. Testing Strategy

### 6.1 Test Pyramid

```
          ┌─────────────────────┐
          │   E2E Scenarios     │  ~10 tests  (full banking flows)
          ├─────────────────────┤
          │  Integration Tests  │  ~30 tests  (API + real DB)
          ├─────────────────────┤
          │    Unit Tests       │  ~100 tests (services, domain, utils)
          └─────────────────────┘
```

### 6.2 Unit Tests

- **Domain:** balance computation, currency mapping, transfer code mapping, validation
- **Services:** mock TB and PG stores, verify orchestration logic
- **Utilities:** UUIDv7 generation, amount conversion, flag construction

### 6.3 Integration Tests

- Use `pytest-asyncio` with a real TigerBeetle + PostgreSQL (Docker Compose test env)
- Test each API endpoint with the FastAPI `TestClient`
- Verify dual-write consistency

### 6.4 E2E Scenarios

Full banking flows that exercise multiple endpoints:
1. Customer registration → open current account → deposit → withdraw → check balance
2. Loan disbursement → repayment with interest → closure
3. FX conversion between USD and EUR accounts
4. Two-phase hold → capture/void
5. Interest accrual batch → capitalisation

### 6.5 Test Commands

```bash
make test              # All tests
make test-unit         # Unit tests only
make test-integration  # Integration tests (requires Docker)
make test-e2e          # E2E scenarios
```

---

## 7. Development Workflow

### 7.1 Makefile Targets

```makefile
.PHONY: dev down reset logs setup status migrate test lint build

dev:              ## Start full stack (TB + PG + API)
	docker compose up -d --build

down:             ## Stop containers, keep data
	docker compose down

reset:            ## Stop and DELETE all data
	docker compose down -v

logs:             ## Follow API logs
	docker compose logs -f cbs-api

setup:            ## Bootstrap bank
	docker compose run --rm cbs-api cbs setup init --currency USD --currency EUR

status:           ## Check setup status
	docker compose run --rm cbs-api cbs setup status

migrate:          ## Run pending migrations
	docker compose run --rm cbs-api cbs migrate up

test:             ## Run all tests
	pytest

test-unit:        ## Unit tests
	pytest tests/unit

test-integration: ## Integration tests
	pytest tests/integration

lint:             ## Run linter
	ruff check src/ tests/
	ruff format --check src/ tests/

typecheck:        ## Run type checker
	mypy src/

build:            ## Build wheel
	python -m build

db-only:          ## Start only databases
	docker compose up -d tigerbeetle postgres
```

### 7.2 Bare-Metal Development

```bash
# Start only databases
make db-only

# Set environment
export CBS_TB_ADDRESSES=localhost:3001
export CBS_PG_DSN=postgres://cbs:cbs_dev@localhost:5432/corebanking?sslmode=disable
export CBS_PORT=8080

# Run migrations
cbs migrate up

# Run API with hot-reload
uvicorn cbs.main:app --reload --port 8080
```

---

## 8. Implementation Order (Recommended)

| Step | Module | Depends On | Description |
|------|--------|------------|-------------|
| 1 | `pyproject.toml` | — | Project setup, dependencies |
| 2 | `config.py` | 1 | Environment configuration |
| 3 | `domain/` | 1 | All domain models, constants, errors |
| 4 | `util/` | 1 | UUID, amount, TB type helpers |
| 5 | `store/tigerbeetle/` | 3, 4 | TB client wrapper and repos |
| 6 | `store/postgres/` | 3, 4 | PG database, all repos |
| 7 | `alembic/` | 6 | All migration scripts |
| 8 | `cache/` | 6 | In-memory caches |
| 9 | `service/account_service.py` | 5, 6, 8 | Account creation, listing, closure |
| 10 | `service/transfer_service.py` | 5, 6, 8 | Transfer execution |
| 11 | `service/fx_service.py` | 5, 6, 8 | FX transfers |
| 12 | `service/hold_service.py` | 5, 6 | Two-phase holds |
| 13 | `service/balance_service.py` | 5, 6 | Balance queries |
| 14 | `service/loan_service.py` | 5, 6, 10 | Loan operations |
| 15 | `service/fee_service.py` | 5, 6, 10 | Fee charging |
| 16 | `service/customer_service.py` | 6 | Customer management |
| 17 | `api/middleware/` | 6 | Idempotency, request ID, logging |
| 18 | `api/routes/` | 9-17 | All HTTP route handlers |
| 19 | `main.py` | 18 | FastAPI app factory |
| 20 | `cli/` | 19 | Typer CLI (serve, setup, migrate, batch) |
| 21 | `service/batch/` | 5, 6, 10 | Batch jobs |
| 22 | `docker-compose.yml` | 19 | Docker stack |
| 23 | `Dockerfile` | 19 | Container image |
| 24 | `Makefile` | 22 | Dev targets |
| 25 | `tests/` | 19-21 | All test suites |

---

## 9. Dependency Mapping (Go → Python)

| Go Package | Python Equivalent | Notes |
|-----------|-------------------|-------|
| `github.com/go-chi/chi/v5` | `fastapi` | Router + middleware |
| `github.com/tigerbeetle/tigerbeetle-go` | `tigerbeetle` | Official Python client |
| `github.com/jackc/pgx/v5` | `asyncpg` + `sqlalchemy[asyncio]` | Async PG driver |
| `github.com/golang-migrate/migrate/v4` | `alembic` | Migration management |
| `github.com/google/uuid` | `uuid` stdlib (`uuid.uuid7()`) | UUIDv7 native in Python 3.14+ stdlib; no extra dependency |
| `github.com/spf13/cobra` | `typer` | CLI framework |
| `gopkg.in/yaml.v3` | `pyyaml` | YAML parsing |
| `log/slog` | `structlog` | Structured logging |
| Standard `net/http` | `uvicorn` + `httpx` (test) | HTTP server/testing |

---

## 10. Risk Factors and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| TigerBeetle Python client is synchronous | Blocks event loop | Wrap in `asyncio.to_thread()`; benchmark latency |
| Python GIL limits concurrency | Lower throughput than Go | Use `asyncio` for I/O-bound work; use multiprocessing for CPU-bound batch jobs |
| Python startup time slower than Go binary | Slower cold start | Use `uvicorn --workers` for multiple processes; pre-warm caches |
| `tigerbeetle-python` may lag behind Go client version | Missing features | Pin to tested version; contribute upstream if needed |
| 128-bit integer handling | TigerBeetle uses `Uint128` natively | Use Python's arbitrary-precision `int`; convert to/from bytes carefully |
| Async PG driver differences | Query behaviour differs from `pgx` | Test all queries; use SQLAlchemy Core for abstraction |

---

## 11. Performance Considerations

Python will have lower throughput than Go for CPU-bound work. Mitigations:

1. **I/O is the bottleneck**, not CPU — the system is I/O-bound (TB + PG calls)
2. **asyncio** handles concurrent I/O efficiently — similar to Go goroutines for I/O
3. **Batch TB calls** (8191 per call) reduce round-trips — same optimisation as Go
4. **In-memory caches** avoid PG reads on hot path — same as Go
5. **`uvicorn --workers N`** enables multi-process scaling

For the batch jobs (CPU-heavy interest computation):
- Use `concurrent.futures.ProcessPoolExecutor` for parallel computation
- TB writes remain batched and I/O-bound

---

## 12. CI/CD Pipeline

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install ruff mypy
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/
      - run: mypy src/

  test:
    runs-on: ubuntu-latest
    services:
      tigerbeetle:
        image: ghcr.io/tigerbeetle/tigerbeetle:latest
        ports: ["3001:3001"]
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: corebanking
          POSTGRES_USER: cbs
          POSTGRES_PASSWORD: cbs_dev
        ports: ["5432:5432"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install -e ".[dev]"
      - run: cbs migrate up
      - run: pytest --cov=cbs --cov-report=xml
```

---

## 13. File-by-File Migration Map

This table maps every Go source file to its Python equivalent.

### Entrypoint
| Go File | Python File |
|---------|-------------|
| `cmd/cbs-api/main.go` | `src/cbs/main.py` + `src/cbs/cli/app.py` |

### CLI
| Go File | Python File |
|---------|-------------|
| `internal/cli/root.go` | `src/cbs/cli/app.py` |
| `internal/cli/serve.go` | `src/cbs/cli/serve.py` |
| `internal/cli/setup.go` | `src/cbs/cli/setup.py` |
| `internal/cli/migrate.go` | `src/cbs/cli/migrate.py` |
| `internal/cli/batch.go` | `src/cbs/cli/batch.py` |
| `internal/cli/version.go` | `src/cbs/cli/app.py` (version command) |
| `internal/cli/status_print.go` | `src/cbs/cli/status.py` |

### Domain
| Go File | Python File |
|---------|-------------|
| `internal/domain/account.go` | `src/cbs/domain/accounts.py` |
| `internal/domain/transfer.go` | `src/cbs/domain/transfers.py` |
| `internal/domain/currency.go` | `src/cbs/domain/currency.py` |
| `internal/domain/product.go` | `src/cbs/domain/products.py` |
| `internal/domain/settlement.go` | `src/cbs/domain/settlements.py` |
| `internal/domain/errors.go` | `src/cbs/domain/errors.py` |

### API Layer
| Go File | Python File |
|---------|-------------|
| `internal/api/router.go` | `src/cbs/api/router.py` |
| `internal/api/handler/health_handler.go` | `src/cbs/api/routes/health.py` |
| `internal/api/handler/customer_handler.go` | `src/cbs/api/routes/customers.py` |
| `internal/api/handler/account_handler.go` | `src/cbs/api/routes/accounts.py` |
| `internal/api/handler/transfer_handler.go` | `src/cbs/api/routes/transfers.py` + `holds.py` |
| `internal/api/handler/fx_handler.go` | `src/cbs/api/routes/fx.py` |
| `internal/api/handler/balance_handler.go` | `src/cbs/api/routes/balances.py` |
| `internal/api/handler/loan_handler.go` | `src/cbs/api/routes/loans.py` |
| `internal/api/middleware/requestid.go` | `src/cbs/api/middleware/request_id.py` |
| `internal/api/middleware/idempotency.go` | `src/cbs/api/middleware/idempotency.py` |
| `internal/api/middleware/logging.go` | `src/cbs/api/middleware/logging.py` |
| `internal/api/middleware/recovery.go` | `src/cbs/api/middleware/error_handler.py` |
| `internal/api/middleware/cors.go` | FastAPI built-in `CORSMiddleware` |

### Service Layer
| Go File | Python File |
|---------|-------------|
| `internal/service/account_service.go` | `src/cbs/service/account_service.py` |
| `internal/service/transfer_service.go` | `src/cbs/service/transfer_service.py` |
| `internal/service/fx_service.go` | `src/cbs/service/fx_service.py` |
| `internal/service/hold_service.go` | `src/cbs/service/hold_service.py` |
| `internal/service/balance_service.go` | `src/cbs/service/balance_service.py` |
| `internal/service/loan_service.go` | `src/cbs/service/loan_service.py` |
| `internal/service/settlement_service.go` | `src/cbs/service/settlement_service.py` |
| `internal/service/batch_runner.go` | `src/cbs/service/batch/runner.py` |
| `internal/service/interest_accrual.go` | `src/cbs/service/batch/interest_accrual.py` |
| `internal/service/interest_capitalise.go` | `src/cbs/service/batch/interest_capitalise.py` |
| `internal/service/fee_collection.go` | `src/cbs/service/batch/fee_collection.py` |
| `internal/service/arrears_check.go` | `src/cbs/service/batch/arrears_check.py` |

### Store Layer
| Go File | Python File |
|---------|-------------|
| `internal/store/tigerbeetle/client.go` | `src/cbs/store/tigerbeetle/client.py` |
| `internal/store/tigerbeetle/account_repo.go` | `src/cbs/store/tigerbeetle/account_repo.py` |
| `internal/store/tigerbeetle/transfer_repo.go` | `src/cbs/store/tigerbeetle/transfer_repo.py` |
| `internal/store/postgres/db.go` | `src/cbs/store/postgres/database.py` |
| `internal/store/postgres/account_meta_repo.go` | `src/cbs/store/postgres/account_repo.py` |
| `internal/store/postgres/customer_repo.go` | `src/cbs/store/postgres/customer_repo.py` |
| `internal/store/postgres/product_repo.go` | `src/cbs/store/postgres/product_repo.py` |
| `internal/store/postgres/fx_rate_repo.go` | `src/cbs/store/postgres/fx_rate_repo.py` |
| `internal/store/postgres/idempotency_repo.go` | `src/cbs/store/postgres/idempotency_repo.py` |
| `internal/store/postgres/system_account.go` | `src/cbs/store/postgres/system_account_repo.py` |
| `internal/store/postgres/loan_repo.go` | `src/cbs/store/postgres/loan_repo.py` |
| `internal/store/postgres/batch_run_repo.go` | `src/cbs/store/postgres/batch_repo.py` |
| `internal/store/postgres/settlement_repo.go` | `src/cbs/store/postgres/settlement_repo.py` |
| `internal/store/postgres/audit_repo.go` | `src/cbs/store/postgres/audit_repo.py` |
| `internal/store/postgres/fee_collection_repo.go` | `src/cbs/store/postgres/fee_repo.py` |

### Cache Layer
| Go File | Python File |
|---------|-------------|
| `internal/cache/fx_cache.go` | `src/cbs/cache/fx_cache.py` |
| `internal/cache/product_cache.go` | `src/cbs/cache/product_cache.py` |
| `internal/cache/ledger_cache.go` | `src/cbs/cache/ledger_cache.py` |

### Utilities
| Go File | Python File |
|---------|-------------|
| `pkg/tigerbeetleutil/id.go` | `src/cbs/util/uuid.py` |
| `pkg/tigerbeetleutil/amount.go` | `src/cbs/util/amount.py` |
| `pkg/tigerbeetleutil/uuid.go` | `src/cbs/util/tb_types.py` |
| `pkg/httputil/respond.go` | `src/cbs/api/responses.py` |
| `pkg/httputil/decode.go` | FastAPI built-in (Pydantic) |
| `pkg/types/money.go` | `src/cbs/domain/accounts.py` (Balance type) |

### Config
| Go File | Python File |
|---------|-------------|
| `internal/config/config.go` | `src/cbs/config.py` |

### Infrastructure
| Go File | Python File |
|---------|-------------|
| `Dockerfile` | `Dockerfile` (Python multi-stage) |
| `docker-compose.yml` | `docker-compose.yml` (identical services) |
| `Makefile` | `Makefile` (Python commands) |
| `go.mod`, `go.sum` | `pyproject.toml` |
| `migrations/*.sql` | `alembic/versions/*.py` |
| `products.example.yaml` | `products.example.yaml` (identical) |
| `.env.example` | `.env.example` |

---

## 14. Summary

This port preserves the **architecture, domain model, and all business logic** of the
original Go system while leveraging Python's modern async capabilities, type system,
and ecosystem. The key trade-off is lower raw throughput vs. faster development velocity
and a broader talent pool.

The implementation is divided into 4 phases:
1. **Foundation** — project skeleton, Docker, migrations, DB clients
2. **Domain + CLI** — constants, models, `cbs setup` bootstrap
3. **Core API** — all HTTP endpoints, service layer, middleware
4. **Batch** — interest accrual, capitalisation, fee collection

Each phase produces a working, testable increment.
