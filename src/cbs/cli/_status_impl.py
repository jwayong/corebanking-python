"""Status command implementations.

Mirrors corebanking/internal/cli/status_print.go and setup_status_service.go.
"""

from __future__ import annotations

# mypy: disable-error-code="no-untyped-def,attr-defined,no-any-return,union-attr,no-untyped-call"


async def check_status(cfg):
    """Check system status."""
    from cbs.domain.setup_status import (
        SetupStatus, TBStatus, PGStatus, MigrationsStatus,
        LedgerStatus, ProductsStatus,
    )
    from cbs.store.postgres.database import Database
    from cbs.domain.currency import CURRENCIES

    status = SetupStatus()

    # Check TigerBeetle.
    try:
        import tigerbeetle as tb  # pyright: ignore[reportMissingImports]

        cluster = tb.Cluster(nodes=[cfg.tb_addresses])
        client = tb.Client(cluster=cluster)
        try:
            client.lookup_accounts([b"\x00" * 16])
        finally:
            client.close()
        status.tigerbeetle = TBStatus(connected=True, addresses=len(cfg.tb_addresses.split(",")))
    except Exception as e:
        status.tigerbeetle = TBStatus(connected=False, error=str(e))

    # Check PostgreSQL.
    try:
        db = await Database.create(cfg.pg_dsn, cfg.pg_pool_max)
        status.postgresql = PGStatus(connected=True)

        session = db.session()

        try:
            # Check migrations.
            try:
                from alembic.config import Config as AlembicConfig
                from alembic.script import ScriptDirectory

                sync_dsn = cfg.pg_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
                if not sync_dsn.startswith("postgresql://"):
                    sync_dsn = cfg.pg_dsn

                alembic_cfg = AlembicConfig("alembic.ini")
                alembic_cfg.set_main_option("script_location", "alembic")
                alembic_cfg.set_main_option("sqlalchemy.url", sync_dsn)

                script = ScriptDirectory.from_config(alembic_cfg)
                current = script.get_current_revision()

                # Count total migrations using Alembic ScriptDirectory.
                total = len(list(script.walk_revisions()))

                status.migrations = MigrationsStatus(
                    applied=1 if current else 0,
                    total=total,
                    dirty=False,
                )
            except Exception:
                status.migrations = MigrationsStatus(total=0)

            # Check ledgers.
            from cbs.store.postgres.system_account_repo import SystemAccountRepo

            for code, cur_info in CURRENCIES.items():
                exists = await SystemAccountRepo.exists(session, code)
                count = 0
                if exists:
                    from sqlalchemy import text as sa_text
                    result = await session.execute(
                        sa_text("SELECT COUNT(*) FROM system_accounts WHERE currency = :currency"),
                        {"currency": code},
                    )
                    count = result.scalar()

                status.ledgers.append(
                    LedgerStatus(
                        currency=code,
                        ledger=cur_info.ledger,
                        accounts_count=count,
                        initialised=count > 0,
                    )
                )

            # Check products.
            from cbs.store.postgres.product_repo import count_products

            product_count = await count_products(session)
            if product_count > 0:
                from sqlalchemy import text as sa_text
                result = await session.execute(
                    sa_text("SELECT code, category FROM products ORDER BY code")
                )
                deposits = []
                loans = []
                for row in result.fetchall():
                    if row[1] == "deposit":
                        deposits.append(row[0])
                    elif row[1] == "loan":
                        loans.append(row[0])

                status.products = ProductsStatus(
                    count=product_count,
                    deposits=deposits,
                    loans=loans,
                )

        finally:
            await session.close()

    except Exception as e:
        status.postgresql = PGStatus(connected=False, error=str(e))

    return status


def print_setup_status(status) -> None:
    """Pretty-print the setup status report.

    Mirrors Go's PrintSetupStatus from cli/status_print.go.
    """
    print("")
    print("Core Banking System — Setup Status")
    print("═" * 39)

    # TigerBeetle.
    if status.tigerbeetle.connected:
        print(f"TigerBeetle:  ✓ connected ({status.tigerbeetle.addresses} replicas)")
    else:
        print("TigerBeetle:  ✗ not connected")
        if status.tigerbeetle.error:
            print(f"               {status.tigerbeetle.error}")

    # PostgreSQL.
    if status.postgresql.connected:
        print("PostgreSQL:   ✓ connected")
    else:
        print("PostgreSQL:   ✗ not connected")
        if status.postgresql.error:
            print(f"               {status.postgresql.error}")

    # Migrations.
    if (status.migrations.total > 0
            and status.migrations.applied == status.migrations.total
            and not status.migrations.dirty):
        print(f"Migrations:   ✓ {status.migrations.applied}/{status.migrations.total} applied")
    else:
        if status.migrations.dirty:
            print(f"Migrations:   ✗ dirty (version {status.migrations.version})")
        elif status.migrations.total > 0:
            pending = status.migrations.total - status.migrations.applied
            print(f"Migrations:   ✗ {status.migrations.applied}/{status.migrations.total} applied (pending: {pending})")
        else:
            print("Migrations:   ✗ no migrations found")

    # Ledgers.
    print("")
    print("Ledgers:")
    if not status.ledgers:
        print("  (none initialised)")
    for ledger in status.ledgers:
        if ledger.initialised:
            print(f"  {ledger.currency} ({ledger.ledger})  ✓ {ledger.accounts_count} system accounts")
        else:
            print(f"  {ledger.currency} ({ledger.ledger})  ✗ not initialised — run: cbs setup ledger --currency {ledger.currency}")

    # Products.
    print("")
    if status.products.count > 0:
        print(f"Products:     ✓ {status.products.count} products seeded")
        if status.products.deposits:
            print(f"  Deposits:   {', '.join(status.products.deposits)}")
        if status.products.loans:
            print(f"  Loans:      {', '.join(status.products.loans)}")
    else:
        print("Products:     ✗ no products seeded")

    # Overall.
    print("")
    if status.healthy:
        print("Status:       ✓ All systems healthy")
    else:
        print("Status:       ✗ Setup incomplete")
