"""Unit tests for batch_repo module-level functions.

Tests verify INSERT/SELECT SQL, JSONB error serialization, duration
computation, and row mapping using mocked async sessions.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

from cbs.store.postgres.batch_repo import (
    BatchResult,
    BatchRun,
    complete,
    create,
    get_by_date,
    get_by_job_and_date,
)
from tests.unit.store.postgres.fixtures import make_mock_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_batch_row(
    id=1, job_name="interest_calc", business_date=None, status="running",
    processed_count=100, success_count=98, error_count=2, errors=None,
    dry_run=False, started_at=None, completed_at=None,
):
    if business_date is None:
        business_date = date(2025, 6, 1)
    if started_at is None:
        started_at = datetime(2025, 6, 1, 8, 0, 0)
    if errors is None:
        errors = "[]"

    return (
        id, job_name, business_date, status,
        processed_count, success_count, error_count, errors,
        dry_run, started_at, completed_at,
    )


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

class TestBatchCreate:
    async def test_success_with_errors(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=(5,))

        run = BatchRun(
            job_name="interest_calc", business_date=date(2025, 6, 1),
            status="running", errors=[{"msg": "timeout"}], dry_run=False,
        )
        result = await create(mock_session, run)

        assert result == 5
        assert run.id == 5

    async def test_success_no_errors(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=(3,))

        run = BatchRun(
            job_name="fee_collection", business_date=date(2025, 6, 1),
            status="running", dry_run=False,
        )
        result = await create(mock_session, run)

        assert result == 3

    async def test_errors_serialized_to_json(self, mock_session):
        import json
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1,))

        run = BatchRun(
            job_name="arrears_check", business_date=date(2025, 6, 1),
            status="running", errors=[{"account": "ACC-001", "msg": "closed"}],
        )
        await create(mock_session, run)

        params = mock_session.execute.call_args[0][1]
        errors = json.loads(params["errors"])
        assert len(errors) == 1
        assert errors[0]["account"] == "ACC-001"

    async def test_no_errors_defaults_to_empty_array(self, mock_session):
        import json
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1,))

        run = BatchRun(
            job_name="interest_calc", business_date=date(2025, 6, 1),
            status="running", dry_run=False,
        )
        await create(mock_session, run)

        params = mock_session.execute.call_args[0][1]
        errors = json.loads(params["errors"])
        assert errors == []

    async def test_no_rows_raises_runtime_error(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        run = BatchRun(job_name="test", status="running")
        with pytest.raises(RuntimeError, match="batch_run insert returned no rows"):
            await create(mock_session, run)

    async def test_sends_all_fields(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1,))

        run = BatchRun(
            job_name="daily_settlement", business_date=date(2025, 7, 1),
            status="running", processed_count=0, success_count=0,
            error_count=0, dry_run=True,
        )
        await create(mock_session, run)

        params = mock_session.execute.call_args[0][1]
        assert params["job_name"] == "daily_settlement"
        assert params["business_date"] == date(2025, 7, 1)
        assert params["dry_run"] is True

    async def test_uses_insert_sql(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=(1,))

        run = BatchRun(job_name="test", status="running")
        await create(mock_session, run)

        sql = str(mock_session.execute.call_args[0][0])
        assert "INSERT INTO batch_runs" in sql


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------

class TestBatchComplete:
    async def test_success(self, mock_session):
        result = BatchResult(
            processed_count=100, success_count=98, error_count=2,
            errors=[{"msg": "timeout"}],
        )
        await complete(mock_session, 5, result, "completed")

        params = mock_session.execute.call_args[0][1]
        assert params["id"] == 5
        assert params["status"] == "completed"
        assert params["processed_count"] == 100

    async def test_error_status(self, mock_session):
        result = BatchResult(processed_count=50, success_count=40, error_count=10)
        await complete(mock_session, 3, result, "error")

        params = mock_session.execute.call_args[0][1]
        assert params["status"] == "error"

    async def test_uses_update_sql(self, mock_session):
        result = BatchResult(processed_count=10, success_count=10, error_count=0)
        await complete(mock_session, 1, result, "completed")

        sql = str(mock_session.execute.call_args[0][0])
        assert "UPDATE batch_runs" in sql

    async def test_errors_serialized_to_json(self, mock_session):
        import json
        result = BatchResult(
            processed_count=5, success_count=3, error_count=2,
            errors=[{"code": "ERR1"}, {"code": "ERR2"}],
        )
        await complete(mock_session, 1, result, "error")

        params = mock_session.execute.call_args[0][1]
        errors = json.loads(params["errors"])
        assert len(errors) == 2

    async def test_no_errors_defaults_to_empty_array(self, mock_session):
        import json
        result = BatchResult(processed_count=10, success_count=10)
        await complete(mock_session, 1, result, "completed")

        params = mock_session.execute.call_args[0][1]
        errors = json.loads(params["errors"])
        assert errors == []


# ---------------------------------------------------------------------------
# get_by_date()
# ---------------------------------------------------------------------------

class TestBatchGetByDate:
    async def test_multiple_runs(self, mock_session):
        rows = [
            _make_batch_row(id=2, job_name="fee_collection", started_at=datetime(2025, 6, 1, 9, 0)),
            _make_batch_row(id=1, job_name="interest_calc", started_at=datetime(2025, 6, 1, 8, 0)),
        ]
        mock_session.execute.return_value = make_mock_result(fetchall_val=rows)

        result = await get_by_date(mock_session, date(2025, 6, 1))

        assert len(result) == 2
        assert result[0].job_name == "fee_collection"
        assert result[1].job_name == "interest_calc"

    async def test_empty_result(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        result = await get_by_date(mock_session, date(2025, 6, 1))
        assert result == []

    async def test_duration_computation(self, mock_session):
        started = datetime(2025, 6, 1, 8, 0, 0)
        completed = started + timedelta(hours=1, minutes=12, seconds=3)
        row = _make_batch_row(
            id=1, started_at=started, completed_at=completed, status="completed",
        )
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        result = await get_by_date(mock_session, date(2025, 6, 1))
        assert len(result) == 1
        assert result[0].duration == "1h12m3s"

    async def test_duration_hours_minutes_seconds(self, mock_session):
        started = datetime(2025, 6, 1, 8, 0, 0)
        completed = datetime(2025, 6, 1, 10, 5, 30)
        row = _make_batch_row(
            id=1, started_at=started, completed_at=completed, status="completed",
        )
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        result = await get_by_date(mock_session, date(2025, 6, 1))
        assert result[0].duration == "2h5m30s"

    async def test_duration_seconds_only(self, mock_session):
        started = datetime(2025, 6, 1, 8, 0, 0)
        completed = datetime(2025, 6, 1, 8, 0, 45)
        row = _make_batch_row(
            id=1, started_at=started, completed_at=completed, status="completed",
        )
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        result = await get_by_date(mock_session, date(2025, 6, 1))
        assert result[0].duration == "45s"

    async def test_no_duration_when_started_at_missing(self, mock_session):
        completed = datetime.now()
        # Build row tuple directly to avoid helper auto-filling started_at
        row = (1, "interest_calc", date(2025, 6, 1), "completed",
               100, 98, 2, "[]", False, None, completed)
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        result = await get_by_date(mock_session, date(2025, 6, 1))
        assert result[0].duration is None

    async def test_parses_errors_json(self, mock_session):
        import json
        errors = [{"msg": "timeout"}]
        row = _make_batch_row(
            id=1, errors=json.dumps(errors), status="error",
        )
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        result = await get_by_date(mock_session, date(2025, 6, 1))
        assert result[0].errors == errors

    async def test_invalid_errors_json_defaults_to_none(self, mock_session):
        row = _make_batch_row(id=1, errors="not valid json", status="error")
        mock_session.execute.return_value = make_mock_result(fetchall_val=[row])

        result = await get_by_date(mock_session, date(2025, 6, 1))
        assert result[0].errors is None

    async def test_sends_date_param(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchall_val=[])

        await get_by_date(mock_session, date(2025, 12, 25))
        params = mock_session.execute.call_args[0][1]
        assert params["date"] == date(2025, 12, 25)


# ---------------------------------------------------------------------------
# get_by_job_and_date()
# ---------------------------------------------------------------------------

class TestBatchGetByJobAndDate:
    async def test_found(self, mock_session):
        row = _make_batch_row(
            id=5, job_name="interest_calc", business_date=date(2025, 6, 1),
            status="completed", started_at=datetime(2025, 6, 1, 8, 0),
        )
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        result = await get_by_job_and_date(mock_session, "interest_calc", date(2025, 6, 1))

        assert result is not None
        assert result.id == 5
        assert result.job_name == "interest_calc"

    async def test_not_found_returns_none(self, mock_session):
        mock_session.execute.return_value = make_mock_result(fetchone_val=None)

        result = await get_by_job_and_date(mock_session, "nonexistent", date(2025, 6, 1))
        assert result is None

    async def test_sends_job_and_date_params(self, mock_session):
        row = _make_batch_row(id=1)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_by_job_and_date(mock_session, "fee_collection", date(2025, 7, 1))
        params = mock_session.execute.call_args[0][1]
        assert params["job_name"] == "fee_collection"
        assert params["date"] == date(2025, 7, 1)

    async def test_uses_limit_one(self, mock_session):
        row = _make_batch_row(id=1)
        mock_session.execute.return_value = make_mock_result(fetchone_val=row)

        await get_by_job_and_date(mock_session, "test", date(2025, 6, 1))
        sql = str(mock_session.execute.call_args[0][0])
        assert "LIMIT 1" in sql
