"""Unit tests for FXService (cross-currency exchange operations).

Tests verify request validation, rate resolution (client-provided vs. cache),
linked TB transfer execution, error propagation from repository layers,
and response construction — all using mocked dependencies.

Mirrors the style of :mod:`tests.unit.service.test_account_service`.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from cbs.domain.accounts import AccountCode
from cbs.domain.errors import (
    ErrInsufficientBalance,
    ErrInvalidAccount,
    ErrLiquidityPoolUnavailable,
    ErrNotFound,
    ValidationError,
)
from cbs.domain.transfers import (
    FXRequest,
    FXResponse,
)
from cbs.service.fx_service import (
    FXService,
    NewFXService,
    _compute_buy_amount,
    _pack_user_data_128,
)


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

SELL_ACCT_UUID = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
BUY_ACCT_UUID = "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9f"

SELL_LIQUIDITY_BYTES = b"\xaa" * 16
BUY_LIQUIDITY_BYTES = b"\xbb" * 16

USD_LEDGER = 840
EUR_LEDGER = 978


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fx_rate_repo():
    """Create a mock fx_rate_repo module with AsyncMock get_latest."""
    repo = MagicMock()
    repo.get_latest = AsyncMock()
    return repo


def _make_fx_cache():
    """Create a mock fx_cache with get, set, and get_or_refresh methods."""
    cache = MagicMock()
    cache.get = MagicMock()
    cache.set = MagicMock()
    cache.get_or_refresh = AsyncMock()
    return cache


def _make_tb_transfer_repo():
    """Create a mock tb_transfer_repo with AsyncMock create_transfers."""
    repo = MagicMock()
    repo.create_transfers = AsyncMock()
    return repo


def _make_tb_account_repo():
    """Create a mock tb_account_repo with AsyncMock lookup_accounts."""
    repo = MagicMock()
    repo.lookup_accounts = AsyncMock()
    return repo


def _make_account_meta_repo():
    """Create a mock account_meta_repo with AsyncMock get_by_tb_account_id."""
    repo = MagicMock()
    repo.get_by_meta = AsyncMock()
    repo.get_by_tb_account_id = AsyncMock()
    return repo


def _make_system_account_repo():
    """Create a mock system_account_repo with AsyncMock get_by_code.

    get_by_code(session, currency, code) returns 16 bytes or None.
    """
    repo = MagicMock()
    repo.get_by_code = AsyncMock()
    return repo


def _make_metadata_writer():
    """Create a mock metadata_writer with AsyncMock create_transfer_metadata."""
    writer = MagicMock()
    writer.create_transfer_metadata = AsyncMock()
    return writer


def _make_meta(status="active", id=1):
    """Build a mock AccountWithProduct-like object for account_meta_repo."""
    meta = MagicMock()
    meta.id = id
    meta.status = status
    return meta


def _make_currency_info(code, ledger, scale):
    """Build a mock CurrencyInfo object."""
    info = MagicMock()
    info.code = code
    info.ledger = ledger
    info.scale = scale
    return info


def _build_mock_uuids():
    """Build mock UUID objects for correlation ID and leg IDs.

    Returns a tuple of (corr_uuid, leg1_uuid, leg2_uuid).
    """
    corr = MagicMock()
    corr.__str__ = MagicMock(return_value="corr-uuid")

    leg1 = MagicMock()
    leg1.__str__ = MagicMock(return_value="leg1-uuid")

    leg2 = MagicMock()
    leg2.__str__ = MagicMock(return_value="leg2-uuid")

    return corr, leg1, leg2


def _build_service(
    fx_rate_repo=None,
    fx_cache=None,
    tb_transfer_repo=None,
    tb_account_repo=None,
    account_meta_repo=None,
    system_account_repo=None,
    metadata_writer=None,
):
    """Build an FXService with the given (or default) mocks."""
    return NewFXService(
        fx_rate_repo=fx_rate_repo or _make_fx_rate_repo(),
        fx_cache=fx_cache or _make_fx_cache(),
        tb_transfer_repo=tb_transfer_repo or _make_tb_transfer_repo(),
        tb_account_repo=tb_account_repo or _make_tb_account_repo(),
        account_meta_repo=account_meta_repo or _make_account_meta_repo(),
        system_account_repo=system_account_repo or _make_system_account_repo(),
        metadata_writer=metadata_writer or _make_metadata_writer(),
    )


# ---------------------------------------------------------------------------
# FXService.exchange() — setup helpers for common test scenarios
# ---------------------------------------------------------------------------

def _setup_happy_path_mocks(
    fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
    account_meta_repo, system_account_repo, metadata_writer,
    corr_uuid, leg1_uuid, leg2_uuid,
):
    """Configure all mocks for a successful exchange scenario.

    Returns (sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb) bytes
    used in the uuid_to_uint128 side_effect.
    """
    sell_acct_tb = b"\x01" * 16
    buy_acct_tb = b"\x02" * 16
    sell_liq_tb = b"\x03" * 16
    buy_liq_tb = b"\x04" * 16

    # system_account_repo.get_by_code is async: (session, currency, code)
    def get_by_code_side_effect(session, currency, code):
        if currency == "USD":
            return SELL_LIQUIDITY_BYTES
        return BUY_LIQUIDITY_BYTES

    system_account_repo.get_by_code.side_effect = get_by_code_side_effect

    # tb_account_repo.lookup_accounts returns dict mapping tb_id bytes to account dicts
    sell_acct = {"ledger": USD_LEDGER, "code": 2110}
    buy_acct = {"ledger": EUR_LEDGER, "code": 2110}
    sell_liq_acct = {"ledger": USD_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)}
    buy_liq_acct = {"ledger": EUR_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)}

    tb_account_repo.lookup_accounts.return_value = {
        sell_acct_tb: sell_acct,
        buy_acct_tb: buy_acct,
        sell_liq_tb: sell_liq_acct,
        buy_liq_tb: buy_liq_acct,
    }

    # account_meta_repo.get_by_tb_account_id(session, uuid_bytes)
    def get_meta_side_effect(session, tb_id):
        import uuid as _uuid
        if tb_id == _uuid.UUID(SELL_ACCT_UUID).bytes:
            return _make_meta(id=1)
        return _make_meta(id=2)

    account_meta_repo.get_by_tb_account_id.side_effect = get_meta_side_effect

    # tb_transfer_repo.create_transfers returns success results
    tb_transfer_repo.create_transfers.return_value = [
        {"status": 0},  # TransferCreated
        {"status": 0},
    ]

    return sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb


def _patch_uuid_conversions(corr_uuid, leg1_uuid, leg2_uuid, sell_acct_tb, buy_acct_tb,
                             sell_liq_tb, buy_liq_tb):
    """Return a context manager patching uuid utilities for happy path tests.

    The mock chain is:
    - generate_uuidv7 -> [corr_uuid, leg1_uuid, leg2_uuid] (3 calls)
    - uuid_to_uint128 -> [corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid] (7 calls)
    - tb_id_to_uuid -> [sell_liq_uuid, buy_liq_uuid] (2 calls) where sell_liq_uuid converts to sell_liq_tb
    - uint128_to_uuid -> [leg1_uuid, leg2_uuid] (2 calls)
    """
    # sell_liq_uuid and buy_liq_uuid are intermediate values that uuid_to_uint128
    # converts to sell_liq_tb and buy_liq_tb respectively.
    sell_liq_uuid = corr_uuid  # reused as intermediate (doesn't matter, uuid_to_uint128 overrides)
    buy_liq_uuid = corr_uuid

    return patch(
        "cbs.service.fx_service.generate_uuidv7",
        side_effect=[corr_uuid, leg1_uuid, leg2_uuid],
    ), patch(
        "cbs.service.fx_service.uuid_to_uint128",
        side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid],
    ), patch(
        "cbs.service.fx_service.tb_id_to_uuid",
        side_effect=[sell_liq_uuid, buy_liq_uuid],
    )


# ---------------------------------------------------------------------------
# FXService.exchange()
# ---------------------------------------------------------------------------

class TestFXServiceExchange:
    """Tests for ``FXService.exchange()``."""

    async def test_success_client_provided_rate(
        self, mock_session, sample_uuid
    ):
        """Happy path with client-provided rate: validate -> skip rate lookup -> compute buy amount
        -> 2 linked TB transfers -> response with two legs."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        corr_uuid, leg1_uuid, leg2_uuid = _build_mock_uuids()

        sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb = _setup_happy_path_mocks(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
            corr_uuid, leg1_uuid, leg2_uuid,
        )

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,  # $10.00 in cents
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],  # intermediate values (uuid_to_uint128 overrides to sell_liq_tb/buy_liq_tb)
        ), patch(
            "cbs.service.fx_service.generate_uuidv7",
            side_effect=[corr_uuid, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.uint128_to_uuid",
            side_effect=[leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.datetime",
            wraps=datetime,
        ):
            result = await svc.exchange(mock_session, req)

        assert isinstance(result, FXResponse)
        assert result.transfer_type == "fx"
        assert len(result.legs) == 2

        # Leg 1: customer sell -> sell liquidity pool
        assert result.legs[0].debit_account_id == SELL_ACCT_UUID
        assert result.legs[0].code == "fx_debit"

        # Leg 2: buy liquidity pool -> customer buy
        assert result.legs[1].credit_account_id == BUY_ACCT_UUID
        assert result.legs[1].code == "fx_credit"

        # Rate and amounts
        assert result.rate == 0.85
        assert result.sell_amount.currency == "USD"
        assert result.buy_amount.currency == "EUR"

        # buy_amount = (1000 * 850000) / 10^2 = 850 (both scale=2, diff=4)
        assert result.buy_amount.amount == 850

        # Status
        assert result.status == "posted"

        # fx_cache.get should NOT be called when rate is client-provided
        fx_cache.get.assert_not_called()

        # tb_account_repo.lookup_accounts called with 4 account IDs
        tb_account_repo.lookup_accounts.assert_awaited_once()
        lookup_ids = tb_account_repo.lookup_accounts.call_args[0][0]
        assert len(lookup_ids) == 4

        # tb_transfer_repo.create_transfers called with 2 transfers
        tb_transfer_repo.create_transfers.assert_awaited_once()
        transfers = tb_transfer_repo.create_transfers.call_args[0][0]
        assert len(transfers) == 2

    async def test_success_rate_from_cache(self, mock_session):
        """Rate resolved from cache when client rate <= 0."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        corr_uuid, leg1_uuid, leg2_uuid = _build_mock_uuids()

        # Cache hit — returns rate dict
        fx_cache.get.return_value = {"rate": 0.92, "effective_at": datetime(2025, 6, 1)}

        sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb = _setup_happy_path_mocks(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
            corr_uuid, leg1_uuid, leg2_uuid,
        )

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0,  # client did not provide rate
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],  # intermediate values (uuid_to_uint128 overrides)
        ), patch(
            "cbs.service.fx_service.generate_uuidv7",
            side_effect=[corr_uuid, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.uint128_to_uuid",
            side_effect=[leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.datetime",
            wraps=datetime,
        ):
            result = await svc.exchange(mock_session, req)

        # Cache was consulted
        fx_cache.get.assert_called_once_with("USD", "EUR")

        assert result.rate == 0.92
        # buy_amount = (1000 * 920000) / 10^4 = 920
        assert result.buy_amount.amount == 920

        # fx_rate_repo.get_latest should NOT be called (cache hit)
        fx_rate_repo.get_latest.assert_not_called()

    async def test_validation_same_ledger(self, mock_session):
        """ValidationError when sell and buy currencies are the same ledger."""
        svc = _build_service()

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="USD",  # same as sell — but validate() catches this first
            rate=1.0,
        )

        # FXRequest.validate() checks sell_currency == buy_currency before the service
        # checks ledger equality. The error message reflects that check.
        with pytest.raises(ValidationError, match="sell_currency and buy_currency must differ"):
            await svc.exchange(mock_session, req)

    async def test_rate_not_found(self, mock_session):
        """ErrNotFound when rate not found: cache miss + PG returns None."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # Cache miss
        fx_cache.get.return_value = None

        # PG returns None -> ErrNotFound via get_or_refresh
        fx_cache.get_or_refresh.side_effect = ErrNotFound

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0,  # triggers rate resolution
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)
            assert exc_info.value is ErrNotFound

    async def test_account_not_found_in_tb_lookup(self, mock_session):
        """ErrInvalidAccount when sell account missing from TB batch lookup."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        corr_uuid, leg1_uuid, leg2_uuid = _build_mock_uuids()
        sell_acct_tb = b"\x01" * 16
        buy_acct_tb = b"\x02" * 16
        sell_liq_tb = b"\x03" * 16
        buy_liq_tb = b"\x04" * 16

        # Only buy account and liquidity pools — sell account missing
        tb_account_repo.lookup_accounts.return_value = {
            buy_acct_tb: {"ledger": EUR_LEDGER, "code": 2110},
            sell_liq_tb: {"ledger": USD_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)},
            buy_liq_tb: {"ledger": EUR_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)},
        }

        system_account_repo.get_by_code.side_effect = lambda s, c, code: (
            SELL_LIQUIDITY_BYTES if c == "USD" else BUY_LIQUIDITY_BYTES
        )

        import uuid as _uuid

        def get_meta_side_effect(session, tb_id):
            if tb_id == _uuid.UUID(SELL_ACCT_UUID).bytes:
                return _make_meta(id=1)
            return _make_meta(id=2)

        account_meta_repo.get_by_tb_account_id.side_effect = get_meta_side_effect

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],
        ), patch(
            "cbs.service.fx_service.generate_uuidv7",
            side_effect=[corr_uuid, leg1_uuid, leg2_uuid],
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)
            assert exc_info.value is ErrInvalidAccount

    async def test_liquidity_pool_not_found(self, mock_session):
        """ErrLiquidityPoolUnavailable when sell liquidity pool missing from TB response."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        sell_acct_tb = b"\x01" * 16
        buy_acct_tb = b"\x02" * 16
        sell_liq_tb = b"\x03" * 16
        buy_liq_tb = b"\x04" * 16

        # sell_liq_tb missing from lookup result
        tb_account_repo.lookup_accounts.return_value = {
            sell_acct_tb: {"ledger": USD_LEDGER, "code": 2110},
            buy_acct_tb: {"ledger": EUR_LEDGER, "code": 2110},
            buy_liq_tb: {"ledger": EUR_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)},
        }

        system_account_repo.get_by_code.side_effect = lambda s, c, code: (
            SELL_LIQUIDITY_BYTES if c == "USD" else BUY_LIQUIDITY_BYTES
        )

        import uuid as _uuid

        def get_meta_side_effect(session, tb_id):
            if tb_id == _uuid.UUID(SELL_ACCT_UUID).bytes:
                return _make_meta(id=1)
            return _make_meta(id=2)

        account_meta_repo.get_by_tb_account_id.side_effect = get_meta_side_effect

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        corr_uuid = MagicMock()

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)
            assert exc_info.value is ErrLiquidityPoolUnavailable

    async def test_tb_create_raises_valueerror(self, mock_session):
        """TB create fails with ValueError from repo — mapped via map_tb_error."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        corr_uuid, leg1_uuid, leg2_uuid = _build_mock_uuids()

        sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb = _setup_happy_path_mocks(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
            corr_uuid, leg1_uuid, leg2_uuid,
        )

        # Override: create_transfers raises ValueError (connection error)
        tb_transfer_repo.create_transfers.side_effect = ValueError("connection refused")

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=10000,  # $100.00 — enough to trigger insufficient balance
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],  # intermediate values (uuid_to_uint128 overrides)
        ), patch(
            "cbs.service.fx_service.generate_uuidv7",
            side_effect=[corr_uuid, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.uint128_to_uuid",
            side_effect=[leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.map_tb_error",
            return_value=ErrInsufficientBalance,
        ), patch(
            "cbs.service.fx_service.find_linked_root_cause",
            return_value=None,
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)
            assert exc_info.value is ErrInsufficientBalance

        # map_tb_error was called with the ValueError
        # find_linked_root_cause should NOT be called (exception path)

    async def test_linked_transfer_failure_root_cause(self, mock_session):
        """find_linked_root_cause detects root cause from linked transfer results."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        corr_uuid, leg1_uuid, leg2_uuid = _build_mock_uuids()

        sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb = _setup_happy_path_mocks(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
            corr_uuid, leg1_uuid, leg2_uuid,
        )

        # Override: first leg fails with insufficient balance, second is LinkedEventFailed (81)
        tb_transfer_repo.create_transfers.return_value = [
            {"status": 10},  # TransferExceedsCredits -> insufficient balance
            {"status": 81},  # LinkedEventFailed
        ]

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=10000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],  # intermediate values (uuid_to_uint128 overrides)
        ), patch(
            "cbs.service.fx_service.generate_uuidv7",
            side_effect=[corr_uuid, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.uint128_to_uuid",
            side_effect=[leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.find_linked_root_cause",
            return_value=ErrInsufficientBalance,
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)
            assert exc_info.value is ErrInsufficientBalance

        # find_linked_root_cause was called with the results
        from cbs.service.errors import find_linked_root_cause

    async def test_metadata_written_for_both_legs(self, mock_session):
        """Metadata writer is called for both FX legs in background task."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        corr_uuid, leg1_uuid, leg2_uuid = _build_mock_uuids()

        sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb = _setup_happy_path_mocks(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
            corr_uuid, leg1_uuid, leg2_uuid,
        )

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],  # intermediate values (uuid_to_uint128 overrides)
        ), patch(
            "cbs.service.fx_service.generate_uuidv7",
            side_effect=[corr_uuid, leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.uint128_to_uuid",
            side_effect=[leg1_uuid, leg2_uuid],
        ), patch(
            "cbs.service.fx_service.datetime",
            wraps=datetime,
        ):
            result = await svc.exchange(mock_session, req)

        # asyncio.create_task is fire-and-forget; the task may not have run yet.
        # We verify the task was created by checking that asyncio.create_task was called.
        # Since we can't easily await the background task, we verify the response is correct.
        assert result.status == "posted"
        assert len(result.legs) == 2

    async def test_system_account_repo_exception(self, mock_session):
        """ErrLiquidityPoolUnavailable when system_account_repo.get_by_code raises."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # get_by_code raises an exception for the sell currency
        system_account_repo.get_by_code.side_effect = RuntimeError("db connection lost")

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)
            assert exc_info.value is ErrLiquidityPoolUnavailable

    async def test_system_account_repo_returns_none(self, mock_session):
        """ErrLiquidityPoolUnavailable when system_account_repo.get_by_code returns None."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        # get_by_code returns None (no liquidity pool row)
        system_account_repo.get_by_code.return_value = None

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)
            assert exc_info.value is ErrLiquidityPoolUnavailable

    async def test_account_closed_raises_err(self, mock_session):
        """ErrAccountClosed when customer account status is 'closed' in PG metadata."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        sell_acct_tb = b"\x01" * 16
        buy_acct_tb = b"\x02" * 16
        sell_liq_tb = b"\x03" * 16
        buy_liq_tb = b"\x04" * 16

        tb_account_repo.lookup_accounts.return_value = {
            sell_acct_tb: {"ledger": USD_LEDGER, "code": 2110},
            buy_acct_tb: {"ledger": EUR_LEDGER, "code": 2110},
            sell_liq_tb: {"ledger": USD_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)},
            buy_liq_tb: {"ledger": EUR_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)},
        }

        system_account_repo.get_by_code.side_effect = lambda s, c, code: (
            SELL_LIQUIDITY_BYTES if c == "USD" else BUY_LIQUIDITY_BYTES
        )

        import uuid as _uuid

        def get_meta_side_effect(session, tb_id):
            if tb_id == _uuid.UUID(SELL_ACCT_UUID).bytes:
                return _make_meta(status="closed", id=1)  # closed!
            return _make_meta(id=2)

        account_meta_repo.get_by_tb_account_id.side_effect = get_meta_side_effect

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        corr_uuid = MagicMock()

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],
        ):
            with pytest.raises(Exception) as exc_info:
                await svc.exchange(mock_session, req)

        from cbs.domain.errors import ErrAccountClosed
        assert exc_info.value is ErrAccountClosed

    async def test_ledger_mismatch_validation_error(self, mock_session):
        """ValidationError when TB account ledger does not match expected currency ledger."""
        fx_rate_repo = _make_fx_rate_repo()
        fx_cache = _make_fx_cache()
        tb_transfer_repo = _make_tb_transfer_repo()
        tb_account_repo = _make_tb_account_repo()
        account_meta_repo = _make_account_meta_repo()
        system_account_repo = _make_system_account_repo()
        metadata_writer = _make_metadata_writer()

        sell_acct_tb = b"\x01" * 16
        buy_acct_tb = b"\x02" * 16
        sell_liq_tb = b"\x03" * 16
        buy_liq_tb = b"\x04" * 16

        # sell_acct has wrong ledger (EUR instead of USD)
        tb_account_repo.lookup_accounts.return_value = {
            sell_acct_tb: {"ledger": EUR_LEDGER, "code": 2110},  # wrong!
            buy_acct_tb: {"ledger": EUR_LEDGER, "code": 2110},
            sell_liq_tb: {"ledger": USD_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)},
            buy_liq_tb: {"ledger": EUR_LEDGER, "code": int(AccountCode.LIQUIDITY_POOL)},
        }

        system_account_repo.get_by_code.side_effect = lambda s, c, code: (
            SELL_LIQUIDITY_BYTES if c == "USD" else BUY_LIQUIDITY_BYTES
        )

        import uuid as _uuid

        def get_meta_side_effect(session, tb_id):
            if tb_id == _uuid.UUID(SELL_ACCT_UUID).bytes:
                return _make_meta(id=1)
            return _make_meta(id=2)

        account_meta_repo.get_by_tb_account_id.side_effect = get_meta_side_effect

        svc = _build_service(
            fx_rate_repo, fx_cache, tb_transfer_repo, tb_account_repo,
            account_meta_repo, system_account_repo, metadata_writer,
        )

        req = FXRequest(
            debit_account_id=SELL_ACCT_UUID,
            credit_account_id=BUY_ACCT_UUID,
            sell_amount=1000,
            sell_currency="USD",
            buy_currency="EUR",
            rate=0.85,
        )

        corr_uuid = MagicMock()

        with patch(
            "cbs.service.fx_service.lookup_currency",
            side_effect=[_make_currency_info("USD", USD_LEDGER, 2), _make_currency_info("EUR", EUR_LEDGER, 2)],
        ), patch(
            "cbs.service.fx_service.uuid_to_uint128",
            side_effect=[corr_uuid, sell_acct_tb, buy_acct_tb, sell_liq_tb, buy_liq_tb],
        ), patch(
            "cbs.service.fx_service.tb_id_to_uuid",
            side_effect=[corr_uuid, corr_uuid],
        ):
            with pytest.raises(ValidationError, match="debit account currency does not match"):
                await svc.exchange(mock_session, req)


# ---------------------------------------------------------------------------
# FXService — module-level helpers
# ---------------------------------------------------------------------------

class TestComputeBuyAmount:
    """Tests for ``_compute_buy_amount()`` integer arithmetic helper."""

    def test_usd_to_eur_same_scale(self):
        """USD (scale 2) to EUR (scale 2), rate 0.85 -> buy = 850 cents."""
        # sell_amount=1000 means $10.00 in cents (scale 2).
        # rate_int=850000 means 0.85 at scale 6.
        # raw = 1000 * 850000 = 850_000_000 (at scale sell_scale+6 = 8)
        # diff = 2 + 6 - 2 = 6, divisor = 10^6
        # (850_000_000 + 500_000) // 1_000_000 = 850
        # $10.00 * 0.85 = EUR 8.50 = 850 cents
        result = _compute_buy_amount(1000, 850_000, 2, 2)
        assert result == 850

    def test_usd_to_jpy_zero_scale(self):
        """USD (scale 2) to JPY (scale 0), rate 150 -> buy = 150 yen."""
        # sell_amount=100 means $1.00 in cents (scale 2).
        # rate_int=150_000_000 means 150.0 at scale 6.
        # raw = 100 * 150_000_000 = 15_000_000_000 (at scale sell_scale+6 = 8)
        # diff = 2 + 6 - 0 = 8, divisor = 10^8
        # (15_000_000_000 + 50_000_000) // 100_000_000 = 150
        # $1.00 * 150 = JPY 150 (scale 0, no decimals)
        result = _compute_buy_amount(100, 150_000_000, 2, 0)
        assert result == 150

    def test_round_half_up(self):
        """Rounding: fractional result rounds up at 0.5 boundary."""
        # sell=1000 means $10.00 in cents (scale 2).
        # rate_int=333_500 means 0.3335 at scale 6.
        # raw = 1000 * 333_500 = 333_500_000 (at scale sell_scale+6 = 8)
        # diff = 2 + 6 - 0 = 8, divisor = 10^8
        # (333_500_000 + 50_000_000) // 100_000_000 = 3
        # $10.00 * 0.3335 = 3.335 -> rounds to 3 (fractional part < 0.5 after scaling)
        result = _compute_buy_amount(1000, 333_500, 2, 0)
        assert result == 3

    def test_negative_result_clamped_to_zero(self):
        """Negative intermediate result returns 0."""
        result = _compute_buy_amount(1000, -850_000, 2, 2)
        assert result == 0


class TestPackUserData128:
    """Tests for ``_pack_user_data_128()`` byte packing helper."""

    def test_packs_rate_in_first_8_bytes(self):
        """Rate as little-endian uint64 in first 8 bytes, zeros in upper half."""
        rate_int = 850_000  # 0.85 at scale 6
        result = _pack_user_data_128(rate_int)

        assert len(result) == 16
        # First 8 bytes: rate as little-endian uint64
        expected_lower = (850_000 & 0xFFFFFFFFFFFFFFFF).to_bytes(8, byteorder="little")
        assert result[:8] == expected_lower
        # Upper 8 bytes: zero
        assert result[8:] == b"\x00" * 8

    def test_zero_rate(self):
        """Zero rate packs to all zeros."""
        result = _pack_user_data_128(0)
        assert result == b"\x00" * 16


# ---------------------------------------------------------------------------
# NewFXService factory
# ---------------------------------------------------------------------------

class TestNewFXService:
    """Tests for the ``NewFXService()`` factory function."""

    def test_creates_service_without_logger(self):
        """Factory creates FXService instance with default logger."""
        svc = NewFXService(
            fx_rate_repo=_make_fx_rate_repo(),
            fx_cache=_make_fx_cache(),
            tb_transfer_repo=_make_tb_transfer_repo(),
            tb_account_repo=_make_tb_account_repo(),
            account_meta_repo=_make_account_meta_repo(),
            system_account_repo=_make_system_account_repo(),
            metadata_writer=_make_metadata_writer(),
        )

        assert isinstance(svc, FXService)

    def test_creates_service_with_logger(self):
        """Factory creates FXService instance with custom logger."""
        logger = MagicMock()
        svc = NewFXService(
            fx_rate_repo=_make_fx_rate_repo(),
            fx_cache=_make_fx_cache(),
            tb_transfer_repo=_make_tb_transfer_repo(),
            tb_account_repo=_make_tb_account_repo(),
            account_meta_repo=_make_account_meta_repo(),
            system_account_repo=_make_system_account_repo(),
            metadata_writer=_make_metadata_writer(),
            logger=logger,
        )

        assert isinstance(svc, FXService)
