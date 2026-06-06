"""Shared helpers for PostgreSQL repository unit tests.

These utilities build mock objects that stand in for SQLAlchemy result
proxies and PostgreSQL errors.  Import from this module (not conftest)
in every test file.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def make_mock_result(
    fetchone_val=None,
    fetchall_val=None,
    scalar_val=None,
    rowcount=0,
):
    """Build a MagicMock that behaves like a SQLAlchemy ResultProxy.

    Parameters
    ----------
    fetchone_val: value returned by ``.fetchone()`` (default None)
    fetchall_val:  list returned by ``.fetchall()`` (default [])
    scalar_val:    value returned by ``.scalar()`` (default None)
    rowcount:      int for affected-row count (default 0)
    """
    result = MagicMock()
    result.fetchone.return_value = fetchone_val
    result.fetchall.return_value = fetchall_val if fetchall_val is not None else []
    result.scalar.return_value = scalar_val
    result.rowcount = rowcount
    return result


def make_mock_row(**kwargs):
    """Build a MagicMock whose *named* attributes are set from *kwargs*.

    Use this when the repo under test accesses row columns by name
    (e.g. ``row.id``, ``row.code``) rather than positional indexing.
    """
    row = MagicMock()
    for key, value in kwargs.items():
        setattr(row, key, value)
    return row


def make_pg_integrity_error(pgcode="23505"):
    """Return a SQLAlchemy IntegrityError wrapping a mock PG error.

    The mock error has ``pgcode`` set so that ``_is_unique_violation()``
    and the idempotency repo's 23505 check both fire correctly.
    """
    from sqlalchemy.exc import IntegrityError

    pg_err = MagicMock()
    pg_err.pgcode = pgcode
    return IntegrityError("statement", {}, pg_err)
