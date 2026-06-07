"""Tests for setup status domain model."""

import pytest

from cbs.domain.setup_status import (
    SetupStatus, TBStatus, PGStatus, MigrationsStatus,
    LedgerStatus, ProductsStatus,
)


class TestSetupStatus:
    """Test SetupStatus healthy flag computation."""

    def test_healthy_when_all_ok(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5, dirty=False)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=6)

        assert status.healthy is True

    def test_unhealthy_without_tb(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=False, error="connection refused")
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=6)

        assert status.healthy is False

    def test_unhealthy_without_pg(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=False, error="connection refused")
        status.migrations = MigrationsStatus(applied=5, total=5)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=6)

        assert status.healthy is False

    def test_unhealthy_pending_migrations(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=3, total=5)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=6)

        assert status.healthy is False

    def test_unhealthy_dirty_migrations(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5, dirty=True)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=6)

        assert status.healthy is False

    def test_unhealthy_no_ledgers(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=0)]
        status.products = ProductsStatus(count=6)

        assert status.healthy is False

    def test_unhealthy_no_products(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(applied=5, total=5)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=0)

        assert status.healthy is False

    def test_unhealthy_no_migrations(self):
        status = SetupStatus()
        status.tigerbeetle = TBStatus(connected=True, addresses=3)
        status.postgresql = PGStatus(connected=True)
        status.migrations = MigrationsStatus(total=0)
        status.ledgers = [LedgerStatus(currency="USD", ledger=840, accounts_count=18, initialised=True)]
        status.products = ProductsStatus(count=6)

        assert status.healthy is False


class TestProductsStatus:
    """Test ProductsStatus defaults."""

    def test_default_lists_are_empty(self):
        ps = ProductsStatus()
        assert ps.deposits == []
        assert ps.loans == []


class TestSetupStatusDefaults:
    """Test SetupStatus default initialisation."""

    def test_defaults_are_safe(self):
        status = SetupStatus()
        assert isinstance(status.tigerbeetle, TBStatus)
        assert isinstance(status.postgresql, PGStatus)
        assert isinstance(status.migrations, MigrationsStatus)
        assert status.ledgers == []
        assert isinstance(status.products, ProductsStatus)
