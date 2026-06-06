import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

log = structlog.get_logger()

class Database:
    def __init__(self, dsn: str, max_size: int = 10):
        # Ensure DSN uses the correct asyncpg prefix
        if dsn.startswith("postgres://"):
            dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
        elif not dsn.startswith("postgresql+asyncpg://"):
            # Fallback if it's just postgresql:// without the driver specified
            dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

        # Guard against invalid pool size
        pool_size = max_size if max_size > 0 else 5

        self._engine = create_async_engine(
            dsn,
            pool_size=pool_size,
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        log.debug("postgres_pool_created", pool_size=pool_size)

    @classmethod
    async def create(cls, dsn: str, max_size: int = 10) -> "Database":
        """Factory method that creates the pool and verifies connectivity."""
        db = cls(dsn, max_size)
        await db.ping()
        return db

    def session(self):
        """Return a new async session."""
        return self._session_factory()

    async def ping(self) -> None:
        """Verify database connectivity."""
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as e:
            log.error("postgres_ping_failed", error=str(e))
            raise

    async def close(self):
        """Dispose of the engine and connection pool."""
        log.debug("postgres_pool_closing")
        await self._engine.dispose()
