"""PostgreSQL repository for idempotency key storage.

Mirrors corebanking/internal/store/postgres/idempotency_repo.go
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import sqlalchemy.exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.errors import ErrIdempotencyKeyExists

log = structlog.get_logger()


@dataclass
class IdempotencyKey:
    """Record from the idempotency_keys table."""

    id: int = 0
    key: str = ""
    status: str = ""
    response_code: Optional[int] = None
    response_body: Optional[bytes] = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


class IdempotencyRepo:
    """Handles idempotency key storage and lifecycle."""

    # Table columns for SELECT *
    _COLUMNS = "id, idempotency_key, status, response_code, response_body, created_at, completed_at"

    @staticmethod
    async def get(session: AsyncSession, key: str) -> Optional[IdempotencyKey]:
        """Retrieve an idempotency key record by key string.

        Returns None if not found.
        """
        stmt = text(
            f"SELECT {IdempotencyRepo._COLUMNS} FROM idempotency_keys WHERE idempotency_key = :key"
        )
        result = await session.execute(stmt, {"key": key})
        rec = result.fetchone()

        if rec is None:
            return None

        response_code = rec.response_code  # may be None from DB
        return IdempotencyKey(
            id=rec.id,
            key=rec.idempotency_key,
            status=rec.status,
            response_code=response_code,
            response_body=rec.response_body,
            created_at=rec.created_at,
            completed_at=rec.completed_at,
        )

    @staticmethod
    async def reserve(session: AsyncSession, key: str) -> IdempotencyKey:
        """Insert a new idempotency key with status 'pending'.

        If the key exists with status 'failed', it is reset to 'pending' (retry).
        Raises ErrIdempotencyKeyExists if the key exists with a non-failed status.
        """
        stmt = text(
            f"""INSERT INTO idempotency_keys (idempotency_key, status)
                 VALUES (:key, 'pending')
                 ON CONFLICT (idempotency_key)
                 DO UPDATE SET status = 'pending', response_code = NULL, response_body = NULL,
                    completed_at = NULL, created_at = NOW()
                 WHERE idempotency_keys.status = 'failed'
                 RETURNING {IdempotencyRepo._COLUMNS}"""
        )

        try:
            result = await session.execute(stmt, {"key": key})
        except sqlalchemy.exc.IntegrityError as e:
            # Unique violation (e.g., concurrent insert beat us)
            if getattr(e, "orig", None) and getattr(e.orig, "pgcode", None) == "23505":
                raise ErrIdempotencyKeyExists from e
            raise

        rec = result.fetchone()

        if rec is None:
            # ON CONFLICT matched but WHERE clause didn't fire —
            # key exists with a non-failed status (pending or completed).
            raise ErrIdempotencyKeyExists

        return IdempotencyKey(
            id=rec.id,
            key=rec.idempotency_key,
            status=rec.status,
            response_code=None,
            response_body=None,
            created_at=rec.created_at,
            completed_at=None,
        )

    @staticmethod
    async def complete(
        session: AsyncSession, key: str, response_code: int, response_body: bytes
    ) -> None:
        """Update an idempotency key to 'completed' with the response."""
        stmt = text(
            """UPDATE idempotency_keys
                 SET status = 'completed', response_code = :response_code,
                     response_body = :response_body, completed_at = NOW()
                 WHERE idempotency_key = :key"""
        )
        await session.execute(
            stmt,
            {
                "key": key,
                "response_code": response_code,
                "response_body": response_body,
            },
        )

    @staticmethod
    async def fail(
        session: AsyncSession, key: str, response_code: int, response_body: bytes
    ) -> None:
        """Update an idempotency key to 'failed' with the response."""
        stmt = text(
            """UPDATE idempotency_keys
                 SET status = 'failed', response_code = :response_code,
                     response_body = :response_body, completed_at = NOW()
                 WHERE idempotency_key = :key"""
        )
        await session.execute(
            stmt,
            {
                "key": key,
                "response_code": response_code,
                "response_body": response_body,
            },
        )

    @staticmethod
    async def delete_expired(session: AsyncSession, ttl_seconds: int) -> int:
        """Remove pending keys older than the given TTL.

        Returns the number of rows deleted.
        """
        stmt = text(
            "DELETE FROM idempotency_keys WHERE status = 'pending' AND created_at < NOW() - :ttl::interval"
        )
        result = await session.execute(
            stmt,
            {"ttl": f"{ttl_seconds} seconds"},
        )
        return result.rowcount
