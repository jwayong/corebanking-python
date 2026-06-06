"""Migration command implementations.

Mirrors corebanking/internal/cli/migrate.go.
"""

from __future__ import annotations

import os
import time

# mypy: disable-error-code="no-untyped-def,attr-defined,no-any-return"

import typer


def migrate_up(cfg, migrations_dir: str) -> None:
    """Apply all pending migrations."""
    dsn = _get_pg_dsn(cfg)
    if not dsn:
        typer.echo("Error: PostgreSQL DSN is required (--pg-dsn or CBS_PG_DSN)", err=True)
        raise typer.Exit(1)

    sync_dsn = _convert_to_sync_dsn(dsn)
    abs_dir = os.path.abspath(migrations_dir)

    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_cfg.set_main_option("script_location", abs_dir)
        alembic_cfg.set_main_option("sqlalchemy.url", sync_dsn)

        script = ScriptDirectory.from_config(alembic_cfg)
        current = script.get_current_revision()
        heads = script.get_heads()

        if current in heads:
            typer.echo("no pending migrations")
            return

        alembic_cfg.invoke("upgrade", "head")

        new_current = script.get_current_revision()
        typer.echo(f"migrations applied successfully (version: {new_current})")

    except Exception as e:
        typer.echo(f"Error running migrations: {e}", err=True)
        raise typer.Exit(1)


def migrate_down(cfg, migrations_dir: str) -> None:
    """Rollback last migration."""
    dsn = _get_pg_dsn(cfg)
    if not dsn:
        typer.echo("Error: PostgreSQL DSN is required (--pg-dsn or CBS_PG_DSN)", err=True)
        raise typer.Exit(1)

    sync_dsn = _convert_to_sync_dsn(dsn)
    abs_dir = os.path.abspath(migrations_dir)

    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_cfg.set_main_option("script_location", abs_dir)
        alembic_cfg.set_main_option("sqlalchemy.url", sync_dsn)

        script = ScriptDirectory.from_config(alembic_cfg)
        current = script.get_current_revision()

        if current is None:
            typer.echo("no migrations to rollback")
            return

        alembic_cfg.invoke("downgrade", "-1")

        new_current = script.get_current_revision()
        if new_current is None:
            typer.echo("rolled back to empty state")
        else:
            typer.echo(f"rolled back to version {new_current}")

    except Exception as e:
        typer.echo(f"Error rolling back migration: {e}", err=True)
        raise typer.Exit(1)


def migrate_status(cfg, migrations_dir: str) -> None:
    """Show current migration status."""
    dsn = _get_pg_dsn(cfg)
    if not dsn:
        typer.echo("Error: PostgreSQL DSN is required (--pg-dsn or CBS_PG_DSN)", err=True)
        raise typer.Exit(1)

    sync_dsn = _convert_to_sync_dsn(dsn)
    abs_dir = os.path.abspath(migrations_dir)

    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_cfg.set_main_option("script_location", abs_dir)
        alembic_cfg.set_main_option("sqlalchemy.url", sync_dsn)

        script = ScriptDirectory.from_config(alembic_cfg)
        current = script.get_current_revision()
        heads = script.get_heads()

        if current in heads:
            typer.echo(f"Current revision is up to date: {current}")
        else:
            typer.echo(f"Current revision: {current or 'None'}")
            typer.echo(f"Heads: {', '.join(heads)}")

    except Exception as e:
        typer.echo(f"Error checking migration status: {e}", err=True)
        raise typer.Exit(1)


def migrate_create(migrations_dir: str, name: str) -> None:
    """Create a new migration file pair."""
    if not name:
        typer.echo("Error: --name is required", err=True)
        raise typer.Exit(1)

    abs_dir = os.path.abspath(migrations_dir)
    os.makedirs(abs_dir, exist_ok=True)

    version = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    base = f"{version}_{name}"

    up_file = os.path.join(abs_dir, f"{base}.up.sql")
    down_file = os.path.join(abs_dir, f"{base}.down.sql")

    with open(up_file, "w") as f:
        f.write(f"-- Migration: {name}\n-- Direction: up\n")
    with open(down_file, "w") as f:
        f.write(f"-- Migration: {name}\n-- Direction: down\n")

    typer.echo("Created migration files:")
    typer.echo(f"  {up_file}")
    typer.echo(f"  {down_file}")


def _get_pg_dsn(cfg) -> str:
    if cfg and cfg.pg_dsn:
        return cfg.pg_dsn
    import os
    return os.environ.get("CBS_PG_DSN", "")


def _convert_to_sync_dsn(dsn: str) -> str:
    """Convert async DSN to sync DSN for Alembic."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    if dsn.startswith("postgres://"):
        return dsn
    return dsn
