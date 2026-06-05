# Issue 04: PostgreSQL Connection Pool

**Phase:** 1 - Foundation
**Priority:** High
**Labels:** `phase-1`, `foundation`
**Depends on:** #01 (Project Bootstrap)

## Summary

Create the PostgreSQL database module with async connection pool using
SQLAlchemy 2.0 async engine + asyncpg driver, and a session factory.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/store/postgres/__init__.py` | PG store package |
| `src/cbs/store/postgres/database.py` | Database class with async engine and session factory |

## Detailed Spec

```python
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

**Key decision:** Use SQLAlchemy Core (not ORM) for queries — see PLAN.md section 5.2.

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/store/postgres/db.go` | Go PG connection pool |

## Acceptance Criteria

- [ ] `Database` connects to PostgreSQL using the provided DSN
- [ ] Connection pool is correctly sized
- [ ] `session()` returns a working async session
- [ ] `close()` cleanly disposes the pool
