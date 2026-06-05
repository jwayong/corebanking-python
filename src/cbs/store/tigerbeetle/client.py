import asyncio
from tigerbeetle import Client

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
