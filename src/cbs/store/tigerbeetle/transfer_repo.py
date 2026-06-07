"""TigerBeetle transfer repository — transfer operations against TB.

Mirrors corebanking/internal/store/tigerbeetle/transfer_repo.go.
"""

from __future__ import annotations

# mypy: disable-error-code="type-arg"

import structlog

from cbs.store.tigerbeetle.client import TBClient

log = structlog.get_logger()

# Maximum number of transfers per batch call (TB limit).
MAX_BATCH_SIZE = 8191


class TransferRepo:
    """Handles transfer operations against TigerBeetle.

    Provides async wrappers around the synchronous TB client with
    batch splitting, linked transfer support, and cursor-based pagination.
    """

    def __init__(self, client: TBClient) -> None:
        self._client = client

    async def create_transfers(self, transfers: list[dict]) -> list[dict]:
        """Create one or more transfers in TigerBeetle.

        Automatically splits batches exceeding the TB limit of 8191 transfers
        per call. Returns all results (including errors) so callers can inspect
        each transfer's status for granular error reporting.

        Args:
            transfers: List of TB transfer dicts.

        Returns:
            List of create results from TB.

        Raises:
            ValueError: If no transfers provided or if any transfer fails.
        """
        if not transfers:
            raise ValueError("transfer: no transfers provided")

        all_results: list[dict] = []

        # Split into chunks of MAX_BATCH_SIZE.
        for i in range(0, len(transfers), MAX_BATCH_SIZE):
            chunk = transfers[i : i + MAX_BATCH_SIZE]
            results = await self._client.create_transfers(chunk)
            all_results.extend(results)

        # Check for per-transfer errors.
        for i, result in enumerate(all_results):
            status = result.get("status")
            # TransferCreated=0, TransferExists=1 are acceptable.
            if status not in (0, 1):
                raise ValueError(
                    f"TB transfer[{i}] failed: status={status}, "
                    f"error_code={result.get('error_code')}"
                )

        return all_results

    async def lookup_transfer(self, tb_id: bytes) -> dict | None:
        """Retrieve a single transfer by its TB ID.

        Args:
            tb_id: 16-byte little-endian Uint128 ID.

        Returns:
            Transfer dict if found, ``None`` if not found.
        """
        transfers = await self._client.lookup_transfers([tb_id])

        if not transfers:
            return None

        return transfers[0]

    async def get_account_transfers(
        self,
        tb_id: bytes,
        cursor: bytes | None = None,
        limit: int = 20,
        from_unix_nano: int = 0,
        to_unix_nano: int = 0,
    ) -> list[dict]:
        """Retrieve transfers for an account with cursor-based pagination.

        Results are ordered by timestamp descending (newest first).
        Fetches ``limit + 1`` rows so the caller can detect whether more pages exist.

        Args:
            tb_id: 16-byte little-endian Uint128 account ID.
            cursor: Previous transfer UUIDv7 bytes for pagination (optional).
            limit: Maximum number of transfers to return.
            from_unix_nano: Start of date range filter (nanoseconds, optional).
            to_unix_nano: End of date range filter (nanoseconds, optional).

        Returns:
            List of transfer dicts (may be empty).
        """
        page_limit = limit + 1

        # Build filter dict for the TB client.
        flags = 0x01  # Reversed — descending order (newest first)

        filter_dict = {
            "account_id": tb_id,
            "limit": page_limit,
            "flags": flags,
        }

        # Extract cursor timestamp and TB ID for pagination.
        cursor_ts = _extract_uuidv7_timestamp_nano(cursor) if cursor else 0
        cursor_tb_id = _uuid_to_uint128(cursor) if cursor and len(cursor) == 16 else b""

        result: list[dict] = []
        seen: set[bytes] = set()
        last_page_min_ts = 0

        while True:
            # Set timestamp_max for pagination.
            if not result and cursor_ts > 0:
                filter_dict["timestamp_max"] = cursor_ts  # inclusive — post-filter below
            elif last_page_min_ts > 0:
                filter_dict["timestamp_max"] = last_page_min_ts

            page = await self._client.get_account_transfers(tb_id, **filter_dict)

            if not page:
                break

            # Track the oldest timestamp from the raw page for next pagination step.
            last_page_min_ts = page[-1].get("timestamp", 0)

            # Post-filter: deduplicate, exclude cursor item, apply date range.
            filtered = _post_filter_transfers(
                page, seen, cursor_tb_id, cursor_ts, from_unix_nano, to_unix_nano
            )

            result.extend(filtered)

            # Stop if TB returned fewer than pageLimit (no more pages) or we have enough.
            if len(page) < page_limit or len(result) >= page_limit:
                break

        return result


def _post_filter_transfers(
    transfers: list[dict],
    seen: set[bytes],
    cursor_tb_id: bytes,
    cursor_ts: int,
    from_unix_nano: int,
    to_unix_nano: int,
) -> list[dict]:
    """Apply deduplication, cursor exclusion, and date range filtering.

    Args:
        transfers: Raw page of transfer dicts from TB.
        seen: Set of already-seen transfer IDs (for dedup).
        cursor_tb_id: Cursor TB ID for exclusion.
        cursor_ts: Cursor timestamp for exclusion.
        from_unix_nano: Start of date range filter (nanoseconds).
        to_unix_nano: End of date range filter (nanoseconds).

    Returns:
        Filtered list of transfer dicts.
    """
    filtered: list[dict] = []

    for t in transfers:
        id_bytes = bytes(t.get("id", b""))

        # Skip already-seen items (from previous TB pages with overlapping TimestampMax).
        if id_bytes in seen:
            continue

        # Exclude the cursor item: same timestamp and id >= cursor.
        if cursor_ts > 0 and t.get("timestamp") == cursor_ts:
            if not _uint128_less_than(bytes(t.get("id", b"")), cursor_tb_id):
                continue

        # Date range filter on UserData64 (value_date).
        user_data_64 = int(t.get("user_data_64", 0))
        if from_unix_nano > 0 and user_data_64 < from_unix_nano:
            continue
        if to_unix_nano > 0 and user_data_64 > to_unix_nano:
            continue

        seen.add(id_bytes)
        filtered.append(t)

    return filtered


def _uint128_less_than(a: bytes, b: bytes) -> bool:
    """Return True if a < b numerically.

    Both are TB Uint128 stored in little-endian byte order, so we compare
    from the most significant byte (index 15) down to index 0.

    Args:
        a: First Uint128 (little-endian bytes).
        b: Second Uint128 (little-endian bytes).

    Returns:
        True if a < b.
    """
    for i in range(15, -1, -1):
        if a[i] != b[i]:
            return a[i] < b[i]
    return False  # equal


def _extract_uuidv7_timestamp_nano(b: bytes | None) -> int:
    """Extract the embedded timestamp (in nanoseconds) from a UUIDv7 byte slice.

    UUIDv7 stores a 48-bit Unix timestamp in milliseconds in its first 6 bytes
    (big-endian). Returns 0 if the input is None or too short.

    Args:
        b: UUIDv7 bytes (big-endian) or None.

    Returns:
        Timestamp in nanoseconds, or 0 if input is invalid.
    """
    if not b or len(b) < 6:
        return 0

    ts_ms = (
        int(b[0]) << 40
        | int(b[1]) << 32
        | int(b[2]) << 24
        | int(b[3]) << 16
        | int(b[4]) << 8
        | int(b[5])
    )
    return ts_ms * 1_000_000  # convert milliseconds to nanoseconds


def _uuid_to_uint128(raw: bytes) -> bytes:
    """Convert UUID big-endian bytes to TB little-endian Uint128.

    Each 8-byte half is reversed in the conversion.

    Args:
        raw: 16 bytes in big-endian order (UUID format).

    Returns:
        16 bytes in little-endian order (Uint128 format).
    """
    if len(raw) != 16:
        return b""

    result = bytearray(16)
    for i in range(8):
        result[i] = raw[7 - i]
    for i in range(8):
        result[8 + i] = raw[15 - i]
    return bytes(result)
