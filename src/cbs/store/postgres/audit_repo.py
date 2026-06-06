"""PostgreSQL repository for audit log and transfer metadata operations.

Mirrors corebanking/internal/store/postgres/audit_repo.go.
"""

from __future__ import annotations

import structlog
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import NoResultFound

from cbs.domain.errors import ErrNotFound  # noqa: F401 — imported for domain error matching
from cbs.store.postgres.database import Database

log = structlog.get_logger()

# Max IDs per batch query to avoid PG parameter size limits.
_MAX_BATCH_SIZE = 100


@dataclass
class TransferMetadataRecord:
    """Row in the transfer_metadata table."""

    tb_transfer_id: bytes = b""
    tb_correlation: bytes | None = None
    account_id: int = 0
    counterparty: str | None = None
    description: str | None = None
    reference: str | None = None
    value_date: date = field(default_factory=date.today)


class AuditRepo:
    """Handles audit log and transfer metadata writes."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_transfer_metadata(
        self, session, rec: TransferMetadataRecord
    ) -> None:
        """Insert transfer metadata into the ``transfer_metadata`` table.

        This is the PG half of the dual-write after a successful TigerBeetle
        transfer.
        """
        await session.execute(
            text(
                "INSERT INTO transfer_metadata "
                "(tb_transfer_id, tb_correlation, account_id, counterparty, description, reference, value_date) "
                "VALUES (:tb_transfer_id, :tb_correlation, :account_id, :counterparty, :description, :reference, :value_date)"
            ),
            {
                "tb_transfer_id": rec.tb_transfer_id,
                "tb_correlation": rec.tb_correlation,
                "account_id": rec.account_id,
                "counterparty": rec.counterparty,
                "description": rec.description,
                "reference": rec.reference,
                "value_date": rec.value_date,
            },
        )

    async def get_by_tb_transfer_id(
        self, session, tb_transfer_id: bytes
    ) -> TransferMetadataRecord | None:
        """Fetch transfer metadata by TigerBeetle transfer ID.

        Returns ``None`` when no metadata exists (not all transfers have
        metadata).  Never raises.
        """
        try:
            result = await session.execute(
                text(
                    "SELECT tb_transfer_id, tb_correlation, account_id, counterparty, "
                    "       description, reference, value_date "
                    "FROM transfer_metadata "
                    "WHERE tb_transfer_id = :tb_transfer_id"
                ),
                {"tb_transfer_id": tb_transfer_id},
            )
            row = result.fetchone()
        except NoResultFound:
            return None

        if row is None:
            return None

        return TransferMetadataRecord(
            tb_transfer_id=row[0],
            tb_correlation=row[1],
            account_id=int(row[2]),
            counterparty=row[3],
            description=row[4],
            reference=row[5],
            value_date=row[6],
        )

    async def get_by_tb_transfer_ids(
        self, session, tb_transfer_ids: list[bytes]
    ) -> dict[str, TransferMetadataRecord]:
        """Batch-fetch transfer metadata for multiple TigerBeetle transfer IDs.

        Returns a dict keyed by UUID string (e.g. ``"550e8400-e29b-41d4-a716-446655440000"``)
        for efficient lookup by the service layer.

        Splits large batches into chunks of :data:`_MAX_BATCH_SIZE` to avoid
        PG query size limits.  Uses ``= ANY($1)`` with a bytea[] parameter.

        Returns an empty dict when *tb_transfer_ids* is empty (never ``None``).
        """
        if not tb_transfer_ids:
            return {}

        result: dict[str, TransferMetadataRecord] = {}

        for i in range(0, len(tb_transfer_ids), _MAX_BATCH_SIZE):
            batch = tb_transfer_ids[i : i + _MAX_BATCH_SIZE]

            try:
                rows_result = await session.execute(
                    text(
                        "SELECT tb_transfer_id, tb_correlation, account_id, counterparty, "
                        "       description, reference, value_date "
                        "FROM transfer_metadata "
                        "WHERE tb_transfer_id = ANY(:ids)"
                    ),
                    {"ids": batch},
                )
            except NoResultFound:
                continue

            for row in rows_result.fetchall():
                rec = TransferMetadataRecord(
                    tb_transfer_id=row[0],
                    tb_correlation=row[1],
                    account_id=int(row[2]),
                    counterparty=row[3],
                    description=row[4],
                    reference=row[5],
                    value_date=row[6],
                )
                key = _uuid.UUID(bytes=rec.tb_transfer_id).hex
                # Format as standard UUID string with hyphens.
                key = f"{key[0:8]}-{key[8:12]}-{key[12:16]}-{key[16:20]}-{key[20:]}"
                result[key] = rec

        return result
