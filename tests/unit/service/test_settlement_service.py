"""Unit tests for SettlementService (stub implementation).

Tests verify that the stub raises ErrNotImplemented as expected.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from cbs.domain.errors import ErrNotImplemented
from cbs.service.settlement_service import NewSettlementService, SettlementService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_tb_repo():
    """Create a mock TB transfer repo."""
    return MagicMock()


def _make_mock_settlement_repo():
    """Create a mock PG settlement repo."""
    return MagicMock()


# ---------------------------------------------------------------------------
# SettlementService.run_batch()
# ---------------------------------------------------------------------------

class TestSettlementServiceRunBatch:
    """Tests for ``SettlementService.run_batch()``."""

    async def test_raises_not_implemented(self, mock_session):
        """run_batch raises ErrNotImplemented (stub)."""
        tb_repo = _make_mock_tb_repo()
        settlement_repo = _make_mock_settlement_repo()

        svc = NewSettlementService(tb_repo, settlement_repo)
        batch = MagicMock(id="BATCH-001")

        with pytest.raises(Exception) as exc_info:
            await svc.run_batch(mock_session, batch)
        assert exc_info.value is ErrNotImplemented


# ---------------------------------------------------------------------------
# NewSettlementService factory
# ---------------------------------------------------------------------------

class TestNewSettlementService:
    """Tests for the ``NewSettlementService`` factory."""

    def test_with_all_args(self):
        """Factory with all args returns SettlementService instance."""
        tb_repo = _make_mock_tb_repo()
        settlement_repo = _make_mock_settlement_repo()

        svc = NewSettlementService(tb_repo, settlement_repo)
        assert isinstance(svc, SettlementService)

    def test_without_logger(self):
        """Factory without logger uses default structlog."""
        tb_repo = _make_mock_tb_repo()
        settlement_repo = _make_mock_settlement_repo()

        svc = NewSettlementService(tb_repo, settlement_repo)
        assert svc._log is not None
