"""Setup status domain model — system health reporting structures.

Mirrors corebanking/internal/domain/status.go.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TBStatus:
    """TigerBeetle connectivity status."""

    connected: bool = False
    addresses: int = 0
    error: str = ""


@dataclass
class PGStatus:
    """PostgreSQL connectivity status."""

    connected: bool = False
    error: str = ""


@dataclass
class MigrationsStatus:
    """Migration state."""

    applied: int = 0
    total: int = 0
    dirty: bool = False
    version: int = 0


@dataclass
class LedgerStatus:
    """Per-currency ledger status."""

    currency: str = ""
    ledger: int = 0
    accounts_count: int = 0
    initialised: bool = False


@dataclass
class ProductsStatus:
    """Product catalogue summary."""

    count: int = 0
    deposits: list[str] | None = None
    loans: list[str] | None = None

    def __post_init__(self) -> None:
        if self.deposits is None:
            self.deposits = []
        if self.loans is None:
            self.loans = []


@dataclass
class SetupStatus:
    """Overall CBS setup status."""

    tigerbeetle: TBStatus = None  # type: ignore[assignment]
    postgresql: PGStatus = None   # type: ignore[assignment]
    migrations: MigrationsStatus = None  # type: ignore[assignment]
    ledgers: list[LedgerStatus] = None   # type: ignore[assignment]
    products: ProductsStatus = None       # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.tigerbeetle is None:
            self.tigerbeetle = TBStatus()
        if self.postgresql is None:
            self.postgresql = PGStatus()
        if self.migrations is None:
            self.migrations = MigrationsStatus()
        if self.ledgers is None:
            self.ledgers = []
        if self.products is None:
            self.products = ProductsStatus()

    @property
    def healthy(self) -> bool:
        """All systems operational."""
        return (
            self.tigerbeetle.connected
            and self.postgresql.connected
            and self.migrations.total > 0
            and self.migrations.applied == self.migrations.total
            and not self.migrations.dirty
            and any(ledger.initialised for ledger in self.ledgers)
            and self.products.count > 0
        )
