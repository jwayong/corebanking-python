"""TigerBeetle account repository — account CRUD operations against TB.

Mirrors corebanking/internal/store/tigerbeetle/account_repo.go.
"""

from __future__ import annotations

# mypy: disable-error-code="type-arg"

import structlog

from cbs.domain.accounts import is_debit_balance
from cbs.store.tigerbeetle.client import TBClient

log = structlog.get_logger()


class AccountRepo:
    """Handles account operations against TigerBeetle.

    Translates between domain AccountCode values and TB account flags,
    and provides async wrappers around the synchronous TB client.
    """

    def __init__(self, client: TBClient) -> None:
        self._client = client

    @staticmethod
    def build_account_flags(code: int) -> int:
        """Build TB account flags from an AccountCode.

        Asset/expense accounts (1000-1999, 5000-5999) use
        ``debits_must_not_exceed_credits``.  Liability/equity/income
        accounts (2000-4999) use ``credits_must_not_exceed_debits``.
        All accounts get the ``history`` flag for balance tracking.

        Args:
            code: The account code (e.g., ``AccountCode.DEPosit_SAVINGS``).

        Returns:
            Integer flag value for the TB ``account_flags`` field.
        """
        flags = 0x04  # history — always enabled

        if is_debit_balance(code):
            flags |= 0x01  # debits_must_not_exceed_credits (asset/expense)
        else:
            flags |= 0x02  # credits_must_not_exceed_debits (liability/equity/income)

        return flags

    async def create_account(self, account: dict[str, object]) -> list[dict]:
        """Create a single account in TigerBeetle.

        Args:
            account: Dict with TB account fields (id, ledger, flags, etc.).

        Returns:
            List of create results from TB.

        Raises:
            ValueError: If creation fails (non-success status).
        """
        results = await self._client.create_accounts([account])

        for result in results:
            status = result.get("status")
            if status != 0 and status != 1:  # AccountCreated=0, AccountExists=1
                raise ValueError(f"TB create account failed: {result}")

        return results

    async def lookup_account(self, tb_id: bytes) -> dict | None:
        """Retrieve a single account by its TB ID.

        Args:
            tb_id: 16-byte little-endian Uint128 ID.

        Returns:
            Account dict if found, ``None`` if not found.
        """
        accounts = await self._client.lookup_accounts([tb_id])

        if not accounts:
            return None

        return accounts[0]

    async def lookup_accounts(self, tb_ids: list[bytes]) -> dict[bytes, dict]:
        """Retrieve multiple accounts by their TB IDs in a single batch call.

        Args:
            tb_ids: List of 16-byte little-endian Uint128 IDs.

        Returns:
            Dict keyed by TB ID bytes for efficient lookup.
        """
        if not tb_ids:
            return {}

        accounts = await self._client.lookup_accounts(tb_ids)

        result: dict[bytes, dict] = {}
        for account in accounts:
            # The TB client returns the id as bytes.
            result[account["id"]] = account

        return result

    async def get_account_balances(
        self, tb_id: bytes, cursor: bytes | None = None, limit: int = 20
    ) -> list[dict]:
        """Retrieve balance snapshots for an account with cursor-based pagination.

        Results are ordered by timestamp descending (newest first).
        Fetches ``limit + 1`` rows so the caller can detect whether more pages exist.

        Args:
            tb_id: 16-byte little-endian Uint128 account ID.
            cursor: Previous transfer UUIDv7 bytes for pagination (optional).
            limit: Maximum number of balance snapshots to return.

        Returns:
            List of account balance dicts (may be empty).
        """
        page_limit = limit + 1

        # Build filter dict for the TB client.
        flags = 0x01  # Reversed — descending order (newest first)

        filter_dict = {
            "account_id": tb_id,
            "limit": page_limit,
            "flags": flags,
        }

        # Extract cursor timestamp for pagination.
        cursor_ts = _extract_uuidv7_timestamp_nano(cursor) if cursor else 0

        result: list[dict] = []
        seen: set[tuple[bytes, bytes]] = set()
        last_page_min_ts = 0

        while True:
            # Set timestamp_max for pagination.
            if not result and cursor_ts > 0:
                filter_dict["timestamp_max"] = cursor_ts
            elif last_page_min_ts > 0:
                filter_dict["timestamp_max"] = last_page_min_ts

            page = await self._client.get_account_balances(tb_id, **filter_dict)

            if not page:
                break

            # Track the oldest timestamp from the raw page for next pagination step.
            last_page_min_ts = page[-1].get("timestamp", 0)

            # Deduplicate overlapping pagination windows.
            filtered = _dedup_balances(page, seen)
            result.extend(filtered)

            # Stop if TB returned fewer than pageLimit (no more pages) or we have enough.
            if len(page) < page_limit or len(result) >= page_limit:
                break

            # Safety: if all items were duplicates, advance past same-timestamp items.
            if not filtered:
                filter_dict["timestamp_max"] = last_page_min_ts - 1

        return result


def _dedup_balances(balances: list[dict], seen: set[tuple[bytes, bytes]]) -> list[dict]:
    """Remove duplicate balance snapshots from overlapping pagination windows.

    Uses composite key of (debits_posted, credits_posted) to detect duplicates.
    """
    filtered: list[dict] = []
    for balance in balances:
        key = (
            bytes(balance.get("debits_posted", b"")),
            bytes(balance.get("credits_posted", b"")),
        )
        if key in seen:
            continue
        seen.add(key)
        filtered.append(balance)
    return filtered


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
