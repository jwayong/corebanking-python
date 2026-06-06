"""Settlement domain model — batch operations for interbank settlements."""

from __future__ import annotations

from datetime import datetime

import msgspec


class SettlementBatch(msgspec.Struct, frozen=True):
    """Settlement batch representation."""

    id: str
    status: str  # "pending", "completed", "failed"
    created_at: datetime


# Settlement status constants
SETTLEMENT_STATUS_PENDING: str = "pending"
SETTLEMENT_STATUS_COMPLETED: str = "completed"
SETTLEMENT_STATUS_FAILED: str = "failed"
