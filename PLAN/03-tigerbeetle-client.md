# Issue 03: TigerBeetle Client Wrapper

**Phase:** 1 - Foundation
**Priority:** High
**Labels:** `phase-1`, `foundation`
**Depends on:** #01 (Project Bootstrap)

## Summary

Create a TBClient wrapper around the official `tigerbeetle-python` client.
The wrapper provides a consistent interface and handles the sync-to-async
bridge via `asyncio.to_thread()`.

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/store/__init__.py` | Store package |
| `src/cbs/store/tigerbeetle/__init__.py` | TB store package |
| `src/cbs/store/tigerbeetle/client.py` | TBClient wrapper with async methods |

## Detailed Spec

```python
from tigerbeetle import Client, ClusterCredential

class TBClient:
    def __init__(self, addresses: list[str], cluster_id: int = 0):
        self._client = Client(cluster_id=cluster_id, replica_addresses=addresses)

    async def create_accounts(self, accounts: list[dict]) -> list[dict]:
        return await asyncio.to_thread(self._client.create_accounts, accounts)

    async def create_transfers(self, transfers: list[dict]) -> list[dict]:
        return await asyncio.to_thread(self._client.create_transfers, transfers)

    async def lookup_accounts(self, ids: list[bytes]) -> list[dict]:
        return await asyncio.to_thread(self._client.lookup_accounts, ids)

    async def lookup_transfers(self, ids: list[bytes]) -> list[dict]:
        return await asyncio.to_thread(self._client.lookup_transfers, ids)

    async def get_account_transfers(self, account_id: bytes, **kwargs) -> list[dict]:
        return await asyncio.to_thread(self._client.get_account_transfers, account_id, **kwargs)

    async def get_account_balances(self, account_id: bytes, **kwargs) -> list[dict]:
        return await asyncio.to_thread(self._client.get_account_balances, account_id, **kwargs)
```

**Key decision:** Use `asyncio.to_thread()` (Strategy A from PLAN.md section 5.1)
to wrap all sync TB calls. This prevents blocking the event loop.

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/store/tigerbeetle/client.go` | Go TB client wrapper |

## Acceptance Criteria

- [ ] TBClient connects to a running TigerBeetle instance
- [ ] All methods are async (non-blocking)
- [ ] `create_accounts` and `create_transfers` work correctly
- [ ] `lookup_accounts` and `lookup_transfers` return correct data
- [ ] `get_account_transfers` and `get_account_balances` work with filter kwargs
