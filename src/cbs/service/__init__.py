"""Service layer — business-logic orchestrators."""

from cbs.service.account_service import AccountService, NewAccountService
from cbs.service.balance_service import BalanceResponse, BalanceService, NewBalanceService
from cbs.service.customer_service import CustomerService, NewCustomerService
from cbs.service.fee_service import FeeService, NewFeeService
from cbs.service.fx_service import FXService, NewFXService
from cbs.service.hold_service import HoldService, NewHoldService
from cbs.service.loan_service import LoanService, NewLoanService
from cbs.service.settlement_service import SettlementService, NewSettlementService
from cbs.service.transfer_service import TransferService, NewTransferService

__all__ = [
    "AccountService",
    "BalanceResponse",
    "BalanceService",
    "CustomerService",
    "FeeService",
    "FXService",
    "HoldService",
    "LoanService",
    "NewAccountService",
    "NewBalanceService",
    "NewCustomerService",
    "NewFeeService",
    "NewFXService",
    "NewHoldService",
    "NewLoanService",
    "NewSettlementService",
    "NewTransferService",
    "SettlementService",
    "TransferService",
    "build_services",
]


def build_services(tb_client, db, config):
    """Wire all services with their dependencies.

    Creates repos, caches, then services.  Returns dict keyed by service
    name for DI registration.

    Args:
        tb_client: TigerBeetle client instance (TBClient).
        db: PostgreSQL database instance (Database).
        config: Application configuration (CBSConfig).

    Returns:
        Dict mapping service name to the constructed service instance.
    """
    from cbs.cache import FXCache
    from cbs.store.postgres.account_repo import AccountRepo as PgAccountRepo
    from cbs.store.postgres.audit_repo import AuditRepo
    from cbs.store.postgres.customer_repo import CustomerRepo
    from cbs.store.postgres.settlement_repo import SettlementRepo
    from cbs.store.postgres.system_account_repo import SystemAccountRepo
    from cbs.store.tigerbeetle.account_repo import AccountRepo as TbAccountRepo
    from cbs.store.tigerbeetle.transfer_repo import TransferRepo

    # --- Repos -----------------------------------------------------------
    tb_account_repo = TbAccountRepo(tb_client)
    tb_transfer_repo = TransferRepo(tb_client)
    pg_account_repo = PgAccountRepo(db)
    customer_repo = CustomerRepo(db)
    system_account_repo = SystemAccountRepo(db)
    audit_repo = AuditRepo(db)
    settlement_repo = SettlementRepo(db)

    # --- Caches ----------------------------------------------------------
    fx_cache = FXCache(default_ttl=config.cache_ttl_fx)

    # --- Services --------------------------------------------------------
    customer_service = NewCustomerService(customer_repo)
    balance_service = NewBalanceService(
        tb_account_repo, pg_account_repo
    )

    # AccountService needs the product/loan modules (module-level functions)
    # and the customer service.
    import cbs.store.postgres.fx_rate_repo as fx_rate_repo_module  # noqa: F401
    import cbs.store.postgres.loan_repo as loan_repo_module  # noqa: F401
    import cbs.store.postgres.product_repo as product_repo_module  # noqa: F401

    account_service = NewAccountService(
        tb_account_repo,
        pg_account_repo,
        product_repo_module,
        loan_repo_module,
        customer_service,
    )

    transfer_service = NewTransferService(
        tb_transfer_repo,
        tb_account_repo,
        pg_account_repo,
        system_account_repo,
        audit_repo,
    )

    fx_service = NewFXService(
        fx_rate_repo_module,
        fx_cache,
        tb_transfer_repo,
        tb_account_repo,
        pg_account_repo,
        system_account_repo,
        audit_repo,
    )

    hold_service = NewHoldService(
        tb_transfer_repo,
        tb_account_repo,
        pg_account_repo,
        audit_repo,
    )

    loan_service = NewLoanService(
        tb_transfer_repo,
        tb_account_repo,
        pg_account_repo,
        system_account_repo,
        audit_repo,
        loan_repo_module,
    )

    fee_service = NewFeeService(
        tb_transfer_repo,
        tb_account_repo,
        pg_account_repo,
        system_account_repo,
        audit_repo,
    )

    settlement_service = NewSettlementService(
        tb_transfer_repo,
        settlement_repo,
    )

    return {
        "account": account_service,
        "balance": balance_service,
        "customer": customer_service,
        "fee": fee_service,
        "fx": fx_service,
        "hold": hold_service,
        "loan": loan_service,
        "settlement": settlement_service,
        "transfer": transfer_service,
    }
