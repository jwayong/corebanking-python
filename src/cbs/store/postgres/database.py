from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

class Database:
    def __init__(self, dsn: str, max_size: int = 10):
        # Ensure DSN uses the correct asyncpg prefix
        if dsn.startswith("postgres://"):
            dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
        elif not dsn.startswith("postgresql+asyncpg://"):
            # Fallback if it's just postgresql:// without the driver specified
            dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

        self._engine = create_async_engine(
            dsn,
            pool_size=max_size,
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    def session(self):
        """Return a new async session."""
        return self._session_factory()

    async def close(self):
        """Dispose of the engine and connection pool."""
        await self._engine.dispose()
