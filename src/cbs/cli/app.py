"""CLI application root — Typer app with all sub-commands.

Mirrors corebanking/internal/cli/root.go.
"""

from __future__ import annotations

# mypy: disable-error-code="no-untyped-def,no-any-return,no-untyped-call"

import sys

import structlog
import typer

from cbs.config import CBSConfig, apply_flags, load_defaults, load_from_file

cli_app = typer.Typer(
    name="cbs",
    help="Core Banking System — a high-performance, strongly-consistent core banking system.",
)

# --- Sub-commands --------------------------------------------------------

# Migrate group
migrate_app = typer.Typer()

@migrate_app.command("up")
def migrate_up(
    ctx: typer.Context,
) -> None:
    """Apply all pending migrations."""
    _run_migrate_up(ctx)


@migrate_app.command("down")
def migrate_down(
    ctx: typer.Context,
) -> None:
    """Rollback last migration."""
    _run_migrate_down(ctx)


@migrate_app.command("status")
def migrate_status(
    ctx: typer.Context,
) -> None:
    """Show current migration status."""
    _run_migrate_status(ctx)


@migrate_app.command("create")
def migrate_create(
    ctx: typer.Context,
    name: str = typer.Option("", "--name", "-n", help="Migration name (required)"),
) -> None:
    """Create a new migration file pair."""
    _run_migrate_create(ctx, name)


cli_app.add_typer(migrate_app, name="migrate", help="Database migrations")


# Setup group
setup_app = typer.Typer()

@setup_app.command("init")
def setup_init(
    ctx: typer.Context,
    currency: list[str] | None = typer.Option(None, "--currency", help="Currency code(s) to initialise"),
    products_file: str | None = typer.Option(None, "--products-file", help="Path to products YAML file"),
) -> None:
    """Run all setup steps in order."""
    _run_setup_init(ctx, currency, products_file)


@setup_app.command("ledger")
def setup_ledger(
    ctx: typer.Context,
    currency: list[str] = typer.Option(..., "--currency", help="ISO 4217 currency code (repeatable)"),
) -> None:
    """Create TigerBeetle ledger system accounts."""
    _run_setup_ledger(ctx, currency)


@setup_app.command("product")
def setup_product(
    ctx: typer.Context,
    file: str = typer.Option("", "--file", "-f", help="Path to products YAML/JSON file"),
) -> None:
    """Seed product catalogue into PostgreSQL."""
    _run_setup_product(ctx, file)


@setup_app.command("status")
def setup_status_cmd(
    ctx: typer.Context,
) -> None:
    """Verify setup state (check TB + PG connectivity and data)."""
    _run_setup_status(ctx)


cli_app.add_typer(setup_app, name="setup", help="Bootstrap the core banking system")


# Batch group
batch_app = typer.Typer()

@batch_app.command("run")
def batch_run(
    ctx: typer.Context,
    job_name: str = typer.Argument(..., help="Batch job name"),
    biz_date: str | None = typer.Option(None, "--date", help="Business date (YYYY-MM-DD)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run mode"),
) -> None:
    """Execute a batch job."""
    _run_batch_run(ctx, job_name, biz_date, dry_run)


@batch_app.command("list")
def batch_list(
    ctx: typer.Context,
) -> None:
    """List available batch jobs."""
    _run_batch_list(ctx)


@batch_app.command("status")
def batch_status(
    ctx: typer.Context,
    biz_date: str | None = typer.Option(None, "--date", help="Business date (YYYY-MM-DD)"),
) -> None:
    """Show batch run status for a date."""
    _run_batch_status(ctx, biz_date)


cli_app.add_typer(batch_app, name="batch", help="Run and manage batch jobs")


# Serve command
@cli_app.command("serve")
def serve_run(
    ctx: typer.Context,
) -> None:
    """Start the HTTP API server."""
    _run_serve(ctx)


# Status command
@cli_app.command("status")
def status_show(
    ctx: typer.Context,
) -> None:
    """Print a system status report."""
    _run_status(ctx)


# --- Global options via callback -----------------------------------------

@cli_app.callback(invoke_without_command=True)
def _global_opts(
    ctx: typer.Context,
    port: int | None = typer.Option(None, "--port", help="HTTP API listen port (default 8080)"),
    tb_address: str | None = typer.Option(None, "--tb-address", help="TigerBeetle replica addresses (comma-separated)"),
    pg_dsn: str | None = typer.Option(None, "--pg-dsn", help="PostgreSQL connection string"),
    log_level: str | None = typer.Option(None, "--log-level", help="Log level: debug, info, warn, error"),
    config: str | None = typer.Option(None, "--config", help="Path to YAML config file"),
    migrations_dir: str | None = typer.Option(None, "--migrations-dir", help="Path to migrations directory"),
) -> None:
    """Core Banking System"""
    # Load config.
    if config:
        try:
            cfg = load_from_file(config)
        except Exception as e:
            typer.echo(f"Error loading config file: {e}", err=True)
            raise typer.Exit(1)
    else:
        cfg = load_defaults()

    # Apply CLI flags.
    cfg = apply_flags(cfg, port, tb_address, pg_dsn, log_level)

    # Setup structlog.
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )

    ctx.obj = {
        "cfg": cfg,
        "log": structlog.get_logger(),
        "migrations_dir": migrations_dir or "alembic",
    }


# --- Command implementations ---------------------------------------------

def _get_cfg(ctx: typer.Context) -> CBSConfig:
    state = ctx.obj or {}
    cfg = state.get("cfg")
    if cfg is None:
        typer.echo("Error: configuration not initialized", err=True)
        raise typer.Exit(1)
    return cfg


# Serve
async def _do_serve(cfg: CBSConfig, log) -> None:
    import uvicorn  # pyright: ignore[reportMissingImports]
    from cbs.main import app as litestar_app

    log.info(
        "starting_cbs_api_server",
        port=cfg.port,
        log_level=cfg.log_level,
        tb_addresses=cfg.tb_addresses,
    )

    typer.echo(f"Listening on :{cfg.port}")
    uvicorn.run(
        litestar_app,
        host="0.0.0.0",
        port=cfg.port,
        log_level=cfg.log_level,
    )


def _run_serve(ctx: typer.Context) -> None:
    import signal

    cfg = _get_cfg(ctx)
    log = ctx.obj.get("log")

    try:
        cfg.validate()
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    def _signal_handler(signum, frame):
        if log:
            log.info("shutting_down_gracefully")
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    import asyncio
    asyncio.run(_do_serve(cfg, log))


# Migrate implementations
def _run_migrate_up(ctx: typer.Context) -> None:
    from cbs.cli._migrate_impl import migrate_up
    cfg = _get_cfg(ctx)
    migrations_dir = ctx.obj.get("migrations_dir", "alembic")
    migrate_up(cfg, migrations_dir)


def _run_migrate_down(ctx: typer.Context) -> None:
    from cbs.cli._migrate_impl import migrate_down
    cfg = _get_cfg(ctx)
    migrations_dir = ctx.obj.get("migrations_dir", "alembic")
    migrate_down(cfg, migrations_dir)


def _run_migrate_status(ctx: typer.Context) -> None:
    from cbs.cli._migrate_impl import migrate_status
    cfg = _get_cfg(ctx)
    migrations_dir = ctx.obj.get("migrations_dir", "alembic")
    migrate_status(cfg, migrations_dir)


def _run_migrate_create(ctx: typer.Context, name: str) -> None:
    from cbs.cli._migrate_impl import migrate_create
    migrations_dir = ctx.obj.get("migrations_dir", "alembic")
    migrate_create(migrations_dir, name)


# Setup implementations
def _run_setup_init(ctx: typer.Context, currency: list[str] | None, products_file: str | None) -> None:
    import asyncio
    from cbs.cli._setup_impl import setup_init
    cfg = _get_cfg(ctx)
    log = ctx.obj.get("log")
    asyncio.run(setup_init(cfg, log, currency, products_file))


def _run_setup_ledger(ctx: typer.Context, currency: list[str]) -> None:
    import asyncio
    from cbs.cli._setup_impl import setup_ledger
    cfg = _get_cfg(ctx)
    log = ctx.obj.get("log")
    asyncio.run(setup_ledger(cfg, log, currency))


def _run_setup_product(ctx: typer.Context, file: str) -> None:
    import asyncio
    from cbs.cli._setup_impl import setup_product
    cfg = _get_cfg(ctx)
    log = ctx.obj.get("log")
    asyncio.run(setup_product(cfg, log, file))


def _run_setup_status(ctx: typer.Context) -> None:
    import asyncio
    from cbs.cli._status_impl import check_status, print_setup_status
    cfg = _get_cfg(ctx)
    status = asyncio.run(check_status(cfg))
    print_setup_status(status)
    if not status.healthy:
        raise typer.Exit(1)


# Batch implementations
def _run_batch_run(ctx: typer.Context, job_name: str, biz_date: str | None, dry_run: bool) -> None:
    import asyncio
    from cbs.cli._batch_impl import run_batch_job
    cfg = _get_cfg(ctx)
    log = ctx.obj.get("log")
    asyncio.run(run_batch_job(cfg, log, job_name, biz_date, dry_run))


def _run_batch_list(ctx: typer.Context) -> None:
    from cbs.cli._batch_impl import list_batch_jobs
    cfg = _get_cfg(ctx)
    log = ctx.obj.get("log")
    list_batch_jobs(cfg, log)


def _run_batch_status(ctx: typer.Context, biz_date: str | None) -> None:
    import asyncio
    from cbs.cli._batch_impl import get_batch_status
    cfg = _get_cfg(ctx)
    log = ctx.obj.get("log")
    asyncio.run(get_batch_status(cfg, log, biz_date))


# Status implementation
def _run_status(ctx: typer.Context) -> None:
    import asyncio
    from cbs.cli._status_impl import check_status, print_setup_status
    cfg = _get_cfg(ctx)
    status = asyncio.run(check_status(cfg))
    print_setup_status(status)
    if not status.healthy:
        raise typer.Exit(1)


def main() -> None:
    """Entry point for the cbs CLI."""
    cli_app()


if __name__ == "__main__":
    main()
