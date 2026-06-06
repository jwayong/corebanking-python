"""PostgreSQL store — repository modules for metadata queries and writes.

All repos use SQLAlchemy Core (not ORM) with async sessions from the Database pool.
"""

from cbs.store.postgres.account_repo import (
    AccountRepo,
    AccountRecord,
    AccountWithProduct,
    CustomerAccountRecord,
    OwnerRecord,
)
from cbs.store.postgres.audit_repo import (
    AuditRepo,
    TransferMetadataRecord,
)
from cbs.store.postgres.batch_repo import (
    BatchResult,
    BatchRun,
)
from cbs.store.postgres.customer_repo import (
    Customer,
    CustomerAccount,
    CustomerRepo,
)
from cbs.store.postgres.database import Database
from cbs.store.postgres.fee_repo import (
    FeeBearingAccount,
    FeeCollectionRecord,
    FeeCollectionRepo,
    FeeItem,
)
from cbs.store.postgres.fx_rate_repo import (
    FXRate,
)
from cbs.store.postgres.idempotency_repo import (
    IdempotencyKey,
    IdempotencyRepo,
)
from cbs.store.postgres.loan_repo import (
    LoanDetailRecord,
)
from cbs.store.postgres.product_repo import (
    ProductRecord,
)
from cbs.store.postgres.settlement_repo import SettlementRepo
from cbs.store.postgres.system_account_repo import (
    CreatedSystemAccount,
    SystemAccountRepo,
)

__all__ = [
    # Database
    "Database",
    # Account
    "AccountRepo",
    "AccountRecord",
    "AccountWithProduct",
    "CustomerAccountRecord",
    "OwnerRecord",
    # Customer
    "Customer",
    "CustomerAccount",
    "CustomerRepo",
    # Product
    "ProductRecord",
    # FX Rate
    "FXRate",
    # Idempotency
    "IdempotencyKey",
    "IdempotencyRepo",
    # System Account
    "CreatedSystemAccount",
    "SystemAccountRepo",
    # Loan
    "LoanDetailRecord",
    # Batch
    "BatchRun",
    "BatchResult",
    # Settlement
    "SettlementRepo",
    # Fee
    "FeeItem",
    "FeeBearingAccount",
    "FeeCollectionRecord",
    "FeeCollectionRepo",
    # Audit
    "TransferMetadataRecord",
    "AuditRepo",
]
