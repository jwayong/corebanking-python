"""Unit tests for IdempotencyRepo (PostgreSQL idempotency key storage).

Tests verify INSERT/SELECT/UPDATE SQL, conflict handling, retry-on-failed
logic, and TTL cleanup using mocked async sessions.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy.exc import IntegrityError

from cbs.domain.errors import ErrIdempotencyKeyExists
from cbs.store.postgres.idempotency_repo import IdempotencyKey, IdempotencyRepo
from tests.unit.store.postgres.fixtures import make_mock_result, make_mock_row, make_pg_integrity_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_idem_row(
    id=1, key="key-abc", status="pending", response_code=None,
    response_body=None, created_at=None, completed_at=None,
):
    if created_at is None:
        created_at = datetime.now()
    return make_mock_row(
        id=id,
        idempotency_key=key,
        status=status,
        response_code=response_code,
        response_body=response_body,
        created_at=created_at,
        completed_at=completed_at,
    )


# ---------------------------------------------------------------------------
# IdempotencyRepo.get()
# ---------------------------------------------------------------------------

class TestIdempotencyRepoGet:
    async def test_found(self, mock_session):
        now = datetime.now()
        row = _make_idem_row(
            id=5, key="key-123", status="completed", response_code=200,
            response_body=b'{"ok":true}', created_at=now, completed_at=now,
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await IdempotencyRepo.get(mock_session, "key-123")

        assert result is not None
        assert result.id == 5
        assert result.key == "key-123"
        assert result.status == "completed"
        assert result.response_code == 200
        assert result.response_body == b'{"ok":true}'

    async def test_not_found_returns_none(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        result = await IdempotencyRepo.get(mock_session, "nonexistent")
        assert result is None

    async def test_sends_key_param(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        await IdempotencyRepo.get(mock_session, "my-key")
        params = mock_session.execute.call_args[0][1]
        assert params["key"] == "my-key"

    async def test_uses_correct_select(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        await IdempotencyRepo.get(mock_session, "key-abc")
        sql = str(mock_session.execute.call_args[0][0])
        assert "SELECT" in sql
        assert "idempotency_keys" in sql

    async def test_null_response_code(self, mock_session):
        now = datetime.now()
        row = _make_idem_row(
            id=1, key="key-pending", status="pending", response_code=None,
            created_at=now, completed_at=None,
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await IdempotencyRepo.get(mock_session, "key-pending")
        assert result.response_code is None


# ---------------------------------------------------------------------------
# IdempotencyRepo.reserve()
# ---------------------------------------------------------------------------

class TestIdempotencyRepoReserve:
    async def test_new_key_insert(self, mock_session):
        now = datetime.now()
        row = _make_idem_row(id=10, key="new-key", status="pending", created_at=now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await IdempotencyRepo.reserve(mock_session, "new-key")

        assert result.id == 10
        assert result.key == "new-key"
        assert result.status == "pending"

    async def test_new_key_uses_insert_sql(self, mock_session):
        now = datetime.now()
        row = _make_idem_row(id=1, key="new-key", status="pending", created_at=now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await IdempotencyRepo.reserve(mock_session, "new-key")
        sql = str(mock_session.execute.call_args[0][0])
        assert "INSERT INTO idempotency_keys" in sql

    async def test_retry_on_failed_key(self, mock_session):
        """When key exists with status='failed', UPSERT resets to pending."""
        now = datetime.now()
        row = _make_idem_row(id=5, key="failed-key", status="pending", created_at=now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await IdempotencyRepo.reserve(mock_session, "failed-key")

        assert result.status == "pending"
        assert result.response_code is None

    async def test_conflict_on_active_key_raises(self, mock_session):
        """When key exists with non-failed status, RETURNING returns no rows."""
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        with pytest.raises(Exception) as exc_info:
            await IdempotencyRepo.reserve(mock_session, "active-key")
        assert exc_info.value is ErrIdempotencyKeyExists

    async def test_integrity_error_raises(self, mock_session):
        """Concurrent insert beats us — unique violation."""
        mock_session.execute.side_effect = make_pg_integrity_error("23505")

        with pytest.raises(Exception) as exc_info:
            await IdempotencyRepo.reserve(mock_session, "race-key")
        assert exc_info.value is ErrIdempotencyKeyExists

    async def test_integrity_error_preserves_cause(self, mock_session):
        mock_session.execute.side_effect = make_pg_integrity_error("23505")

        with pytest.raises(Exception) as exc_info:
            await IdempotencyRepo.reserve(mock_session, "race-key")
        assert exc_info.value is ErrIdempotencyKeyExists
        assert isinstance(exc_info.value.__cause__, IntegrityError)

    async def test_non_unique_integrity_error_propagates(self, mock_session):
        mock_session.execute.side_effect = make_pg_integrity_error("23514")

        with pytest.raises(IntegrityError):
            await IdempotencyRepo.reserve(mock_session, "bad-key")

    async def test_sends_key_param(self, mock_session):
        now = datetime.now()
        row = _make_idem_row(id=1, key="my-key", status="pending", created_at=now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await IdempotencyRepo.reserve(mock_session, "my-key")
        params = mock_session.execute.call_args[0][1]
        assert params["key"] == "my-key"

    async def test_uses_upsert_pattern(self, mock_session):
        now = datetime.now()
        row = _make_idem_row(id=1, key="key", status="pending", created_at=now)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await IdempotencyRepo.reserve(mock_session, "key")
        sql = str(mock_session.execute.call_args[0][0])
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql


# ---------------------------------------------------------------------------
# IdempotencyRepo.complete()
# ---------------------------------------------------------------------------

class TestIdempotencyRepoComplete:
    async def test_success(self, mock_session):
        await IdempotencyRepo.complete(mock_session, "key-abc", 200, b'{"ok":true}')

        mock_session.execute.assert_called_once()
        params = mock_session.execute.call_args[0][1]
        assert params["key"] == "key-abc"
        assert params["response_code"] == 200
        assert params["response_body"] == b'{"ok":true}'

    async def test_uses_update_sql(self, mock_session):
        await IdempotencyRepo.complete(mock_session, "key-abc", 201, b'{"id":5}')

        sql = str(mock_session.execute.call_args[0][0])
        assert "UPDATE idempotency_keys" in sql
        assert "status = 'completed'" in sql

    async def test_various_status_codes(self, mock_session):
        await IdempotencyRepo.complete(mock_session, "key-1", 204, b"")

        params = mock_session.execute.call_args[0][1]
        assert params["response_code"] == 204


# ---------------------------------------------------------------------------
# IdempotencyRepo.fail()
# ---------------------------------------------------------------------------

class TestIdempotencyRepoFail:
    async def test_success(self, mock_session):
        await IdempotencyRepo.fail(mock_session, "key-abc", 500, b'{"error":"boom"}')

        params = mock_session.execute.call_args[0][1]
        assert params["key"] == "key-abc"
        assert params["response_code"] == 500

    async def test_uses_update_sql(self, mock_session):
        await IdempotencyRepo.fail(mock_session, "key-abc", 503, b"")

        sql = str(mock_session.execute.call_args[0][0])
        assert "UPDATE idempotency_keys" in sql
        assert "status = 'failed'" in sql


# ---------------------------------------------------------------------------
# IdempotencyRepo.delete_expired()
# ---------------------------------------------------------------------------

class TestIdempotencyRepoDeleteExpired:
    async def test_deletes_expired_keys(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=5)

        count = await IdempotencyRepo.delete_expired(mock_session, 3600)
        assert count == 5

    async def test_no_expired_keys(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=0)

        count = await IdempotencyRepo.delete_expired(mock_session, 3600)
        assert count == 0

    async def test_sends_ttl_parameter(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=0)

        await IdempotencyRepo.delete_expired(mock_session, 7200)
        params = mock_session.execute.call_args[0][1]
        assert params["ttl"] == "7200 seconds"

    async def test_uses_delete_sql(self, mock_session):
        mock_session.execute.return_value = make_mock_result(rowcount=0)

        await IdempotencyRepo.delete_expired(mock_session, 3600)
        sql = str(mock_session.execute.call_args[0][0])
        assert "DELETE FROM idempotency_keys" in sql
        assert "status = 'pending'" in sql
