"""Tests for status report printing."""

import pytest

from cbs.cli._status_impl import print_setup_status
from cbs.domain.setup_status import (
    SetupStatus, TBStatus, PGStatus, MigrationsStatus,
    LedgerStatus, ProductsStatus,
)


class TestPrintSetupStatus:
    """Test status report formatting."""

    def test_healthy_status(self, capsys):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=6, deposits=["savings_usd"], loans=["personal_loan_usd"])

        print_setup_status(status)
        output = capsys.readouterr().out

        assert "Core Banking System" in output
        assert "✓ connected (3 replicas)" in output
        assert "✓ connected" in output
        assert "✓ 5/5 applied" in output
        assert "✓ 18 system accounts" in output
        assert "✓ 6 products seeded" in output
        assert "All systems healthy" in output

    def test_unhealthy_status(self, capsys):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=False, error="connection refused")
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=3, total=5)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=0)]
        status.products = ProductsStatus(count=0)

        print_setup_status(status)
        output = capsys.readouterr().out

        assert "✗ not connected" in output
        assert "connection refused" in output
        assert "pending: 2" in output
        assert "✗ not initialised" in output
        assert "✗ no products seeded" in output
        assert "Setup incomplete" in output

    def test_dirty_migrations(self, capsys):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=1)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5, dirty=True, version=42)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=3)

        print_setup_status(status)
        output = capsys.readouterr().out

        assert "✗ dirty (version 42)" in output

    def test_no_migrations_found(self, capsys):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=1)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(total=0)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=3)

        print_setup_status(status)
        output = capsys.readouterr().out

        assert "✗ no migrations found" in output

    def test_no_ledgers_initialised(self, capsys):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=1)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5)
        status.ledgers = []
        status.products = ProductsStatus(count=3)

        print_setup_status(status)
        output = capsys.readouterr().out

        assert "(none initialised)" in output
