# Issue 01: Project Bootstrap, Config, Docker, Makefile

**Phase:** 1 - Foundation
**Priority:** High (blocks all other issues)
**Labels:** `phase-1`, `foundation`

## Summary

Set up the Python project skeleton with all tooling configuration, environment config,
Docker Compose stack, Dockerfile, and Makefile.

## Files to Create

| File | Description |
|------|-------------|
| `pyproject.toml` | Project metadata, all dependencies, tool config (ruff, mypy, pytest) |
| `src/cbs/__init__.py` | Package root |
| `src/cbs/config.py` | `CBSConfig` using `pydantic-settings` with `CBS_` env prefix |
| `docker-compose.yml` | 4 services: tigerbeetle, postgres, cbs-migrate, cbs-api |
| `Dockerfile` | Multi-stage Python 3.14-slim build |
| `Makefile` | All dev targets (dev, down, reset, logs, setup, status, migrate, test, lint, build, db-only) |
| `.env.example` | Environment variable template |
| `products.example.yaml` | Product seed file (copy verbatim from `../corebanking/products.example.yaml`) |

## Detailed Spec

### pyproject.toml

```toml
[project]
name = "corebanking"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "litestar>=2.14",
    "uvicorn[standard]>=0.30",
    "msgspec>=0.18",
    "pydantic-settings>=2.5",
    "asyncpg>=0.30",
    "psycopg[binary]>=3.2",
    "sqlalchemy[asyncio]>=2.0",
    "alembic>=1.13",
    "tigerbeetle>=0.16",
    "typer>=0.12",
    "structlog>=24.4",
    "pyyaml>=6.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "mypy>=1.11",
    "respx>=0.21",
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

### config.py

```python
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

### docker-compose.yml

Same 4 services as `../corebanking/docker-compose.yml`:
- `tigerbeetle` (single replica, port 3001)
- `postgres` (PostgreSQL 16, port 5432, db=corebanking, user=cbs, pass=cbs_dev)
- `cbs-migrate` (runs alembic, exits)
- `cbs-api` (Litestar app via uvicorn, port 8080, depends on tigerbeetle + postgres)

### Dockerfile

Multi-stage: builder installs deps, runtime stage copies packages + app.
CMD: `uvicorn cbs.main:app --host 0.0.0.0 --port 8080`

### Makefile

All targets from PLAN.md section 7.1 (dev, down, reset, logs, setup, status, migrate, test, test-unit, test-integration, lint, typecheck, build, db-only).

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/go.mod` | Dependency list |
| `../corebanking/docker-compose.yml` | Service definitions |
| `../corebanking/Dockerfile` | Build stages |
| `../corebanking/Makefile` | Dev targets |
| `../corebanking/products.example.yaml` | Product seed (copy verbatim) |
| `../corebanking/.env.example` | Env vars |
| `../corebanking/internal/config/config.go` | Config structure |

## Acceptance Criteria

- [ ] `pip install -e ".[dev]"` succeeds on Python 3.14+
- [ ] `docker compose up -d` starts tigerbeetle and postgres
- [ ] `make db-only` starts only databases
- [ ] `CBSConfig()` loads from environment variables with `CBS_` prefix
- [ ] `ruff check src/` passes
- [ ] `mypy src/` passes (empty package initially)
