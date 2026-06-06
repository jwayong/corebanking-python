"""Batch command implementations.

Mirrors corebanking/internal/cli/batch.go with run/list/status sub-commands.
"""

from __future__ import annotations

# mypy: disable-error-code="no-untyped-def,attr-defined,no-any-return,list-item,no-untyped-call"

import typer
from datetime import date, datetime

from cbs.store.postgres.batch_repo import BatchResult  # noqa: F401 — used in type hints


async def run_batch_job(cfg, log, job_name: str, biz_date_str: str | None, dry_run: bool) -> None:
    """Execute a batch job."""
    try:
        cfg.validate()
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    d = _parse_date(biz_date_str or date.today().isoformat())

    try:
        result = await _execute_batch(cfg, job_name, d, dry_run)

        typer.echo(f"Job:       {result.job_name}")
        typer.echo(f"Date:      {result.business_date}")
        typer.echo(f"Status:    {result.status}")
        typer.echo(f"Processed: {result.processed_count}")
        typer.echo(f"Success:   {result.success_count}")
        typer.echo(f"Errors:    {result.error_count}")
        typer.echo(f"Duration:  {result.duration or 'N/A'}")
        typer.echo(f"Dry Run:   {result.dry_run}")

        if result.errors:
            typer.echo("")
            typer.echo("Error details:")
            for i, err in enumerate(result.errors):
                typer.echo(f"  {i + 1}. {err}")

    except Exception as e:
        typer.echo(f"Error running batch job: {e}", err=True)
        raise typer.Exit(1)


def list_batch_jobs(cfg, log) -> None:
    """List available batch jobs."""
    try:
        cfg.validate()
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    jobs = [
        "interest_accrual",
        "interest_capitalisation",
        "fee_collection",
        "arrears_check",
    ]

    if not jobs:
        typer.echo("No batch jobs registered.")
        return

    for name in jobs:
        typer.echo(name)


async def get_batch_status(cfg, log, biz_date_str: str | None) -> None:
    """Show batch run status for a date."""
    try:
        cfg.validate()
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    d = _parse_date(biz_date_str or date.today().isoformat())

    try:
        runs = await _get_runs_by_date(cfg, d)

        if not runs:
            typer.echo(f"No batch runs for {d.isoformat()}")
            return

        typer.echo(f"{'JOB':<30} {'STATUS':<12} {'PROCESSED':>10} {'SUCCESS':>10} {'ERRORS':>8} {'DURATION':<12} {'DRY_RUN':<7}")
        typer.echo("-" * 89)

        for run in runs:
            typer.echo(
                f"{run.job_name:<30} {run.status:<12} {run.processed_count:>10} "
                f"{run.success_count:>10} {run.error_count:>8} {(run.duration or 'N/A'):<12} "
                f"{str(run.dry_run):<7}"
            )

    except Exception as e:
        typer.echo(f"Error getting batch status: {e}", err=True)
        raise typer.Exit(1)


# -- internal helpers ----------------------------------------------------

async def _execute_batch(cfg, job_name: str, biz_date: date, dry_run: bool):
    """Execute a batch job and return the result."""
    from cbs.store.postgres.database import Database
    from cbs.store.postgres.batch_repo import create, complete, get_by_job_and_date, BatchRun, BatchResult

    db = await Database.create(cfg.pg_dsn, cfg.pg_pool_max)
    session = db.session()

    try:
        # Check for existing completed run.
        if not dry_run:
            existing = await get_by_job_and_date(session, job_name, biz_date)
            if existing and existing.status == "completed":
                raise ValueError(
                    f"Job {job_name!r} already completed for date {biz_date.isoformat()}"
                )

        # Create batch run record.
        run = BatchRun(
            job_name=job_name,
            business_date=biz_date,
            status="running",
            dry_run=dry_run,
        )
        run_id = await create(session, run)

        # Execute the job logic.
        result = await _do_job(job_name, cfg, biz_date, dry_run)

        # Determine status.
        status = "completed" if result.error_count == 0 else "failed"

        # Complete the run.
        await complete(session, run_id, result, status)

        # Fetch the completed run.
        completed = await get_by_job_and_date(session, job_name, biz_date)
        return completed or run

    except Exception as e:
        # Mark as failed.
        error_result = BatchResult(
            error_count=1,
            errors=[str(e)],
        )

        # Try to get the run_id if we created it.
        try:
            existing = await get_by_job_and_date(session, job_name, biz_date)
            if existing:
                await complete(session, existing.id, error_result, "failed")
        except Exception:
            pass

        completed = await get_by_job_and_date(session, job_name, biz_date)
        return completed or BatchRun(
            job_name=job_name,
            business_date=biz_date,
            status="failed",
            error_count=1,
            errors=[str(e)],
        )

    finally:
        await session.close()


async def _do_job(job_name: str, cfg, biz_date: date, dry_run: bool) -> BatchResult:
    """Execute a specific batch job."""
    from cbs.store.postgres.batch_repo import BatchResult

    if job_name == "interest_accrual":
        if dry_run:
            typer.echo(f"[DRY RUN] Would accrue interest for {biz_date.isoformat()}")
        else:
            typer.echo(f"Accruing interest for {biz_date.isoformat()}")
        return BatchResult(processed_count=0, success_count=0)

    elif job_name == "interest_capitalisation":
        if dry_run:
            typer.echo(f"[DRY RUN] Would capitalise interest for {biz_date.isoformat()}")
        else:
            typer.echo(f"Capitalising interest for {biz_date.isoformat()}")
        return BatchResult(processed_count=0, success_count=0)

    elif job_name == "fee_collection":
        if dry_run:
            typer.echo(f"[DRY RUN] Would collect fees for {biz_date.isoformat()}")
        else:
            typer.echo(f"Collecting fees for {biz_date.isoformat()}")
        return BatchResult(processed_count=0, success_count=0)

    elif job_name == "arrears_check":
        if dry_run:
            typer.echo(f"[DRY RUN] Would check arrears for {biz_date.isoformat()}")
        else:
            typer.echo(f"Checking arrears for {biz_date.isoformat()}")
        return BatchResult(processed_count=0, success_count=0)

    else:
        raise ValueError(f"Unknown batch job: {job_name!r}")


async def _get_runs_by_date(cfg, biz_date: date):
    """Get all batch runs for a given date."""
    from cbs.store.postgres.database import Database
    from cbs.store.postgres.batch_repo import get_by_date

    db = await Database.create(cfg.pg_dsn, cfg.pg_pool_max)
    session = db.session()

    runs = await get_by_date(session, biz_date)

    await session.close()
    return runs


def _parse_date(s: str) -> date:
    """Parse a YYYY-MM-DD date string."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format {s!r} (expected YYYY-MM-DD)")
