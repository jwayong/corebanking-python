"""Shared pytest fixtures for PostgreSQL repository unit tests.

All repos use SQLAlchemy async sessions backed by a real database.
Since we cannot connect to PostgreSQL in unit tests, every fixture
returns carefully configured ``unittest.mock.AsyncMock`` / ``MagicMock``
objects that stand in for a real session and Database.

Helper functions live in :mod:`fixtures` — import from there, not here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Re-export helpers so existing imports still work
from tests.unit.store.postgres.fixtures import (  # noqa: F401
    make_mock_result,
    make_mock_row,
    make_pg_integrity_error,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_session():
    """Return an AsyncMock configured as a SQLAlchemy async session.

    The returned mock supports:
    - ``await session.execute(text(...), params)`` → a MagicMock result
      (use ``make_mock_result()`` to configure return values)
    - ``async with session.begin():`` → async context manager that does not
      suppress exceptions (``__aexit__`` returns ``False``)

    To configure multiple sequential ``execute()`` calls, set
    ``session.execute.side_effect = [result1, result2, ...]``.

    The default ``execute()`` return value is a no-op result
    (fetchone→None, fetchall→[], scalar→None, rowcount=0).
    """
    session = AsyncMock()

    # --- async context manager for session.begin() -----------------------
    # Must use MagicMock (not AsyncMock) so begin() returns the context
    # manager directly, not a coroutine.  The context manager's __aenter__
    # and __aexit__ are AsyncMocks so they can be awaited.
    ctx_manager = MagicMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=None)
    ctx_manager.__aexit__ = AsyncMock(return_value=False)  # don't suppress exceptions
    session.begin = MagicMock(return_value=ctx_manager)

    # --- default execute() result ----------------------------------------
    from tests.unit.store.postgres.fixtures import make_mock_result

    default_result = make_mock_result()
    session.execute.return_value = default_result

    return session


@pytest.fixture
def mock_db(mock_session):
    """Return a MagicMock Database whose ``.session()`` yields *mock_session*.

    Use this fixture when instantiating class-based repos (AccountRepo,
    CustomerRepo, etc.) that accept a ``Database`` in their constructor.
    """
    db = MagicMock()
    db.session.return_value = mock_session
    return db
