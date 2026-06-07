"""Tests for TigerBeetle transfer repository."""

import pytest

from cbs.store.tigerbeetle.transfer_repo import (
    TransferRepo,
    MAX_BATCH_SIZE,
    _extract_uuidv7_timestamp_nano,
    _post_filter_transfers,
    _uint128_less_than,
    _uuid_to_uint128,
)


class TestCreateTransfers:
    """Test transfer creation."""

    @pytest.mark.asyncio
    async def test_create_transfers_success(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        transfer = {"id": b"\x01" * 16, "amount": 100}
        mock_tb_client.create_transfers.return_value = [{"status": 0}]

        result = await repo.create_transfers([transfer])

        assert len(result) == 1
        assert result[0]["status"] == 0

    @pytest.mark.asyncio
    async def test_create_transfers_exists(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        transfer = {"id": b"\x01" * 16, "amount": 100}
        mock_tb_client.create_transfers.return_value = [{"status": 1}]  # TransferExists

        result = await repo.create_transfers([transfer])

        assert len(result) == 1
        # TransferExists (status=1) is acceptable.

    @pytest.mark.asyncio
    async def test_create_transfers_failure(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        transfer = {"id": b"\x01" * 16, "amount": 100}
        mock_tb_client.create_transfers.return_value = [{"status": 2, "error_code": 10}]

        with pytest.raises(ValueError, match="TB transfer\\[0\\] failed"):
            await repo.create_transfers([transfer])

    @pytest.mark.asyncio
    async def test_create_transfers_empty_raises(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        with pytest.raises(ValueError, match="no transfers provided"):
            await repo.create_transfers([])

    @pytest.mark.asyncio
    async def test_create_transfers_batch_split(self, mock_tb_client):
        """Large batches are split into chunks of MAX_BATCH_SIZE."""
        repo = TransferRepo(mock_tb_client)

        # Create more transfers than MAX_BATCH_SIZE.
        count = MAX_BATCH_SIZE + 100
        transfers = [{"id": bytes([i % 256] * 16), "amount": 100} for i in range(count)]

        # Return success for each chunk.
        def mock_create(chunk):
            return [{"status": 0} for _ in chunk]

        mock_tb_client.create_transfers.side_effect = mock_create

        result = await repo.create_transfers(transfers)

        assert len(result) == count
        # Should have been called in two batches.
        assert mock_tb_client.create_transfers.call_count == 2


class TestLookupTransfer:
    """Test transfer lookup."""

    @pytest.mark.asyncio
    async def test_lookup_transfer_found(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        from tests.unit.store.tigerbeetle.conftest import make_tb_transfer

        tb_id = b"\x01" * 16
        transfer = make_tb_transfer(tb_id, b"\x02" * 16, b"\x03" * 16)
        mock_tb_client.lookup_transfers.return_value = [transfer]

        result = await repo.lookup_transfer(tb_id)

        assert result is not None
        assert result["id"] == tb_id

    @pytest.mark.asyncio
    async def test_lookup_transfer_not_found(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        tb_id = b"\x01" * 16
        mock_tb_client.lookup_transfers.return_value = []

        result = await repo.lookup_transfer(tb_id)

        assert result is None


class TestUint128LessThan:
    """Test Uint128 comparison."""

    def test_less_than_true(self):
        # Little-endian: MSB at index 15. a=0, b=1 → a < b.
        a = bytes(16)  # all zeros
        b = bytes([0] * 15 + [1])  # MSB = 1

        assert _uint128_less_than(a, b) is True

    def test_less_than_false(self):
        # a=1, b=0 → a > b.
        a = bytes([0] * 15 + [1])
        b = bytes(16)

        assert _uint128_less_than(a, b) is False

    def test_equal_returns_false(self):
        a = bytes(16)
        b = bytes(16)

        assert _uint128_less_than(a, b) is False


class TestUUIDToUint128:
    """Test UUID to Uint128 conversion."""

    def test_uuid_to_uint128_reverses_halves(self):
        # Big-endian UUID: [0..7] = time_high, [8..15] = clock_seq + node
        uuid_bytes = bytes(range(16))  # 0x00, 0x01, ..., 0x0F

        result = _uuid_to_uint128(uuid_bytes)

        # First 8 bytes reversed: [0x07, 0x06, ..., 0x00]
        assert result[:8] == bytes(range(7, -1, -1))
        # Second 8 bytes reversed: [0x0F, 0x0E, ..., 0x08]
        assert result[8:] == bytes(range(15, 7, -1))

    def test_short_input_returns_empty(self):
        result = _uuid_to_uint128(b"\x01\x02")

        assert result == b""


class TestPostFilterTransfers:
    """Test transfer post-filtering."""

    def test_deduplication(self):
        from tests.unit.store.tigerbeetle.conftest import make_tb_transfer

        id1 = b"\x01" * 16
        t1 = make_tb_transfer(id1, b"\x02" * 16, b"\x03" * 16)
        t2 = make_tb_transfer(id1, b"\x02" * 16, b"\x03" * 16)  # duplicate

        seen: set[bytes] = set()
        filtered = _post_filter_transfers([t1, t2], seen, b"", 0, 0, 0)

        assert len(filtered) == 1
        assert filtered[0] is t1

    def test_cursor_exclusion(self):
        from tests.unit.store.tigerbeetle.conftest import make_tb_transfer

        # Create a cursor UUID (big-endian).
        cursor_uuid = bytes([0x01, 0x95, 0x00, 0x00, 0x00, 0x00] + [0] * 10)
        cursor_tb_id = _uuid_to_uint128(cursor_uuid)
        cursor_ts = 1700000000_000000000

        # Transfer with same timestamp and ID >= cursor (in TB format).
        # The transfer's id is already in TB format. Use the cursor's TB ID directly.
        t1 = make_tb_transfer(
            cursor_tb_id, b"\x02" * 16, b"\x03" * 16,
            timestamp=cursor_ts
        )

        seen: set[bytes] = set()
        filtered = _post_filter_transfers([t1], seen, cursor_tb_id, cursor_ts, 0, 0)

        assert len(filtered) == 0  # Cursor item excluded (id >= cursor).

    def test_date_range_filter(self):
        from tests.unit.store.tigerbeetle.conftest import make_tb_transfer

        id1 = b"\x01" * 16
        # Transfer with value_date outside range.
        t1 = make_tb_transfer(id1, b"\x02" * 16, b"\x03" * 16)
        t1["user_data_64"] = 500  # Before from_unix_nano.

        seen: set[bytes] = set()
        filtered = _post_filter_transfers([t1], seen, b"", 0, 1000, 2000)

        assert len(filtered) == 0  # Filtered out by date range.


class TestGetAccountTransfers:
    """Test account transfer retrieval with pagination."""

    @pytest.mark.asyncio
    async def test_get_transfers_single_page(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        from tests.unit.store.tigerbeetle.conftest import make_tb_transfer

        tb_id = b"\x01" * 16
        transfers = [
            make_tb_transfer(b"\x02" * 16, b"\x03" * 16, b"\x04" * 16),
            make_tb_transfer(b"\x03" * 16, b"\x05" * 16, b"\x06" * 16),
        ]
        mock_tb_client.get_account_transfers.return_value = transfers

        result = await repo.get_account_transfers(tb_id, limit=10)

        assert len(result) == 2
        assert mock_tb_client.get_account_transfers.call_count == 1

    @pytest.mark.asyncio
    async def test_get_transfers_with_cursor(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        from tests.unit.store.tigerbeetle.conftest import make_tb_transfer

        tb_id = b"\x01" * 16
        cursor = bytes([0x01, 0x95, 0x00, 0x00, 0x00, 0x00] + [0] * 10)

        transfers = [
            make_tb_transfer(b"\x02" * 16, b"\x03" * 16, b"\x04" * 16),
        ]
        mock_tb_client.get_account_transfers.return_value = transfers

        result = await repo.get_account_transfers(tb_id, cursor=cursor, limit=10)

        assert len(result) == 1
        # Verify timestamp_max was set from cursor.
        call_kwargs = mock_tb_client.get_account_transfers.call_args[1]
        assert "timestamp_max" in call_kwargs

    @pytest.mark.asyncio
    async def test_get_transfers_with_date_range(self, mock_tb_client):
        repo = TransferRepo(mock_tb_client)

        from tests.unit.store.tigerbeetle.conftest import make_tb_transfer

        tb_id = b"\x01" * 16
        transfers = [
            make_tb_transfer(b"\x02" * 16, b"\x03" * 16, b"\x04" * 16),
        ]
        transfers[0]["user_data_64"] = 1500  # Within range.
        mock_tb_client.get_account_transfers.return_value = transfers

        result = await repo.get_account_transfers(
            tb_id, limit=10, from_unix_nano=1000, to_unix_nano=2000
        )

        assert len(result) == 1


class TestExtractUUIDV7Timestamp:
    """Test UUIDv7 timestamp extraction."""

    def test_valid_uuidv7_bytes(self):
        b = bytes([0x01, 0x95, 0x00, 0x00, 0x00, 0x00])
        ts_nano = _extract_uuidv7_timestamp_nano(b)

        expected_ms = 0x019500000000
        assert ts_nano == expected_ms * 1_000_000

    def test_none_input(self):
        assert _extract_uuidv7_timestamp_nano(None) == 0

    def test_short_input(self):
        assert _extract_uuidv7_timestamp_nano(b"\x01\x02") == 0
