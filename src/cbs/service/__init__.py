"""Service layer — business-logic orchestrators."""

from cbs.service.account_service import AccountService, NewAccountService
from cbs.service.balance_service import BalanceResponse, BalanceService, NewBalanceService
from cbs.service.customer_service import CustomerService, NewCustomerService

__all__ = [
    "AccountService",
    "BalanceResponse",
    "BalanceService",
    "CustomerService",
    "NewAccountService",
    "NewBalanceService",
    "NewCustomerService",
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
    from cbs.cache import FXCache, LedgerCache, ProductCache
    from cbs.store.postgres.account_repo import AccountRepo as PgAccountRepo
    from cbs.store.postgres.customer_repo import CustomerRepo
    from cbs.store.tigerbeetle.account_repo import AccountRepo as TbAccountRepo

    # --- Repos -----------------------------------------------------------
    tb_account_repo = TbAccountRepo(tb_client)
    pg_account_repo = PgAccountRepo(db)
    customer_repo = CustomerRepo(db)

    # --- Caches ----------------------------------------------------------
    fx_cache = FXCache(default_ttl=config.cache_ttl_fx)
    product_cache = ProductCache(default_ttl=config.cache_ttl_product)
    ledger_cache = LedgerCache()

    # --- Services --------------------------------------------------------
    customer_service = NewCustomerService(customer_repo)
    balance_service = NewBalanceService(
        tb_account_repo, pg_account_repo
    )

    # AccountService needs the product/loan modules (module-level functions)
    # and the customer service.
    import cbs.store.postgres.loan_repo as loan_repo_module  # noqa: F401
    import cbs.store.postgres.product_repo as product_repo_module  # noqa: F401

    account_service = NewAccountService(
        tb_account_repo,
        pg_account_repo,
        product_repo_module,
        loan_repo_module,
        customer_service,
    )

    return {
        "account": account_service,
        "balance": balance_service,
        "customer": customer_service,
    }
