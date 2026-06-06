"""Batch run repository — batch job tracking against PostgreSQL.

Mirrors the Go `postgres.BatchRunRepo` with async SQLAlchemy Core queries.
"""

from __future__ import annotations

import json
import structlog
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.exc import NoResultFound  # noqa: F401 — imported for compatibility

log = structlog.get_logger()


@dataclass
class BatchRun:
    """Row from the batch_runs table."""

    id: int = 0
    job_name: str = ""
    business_date: date | None = None
    status: str = ""
    processed_count: int = 0
    success_count: int = 0
    error_count: int = 0
    errors: list[dict] | None = None
    dry_run: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration: str | None = None


@dataclass
class BatchResult:
    """Final result of a batch job execution."""

    processed_count: int = 0
    success_count: int = 0
    error_count: int = 0
    errors: list[dict] | None = None


_BATCH_RUN_COLS = (
    "id, job_name, business_date, status, "
    "processed_count, success_count, error_count, errors, "
    "dry_run, started_at, completed_at"
)


def _row_to_batch_run(row: tuple) -> BatchRun:
    run = BatchRun(
        id=row[0],
        job_name=row[1],
        business_date=row[2],
        status=row[3],
        processed_count=row[4],
        success_count=row[5],
        error_count=row[6],
        dry_run=row[8],
        started_at=row[9],
        completed_at=row[10],
    )

    errors_json = row[7]
    if errors_json:
        try:
            run.errors = json.loads(errors_json)
        except (json.JSONDecodeError, TypeError):
            run.errors = None

    if run.completed_at and run.started_at:
        delta = run.completed_at - run.started_at
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        # Mirror Go time.Duration.String() format: "72h3m0s"
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        run.duration = "".join(parts)

    return run


async def create(session, run: BatchRun) -> int:
    """Insert a new batch run record and return the generated ID."""
    errors_json = json.dumps(run.errors) if run.errors else "[]"

    result = await session.execute(
        text(
            "INSERT INTO batch_runs "
            "(job_name, business_date, status, processed_count, success_count, error_count, errors, dry_run) "
            "VALUES (:job_name, :business_date, :status, :processed_count, :success_count, :error_count, "
            ":errors::jsonb, :dry_run) "
            "RETURNING id"
        ),
        {
            "job_name": run.job_name,
            "business_date": run.business_date,
            "status": run.status,
            "processed_count": run.processed_count,
            "success_count": run.success_count,
            "error_count": run.error_count,
            "errors": errors_json,
            "dry_run": run.dry_run,
        },
    )
    row = result.fetchone()
    if row is None:
        raise RuntimeError("batch_run insert returned no rows")

    run.id = row[0]
    log.info("batch_run_created", id=run.id, job_name=run.job_name)
    return run.id


async def complete(session, run_id: int, result: BatchResult, status: str) -> None:
    """Update a batch run with final results and set completed_at."""
    errors_json = json.dumps(result.errors) if result.errors else "[]"

    await session.execute(
        text(
            "UPDATE batch_runs "
            "SET status = :status, processed_count = :processed_count, success_count = :success_count, "
            "error_count = :error_count, errors = :errors::jsonb, completed_at = NOW() "
            "WHERE id = :id"
        ),
        {
            "status": status,
            "processed_count": result.processed_count,
            "success_count": result.success_count,
            "error_count": result.error_count,
            "errors": errors_json,
            "id": run_id,
        },
    )
    log.info("batch_run_completed", id=run_id, status=status)


async def get_by_date(session, d: date) -> list[BatchRun]:
    """Return all batch runs for a given business date, ordered by started_at DESC."""
    result = await session.execute(
        text(f"SELECT {_BATCH_RUN_COLS} FROM batch_runs WHERE business_date = :date ORDER BY started_at DESC"),
        {"date": d},
    )
    runs = [_row_to_batch_run(row) for row in result.fetchall()]
    log.debug("batch_runs_fetched_by_date", date=d, count=len(runs))
    return runs


async def get_by_job_and_date(session, job_name: str, d: date) -> BatchRun | None:
    """Return the most recent run for a specific job on a date.

    Returns None if no run exists (not an error).
    """
    result = await session.execute(
        text(
            f"SELECT {_BATCH_RUN_COLS} FROM batch_runs "
            f"WHERE job_name = :job_name AND business_date = :date ORDER BY started_at DESC LIMIT 1"
        ),
        {"job_name": job_name, "date": d},
    )
    row = result.fetchone()
    if row is None:
        return None
    run = _row_to_batch_run(row)
    log.debug("batch_run_fetched", job_name=job_name, date=d, id=run.id)
    return run
