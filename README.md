# Core Banking System (Python)

A high-performance Python port of the Go-based core banking system, preserving identical architecture and domain models.

## 🚀 Overview

This project is a functional migration of the [original Go core banking system](https://github.com/jwayong/corebanking) to Python 3.14+. It implements a stateless service design with a dual-database strategy for financial consistency and metadata management.

### Architecture
- **Financial State (OLTP):** [TigerBeetle](https://tigerbeetle.io/) for high-performance, immutable ledger entries (balances, transfers).
- **Metadata (OLGP):** [PostgreSQL](https://www.postgresql.org/) for accounts, products, audit logs, and idempotency tracking.
- **Consistency:** Dual-write strategy with TigerBeetle as the source of truth.

## 🛠 Tech Stack

| Component | Technology | Rationale |
| :--- | :--- | :--- |
| **Language** | Python 3.14+ | Native `uuid.uuid7()`, match/case, generics |
| **Web Framework** | [Litestar](https://litestar.dev/) | High-performance ASGI framework with built-in DI and OpenAPI |
| **Ledger DB** | TigerBeetle | Official `tigerbeetle-python` client |
| **Metadata DB** | PostgreSQL | `asyncpg` + `SQLAlchemy 2.0` (Core) |
| **Migrations** | [Alembic](https://alembic.sqlalchemy.org/) | Standard migration tool for SQLAlchemy |
| **CLI** | [Typer](https://typer.tiangolo.com/) | Type-hinted CLI framework |
| **Serialization** | [msgspec](https://github.com/s-v-e/msgspec) | Ultra-fast JSON serialization (Litestar default) |
| **Logging** | [structlog](https://www.structlog.org/) | Structured JSON logging |

## 🏁 Getting Started

### Prerequisites
- Python 3.14+
- Docker & Docker Compose

### Installation & Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/jwayong/corebanking-python.git
   cd corebanking-python
   ```

2. **Start the infrastructure** (TigerBeetle + PostgreSQL + API)
   ```bash
   make dev
   ```

3. **Bootstrap the bank** (Run migrations and seed system accounts/products)
   ```bash
   make setup
   ```

## 🛠 Development Workflow

### Common Commands
| Command | Description |
| :--- | :--- |
| `make dev` | Start the full stack in Docker |
| `make migrate` | Run pending database migrations |
| `make test` | Run all tests (unit, integration, e2e) |
| `make lint` | Run Ruff linter and formatter |
| `make typecheck` | Run Mypy static type checker |
| `make reset` | Stop containers and delete all data |

### Testing
The project uses a test pyramid:
- **Unit Tests:** Domain logic, utilities, and service orchestration.
- **Integration Tests:** API endpoints tested against real databases.
- **E2E Scenarios:** Full banking flows (e.g., registration $\rightarrow$ deposit $\rightarrow$ transfer).

## 🗺 Migration Plan

The migration is structured into four phases:
1. **Foundation:** Infrastructure, Skeleton, and Migrations.
2. **Domain Model & CLI:** Core banking constants and bootstrap tools.
3. **Core API:** Service layer, Route handlers, and Middleware.
4. **Batch Operations:** Interest accrual, Capitalisation, and Fee collection.

Detailed specifications for each phase can be found in [PLAN.md](./PLAN.md) and the [PLAN/](./PLAN/) directory. Progress is tracked via [GitHub Issues](https://github.com/jwayong/corebanking-python/issues).

## 🔗 References
- **Original Go Implementation:** [github.com/jwayong/corebanking](https://github.com/jwayong/corebanking)
- **TigerBeetle Documentation:** [docs.tigerbeetle.io](https://docs.tigerbeetle.io)
