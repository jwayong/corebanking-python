# Issue 15: Litestar App Factory and Wiring

**Phase:** 3 - Core API
**Priority:** High
**Labels:** `phase-3`, `app`
**Depends on:** #14 (API Routes)

## Summary

Create the Litestar application factory that wires together all services,
middleware, routes, and lifecycle hooks (startup/shutdown).

## Files to Create/Update

| File | Description |
|------|-------------|
| `src/cbs/main.py` | Litestar app factory, `on_startup`, `on_shutdown`, app instance |

## Detailed Spec

```python
from litestar import Litestar
from cbs.config import CBSConfig
from cbs.store.tigerbeetle.client import TBClient
from cbs.store.postgres.database import Database
from cbs.api.router import route_handlers
from cbs.service import build_services

async def on_startup(app: Litestar) -> None:
    config = CBSConfig()
    tb = TBClient(config.tb_addresses.split(","))
    db = Database(config.pg_dsn, config.pg_pool_max)
    app.state.tb = tb
    app.state.db = db
    app.state.services = build_services(tb, db, config)

async def on_shutdown(app: Litestar) -> None:
    await app.state.db.close()

app = Litestar(
    route_handlers=route_handlers,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)
```

### What Gets Wired

1. **Config:** Load from environment via `CBSConfig()`
2. **TB Client:** Connect to TigerBeetle cluster
3. **PG Database:** Create async connection pool
4. **Services:** `build_services()` creates all repos, caches, and services
5. **Routes:** All route handlers collected from `api/router.py`
6. **Middleware:** Idempotency, request ID, logging, error handling, CORS
7. **Lifecycle:** Startup opens connections, shutdown closes PG pool

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/cmd/cbs-api/main.go` | Go entrypoint |
| `../corebanking/internal/api/router.go` | Router + middleware setup |

## Acceptance Criteria

- [ ] `uvicorn cbs.main:app` starts the server
- [ ] Startup connects to TB and PG successfully
- [ ] All routes are accessible at correct paths
- [ ] Middleware is applied to all routes
- [ ] Shutdown cleanly closes database connections
- [ ] `GET /health/ready` returns 200 when both databases are up
