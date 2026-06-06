"""Setup command implementations.

Mirrors corebanking/internal/cli/setup.go with init/ledger/product/status sub-commands.
"""

from __future__ import annotations

# mypy: disable-error-code="no-untyped-def,attr-defined,no-any-return,no-untyped-call,call-arg"

import typer

from cbs.domain.currency import lookup_currency
from cbs.domain.system_accounts import SYSTEM_ACCOUNTS


async def setup_init(cfg, log, currency: list[str] | None, products_file: str | None) -> None:
    """Run full bootstrap sequence."""
    try:
        cfg.validate()
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if log:
        log.info("running_full_bootstrap_sequence")

    typer.echo("Running full bootstrap sequence...")

    # Step 1: Verify connections.
    typer.echo("Step 1: Verifying connections...")
    try:
        await _verify_connections(cfg)
        typer.echo("  Connections verified.")
    except Exception as e:
        typer.echo(f"  Connection check failed: {e}", err=True)
        raise typer.Exit(1)

    # Step 2: Run migrations.
    typer.echo("Step 2: Running migrations...")
    try:
        from cbs.cli._migrate_impl import migrate_up as _migrate_up

        _migrate_up(cfg, "alembic")
        typer.echo("  Migrations applied.")
    except Exception as e:
        typer.echo(f"  Migration failed: {e}", err=True)
        raise typer.Exit(1)

    # Step 3: Create system accounts.
    currencies = currency or ["USD"]
    typer.echo(f"Step 3: Creating system accounts for {', '.join(currencies)}...")
    try:
        total = await _setup_ledger(cfg, currencies)
        typer.echo(f"  Created {total} system accounts.")
    except Exception as e:
        typer.echo(f"  System account creation failed: {e}", err=True)
        raise typer.Exit(1)

    # Step 4: Seed products.
    if products_file:
        typer.echo(f"Step 4: Seeding products from {products_file}...")
        try:
            count = await _seed_products(cfg, products_file)
            typer.echo(f"  Seeded {count} products.")
        except Exception as e:
            typer.echo(f"  Product seeding failed: {e}", err=True)
            raise typer.Exit(1)


async def setup_ledger(cfg, log, currency: list[str]) -> None:
    """Create TigerBeetle ledger system accounts."""
    try:
        cfg.validate()
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not currency:
        typer.echo("Error: at least one --currency is required", err=True)
        raise typer.Exit(1)

    try:
        total = await _setup_ledger(cfg, currency)
        typer.echo(f"Created {total} system accounts.")
    except Exception as e:
        typer.echo(f"Error setting up ledger: {e}", err=True)
        raise typer.Exit(1)


async def setup_product(cfg, log, file: str) -> None:
    """Seed product catalogue into PostgreSQL."""
    try:
        cfg.validate()
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not file:
        typer.echo("Error: --file is required", err=True)
        raise typer.Exit(1)

    try:
        count = await _seed_products(cfg, file)
        typer.echo(f"Seeded {count} products from {file}")
    except Exception as e:
        typer.echo(f"Error seeding products: {e}", err=True)
        raise typer.Exit(1)


# -- internal helpers ----------------------------------------------------

async def _verify_connections(cfg) -> None:
    """Verify TB and PG connectivity."""
    from cbs.store.postgres.database import Database

    # Check PostgreSQL.
    try:
        db = await Database.create(cfg.pg_dsn, cfg.pg_pool_max)
        await db.close()
    except Exception as e:
        raise ValueError(f"PostgreSQL connection failed: {e}") from e

    # Check TigerBeetle.
    try:
        import tigerbeetle as tb  # pyright: ignore[reportMissingImports]

        cluster = tb.Cluster(nodes=[cfg.tb_addresses])
        client = tb.Client(cluster=cluster)
        client.lookup_accounts([b"\x00" * 16])
    except Exception as e:
        raise ValueError(f"TigerBeetle connection failed: {e}") from e


async def _setup_ledger(cfg, currencies: list[str]) -> int:
    """Create system accounts for the given currencies."""
    from cbs.store.postgres.database import Database
    from cbs.store.postgres.system_account_repo import SystemAccountRepo, CreatedSystemAccount
    from cbs.util.uuid import generate_uuidv7, uuid_to_uint128

    db = await Database.create(cfg.pg_dsn, cfg.pg_pool_max)
    session = db.session()

    total_created = 0

    for currency_code in currencies:
        try:
            cur_info = lookup_currency(currency_code)
        except ValueError as e:
            typer.echo(f"  Warning: {e}", err=True)
            continue

        # Check if already exists.
        exists = await SystemAccountRepo.exists(session, currency_code)
        if exists:
            typer.echo(f"  {currency_code}: system accounts already exist (skipped)")
            continue

        # Create TigerBeetle accounts.
        try:
            import tigerbeetle as tb  # pyright: ignore[reportMissingImports]

            cluster = tb.Cluster(nodes=[cfg.tb_addresses])
            client = tb.Client(cluster=cluster)

            accounts_to_create = []
            records = []

            for i, sa_def in enumerate(SYSTEM_ACCOUNTS):
                uuid_bytes = generate_uuidv7()
                uint128_id = uuid_to_uint128(uuid_bytes)

                # Build TB flags.
                flags = 0
                if sa_def.tb_flags_debits:
                    flags |= tb.AccountFlags.CREDITS_MUST_NOT_EXCEED_DEBITS
                if sa_def.tb_flags_credits:
                    flags |= tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS
                if sa_def.tb_flags_history:
                    flags |= tb.AccountFlags.HISTORY

                # Linked flag for batch optimisation (all but last).
                if i < len(SYSTEM_ACCOUNTS) - 1:
                    flags |= tb.AccountFlags.LINKED_ID

                account = tb.Account(
                    account_id=uint128_id,
                    account_ledger=cur_info.ledger,
                    account_flags=flags,
                    account_user_data_ex=b"",
                )
                accounts_to_create.append(account)

                records.append(
                    CreatedSystemAccount(
                        tb_account_id=uint128_id,
                        currency=currency_code,
                        ledger=cur_info.ledger,
                        code=sa_def.code,
                        name=sa_def.name,
                    )
                )

            # Create accounts in TigerBeetle.
            client.create_accounts(accounts_to_create)

            # Insert into PostgreSQL.
            await SystemAccountRepo.insert_batch(session, records)

            typer.echo(f"  {currency_code}: created {len(records)} system accounts")
            total_created += len(records)

        except Exception as e:
            typer.echo(f"  {currency_code}: failed — {e}", err=True)
            raise

    await session.close()
    return total_created


async def _seed_products(cfg, file_path: str) -> int:
    """Seed products from YAML file into PostgreSQL."""
    from cbs.store.postgres.database import Database
    from cbs.store.postgres.product_repo import seed_products, system_accounts_exist_for_currency
    from cbs.domain.products import load_products_from_yaml

    db = await Database.create(cfg.pg_dsn, cfg.pg_pool_max)
    session = db.session()

    # Load and validate products.
    try:
        products = load_products_from_yaml(file_path)
    except Exception as e:
        raise ValueError(f"Failed to load products from {file_path}: {e}") from e

    # Check system accounts exist for each currency.
    currencies = set(p.currency for p in products)
    for cur in currencies:
        exists = await system_accounts_exist_for_currency(session, cur)
        if not exists:
            raise ValueError(
                f"System accounts for currency {cur} do not exist. "
                f"Run 'cbs setup ledger --currency {cur}' first."
            )

    # Seed products.
    count = await seed_products(session, products)

    await session.close()
    return count
