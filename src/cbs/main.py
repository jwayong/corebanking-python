"""CBS application — Litestar app factory and CLI entry point."""

from __future__ import annotations

from typing import Any

from litestar import Litestar
from litestar.config.cors import CORSConfig
from litestar.di import Provide

from cbs.api.deps import (
    provide_account_service,
    provide_balance_service,
    provide_config,
    provide_customer_service,
    provide_db_session,
    provide_fee_service,
    provide_fx_service,
    provide_hold_service,
    provide_loan_service,
    provide_settlement_service,
    provide_transfer_service,
)
from cbs.api.middleware.error_handler import EXCEPTION_HANDLERS
from cbs.api.middleware.idempotency import IdempotencyMiddleware
from cbs.api.middleware.logging import LoggingMiddleware
from cbs.api.middleware.request_id import RequestIDMiddleware

# Resolve TYPE_CHECKING forward references in deps at runtime.
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

_SIGNATURE_NAMESPACE = {
    "AccountService": AccountService,
    "BalanceService": BalanceService,
    "CBSConfig": CBSConfig,
    "CustomerService": CustomerService,
    "FeeService": FeeService,
    "FXService": FXService,
    "HoldService": HoldService,
    "LoanService": LoanService,
    "SettlementService": SettlementService,
    "TransferService": TransferService,
}


def create_app(
    config: Any,
    services: dict[str, Any],
    db: Any,
) -> Litestar:
    """Build a fully configured Litestar application.

    Args:
        config: CBSConfig instance.
        services: Dict of service name → service instance (from build_services).
        db: Database instance (from Database.create).

    Returns:
        Configured Litestar app ready to serve requests.
    """
    # 1. CORS configuration
    origins = config.cors_allowed_origins.split(",")
    cors_config = CORSConfig(
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "Idempotent-Replayed"],
        max_age=86400,
    )

    # 2. Middleware stack (first = outermost).
    middleware = [
        RequestIDMiddleware,     # every request gets an ID
        LoggingMiddleware,       # logs with request_id from state
        IdempotencyMiddleware,   # reads db from scope["app"].state.db
    ]

    # 3. DI providers
    dependencies = {
        "account_service": Provide(provide_account_service),
        "balance_service": Provide(provide_balance_service),
        "customer_service": Provide(provide_customer_service),
        "fee_service": Provide(provide_fee_service),
        "fx_service": Provide(provide_fx_service),
        "hold_service": Provide(provide_hold_service),
        "loan_service": Provide(provide_loan_service),
        "settlement_service": Provide(provide_settlement_service),
        "transfer_service": Provide(provide_transfer_service),
        "db_session": Provide(provide_db_session),
        "app_config": Provide(provide_config),
    }

    # 4. Build the app
    app = Litestar(
        route_handlers=[],  # routes added in issue #14
        middleware=middleware,
        exception_handlers=EXCEPTION_HANDLERS,
        dependencies=dependencies,
        cors_config=cors_config,
        signature_namespace=_SIGNATURE_NAMESPACE,
        on_startup=[lambda app: _set_app_state(app, config, services, db)],
    )

    return app


def _set_app_state(app: Litestar, config: Any, services: dict[str, Any], db: Any) -> None:
    """Store services, db, and config on app.state for DI providers."""
    app.state.services = services
    app.state.db = db
    app.state.config = config


# Keep the bare app for CLI compatibility — replaced by create_app at runtime.
app = Litestar(route_handlers=[])
