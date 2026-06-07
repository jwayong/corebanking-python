"""Shared pytest fixtures for TigerBeetle repository unit tests.

All TB repos use the synchronous TB client wrapped with asyncio.to_thread.
We mock the TBClient methods as AsyncMocks to simulate TB responses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_tb_client():
    """Return an AsyncMock configured as a TBClient.

    The returned mock supports:
    - ``await client.create_accounts(accounts)`` → list of result dicts
    - ``await client.lookup_accounts(ids)`` → list of account dicts
    - ``await client.create_transfers(transfers)`` → list of result dicts
    - ``await client.lookup_transfers(ids)`` → list of transfer dicts
    - ``await client.get_account_transfers(account_id, **kwargs)`` → list of transfer dicts
    - ``await client.get_account_balances(account_id, **kwargs)`` → list of balance dicts

    All methods default to returning empty lists.
    Configure with ``client.method.return_value = [...]`` or
    ``client.method.side_effect = [result1, result2, ...]``.
    """
    client = MagicMock(spec=["create_accounts", "lookup_accounts", "create_transfers",
                              "lookup_transfers", "get_account_transfers", "get_account_balances"])

    # Make all methods async.
    client.create_accounts = AsyncMock(return_value=[])
    client.lookup_accounts = AsyncMock(return_value=[])
    client.create_transfers = AsyncMock(return_value=[])
    client.lookup_transfers = AsyncMock(return_value=[])
    client.get_account_transfers = AsyncMock(return_value=[])
    client.get_account_balances = AsyncMock(return_value=[])

    return client


def make_tb_account(tb_id: bytes, ledger: int = 840, flags: int = 5) -> dict:
    """Create a mock TB account dict.

    Args:
        tb_id: 16-byte little-endian Uint128 ID.
        ledger: TigerBeetle ledger number (ISO 4217 numeric code).
        flags: Account flags bitfield.

    Returns:
        Dict matching TB account response format.
    """
    return {
        "id": tb_id,
        "ledger": ledger,
        "flags": flags,
        "user_data_128": b"",
        "debits_posted": 0,
        "credits_posted": 0,
        "debits_pending": 0,
        "credits_pending": 0,
    }


def make_tb_transfer(
    tb_id: bytes,
    debit_account_id: bytes,
    credit_account_id: bytes,
    amount: int = 100_00,
    ledger: int = 840,
    code: int = 1,
    flags: int = 0,
    timestamp: int = 1700000000_000000000,
) -> dict:
    """Create a mock TB transfer dict.

    Args:
        tb_id: 16-byte little-endian Uint128 ID.
        debit_account_id: Debit account TB ID.
        credit_account_id: Credit account TB ID.
        amount: Transfer amount in minor units.
        ledger: TigerBeetle ledger number.
        code: Transfer code (TransferCode enum value).
        flags: Transfer flags bitfield.
        timestamp: Timestamp in nanoseconds.

    Returns:
        Dict matching TB transfer response format.
    """
    return {
        "id": tb_id,
        "debit_account_id": debit_account_id,
        "credit_account_id": credit_account_id,
        "amount": amount,
        "ledger": ledger,
        "code": code,
        "flags": flags,
        "user_data_128": b"",
        "user_data_64": 0,
        "timestamp": timestamp,
    }


def make_create_result(status: int = 0) -> dict:
    """Create a mock TB create result dict.

    Args:
        status: Result status (0=Created, 1=Exists, other=failure).

    Returns:
        Dict matching TB create result format.
    """
    return {"status": status}


def make_tb_balance(
    debits_posted: int = 0,
    credits_posted: int = 0,
    debits_pending: int = 0,
    credits_pending: int = 0,
    timestamp: int = 1700000000_000000000,
) -> dict:
    """Create a mock TB account balance dict.

    Args:
        debits_posted: Cumulative debits posted (Uint128 as int).
        credits_posted: Cumulative credits posted (Uint128 as int).
        debits_pending: Cumulative debits pending (Uint128 as int).
        credits_pending: Cumulative credits pending (Uint128 as int).
        timestamp: Timestamp in nanoseconds.

    Returns:
        Dict matching TB account balance response format.
    """
    return {
        "debits_posted": debits_posted.to_bytes(16, byteorder="little"),
        "credits_posted": credits_posted.to_bytes(16, byteorder="little"),
        "debits_pending": debits_pending.to_bytes(16, byteorder="little"),
        "credits_pending": credits_pending.to_bytes(16, byteorder="little"),
        "timestamp": timestamp,
    }
