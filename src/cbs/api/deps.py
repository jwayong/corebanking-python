"""Litestar dependency injection providers for services, DB sessions, and config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from litestar import Request
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from cbs.config import CBSConfig
    from cbs.service.account_service import AccountService
    from cbs.service.balance_service import BalanceService
    from cbs.service.customer_service import CustomerService
    from cbs.service.fee_service import FeeService
    from cbs.service.fx_service import FXService
    from cbs.service.hold_service import HoldService
    from cbs.service.loan_service import LoanService
    from cbs.service.settlement_service import SettlementService
    from cbs.service.transfer_service import TransferService


async def provide_account_service(request: Request) -> "AccountService":
    return request.app.state.services["account"]


async def provide_balance_service(request: Request) -> "BalanceService":
    return request.app.state.services["balance"]


async def provide_customer_service(request: Request) -> "CustomerService":
    return request.app.state.services["customer"]


async def provide_fee_service(request: Request) -> "FeeService":
    return request.app.state.services["fee"]


async def provide_fx_service(request: Request) -> "FXService":
    return request.app.state.services["fx"]


async def provide_hold_service(request: Request) -> "HoldService":
    return request.app.state.services["hold"]


async def provide_loan_service(request: Request) -> "LoanService":
    return request.app.state.services["loan"]


async def provide_settlement_service(request: Request) -> "SettlementService":
    return request.app.state.services["settlement"]


async def provide_transfer_service(request: Request) -> "TransferService":
    return request.app.state.services["transfer"]


async def provide_db_session(request: Request) -> AsyncSession:
    """Yield a DB session from the app-level session factory.

    Usage as a Litestar dependency: the generator yields a session,
    Litestar cleans up after the request completes.
    """
    db = request.app.state.db  # cbs.store.postgres.database.Database
    async with db.session() as session:
        yield session


async def provide_config(request: Request) -> "CBSConfig":
    return request.app.state.config
