"""Settlement service — business logic for settlement batch operations.

Mirrors corebanking/internal/service/settlement_service.go (stub).
"""

from __future__ import annotations

import structlog
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.errors import ErrNotImplemented
from cbs.domain.settlements import SettlementBatch

log = structlog.get_logger()


class SettlementService:
    """Handles settlement batch processing.

    Coordinates between TigerBeetle transfers and PostgreSQL settlement
    records to execute interbank settlement batches.

    **Stub** — full implementation pending SettlementRepo methods.
    """

    def __init__(
        self,
        tb_transfer_repo,  # mypy: disable-error-code="empty-body"
        settlement_repo,   # mypy: disable-error-code="empty-body"
        logger=None,
    ) -> None:
        self._tb_transfer_repo = tb_transfer_repo
        self._settlement_repo = settlement_repo
        self._log = (logger or log).bind(component="settlement_service")

    async def run_batch(
        self, session: "AsyncSession", batch: SettlementBatch
    ) -> SettlementBatch:
        """Process a settlement batch.

        Intended to reconcile pending settlements by creating the
        corresponding TigerBeetle transfers and updating settlement
        records in PostgreSQL.

        Raises:
            ErrNotImplemented: SettlementRepo has no methods yet; full
                implementation is pending.

        Returns:
            The ``SettlementBatch`` with updated status (when implemented).
        """
        self._log.info(
            "run_batch called",
            batch_id=batch.id,
            status=batch.status,
        )

        raise ErrNotImplemented


def NewSettlementService(
    tb_transfer_repo, settlement_repo, logger=None
) -> SettlementService:
    """Factory — mirrors the Go constructor name."""
    return SettlementService(tb_transfer_repo, settlement_repo, logger)
