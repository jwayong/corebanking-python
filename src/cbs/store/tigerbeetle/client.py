import asyncio
import structlog
from tigerbeetle import Client

log = structlog.get_logger()

class TBClient:
    def __init__(self, addresses: list[str], cluster_id: int = 0):
        self._addresses = addresses
        self._client = Client(cluster_id=cluster_id, replica_addresses=addresses)

    def close(self) -> None:
        """Close the TigerBeetle client connection."""
        self._client.close()

    def addresses(self) -> list[str]:
        """Return the configured replica addresses."""
        return self._addresses

    async def ping(self) -> None:
        """Verify cluster reachability using a no-op request."""
        await asyncio.to_thread(self._client.nop)

    async def create_accounts(self, accounts: list[dict]) -> list[dict]:
        log.debug("tigerbeetle_create_accounts", count=len(accounts))
        return await asyncio.to_thread(self._client.create_accounts, accounts)

    async def create_transfers(self, transfers: list[dict]) -> list[dict]:
        log.debug("tigerbeetle_create_transfers", count=len(transfers))
        return await asyncio.to_thread(self._client.create_transfers, transfers)

    async def lookup_accounts(self, ids: list[bytes]) -> list[dict]:
        log.debug("tigerbeetle_lookup_accounts", count=len(ids))
        return await asyncio.to_thread(self._client.lookup_accounts, ids)

    async def lookup_transfers(self, ids: list[bytes]) -> list[dict]:
        log.debug("tigerbeetle_lookup_transfers", count=len(ids))
        return await asyncio.to_thread(self._client.lookup_transfers, ids)

    async def get_account_transfers(self, account_id: bytes, **kwargs) -> list[dict]:
        log.debug("tigerbeetle_get_account_transfers", account_id=account_id.hex())
        return await asyncio.to_thread(self._client.get_account_transfers, account_id, **kwargs)

    async def get_account_balances(self, account_id: bytes, **kwargs) -> list[dict]:
        log.debug("tigerbeetle_get_account_balances", account_id=account_id.hex())
        return await asyncio.to_thread(self._client.get_account_balances, account_id, **kwargs)
