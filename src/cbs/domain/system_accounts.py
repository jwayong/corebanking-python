"""System account definitions — 18 accounts created per currency ledger.

Mirrors corebanking/internal/domain/system_account.go SystemAccountDefs().
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemAccountDef:
    """Definition of a system account to create per currency."""

    code: int          # GL account code (e.g. 1101)
    name: str          # Human-readable name (e.g. "Cash Vault")
    tb_flags_debits: bool   # CreditsMustNotExceedDebits (asset accounts)
    tb_flags_credits: bool  # DebitsMustNotExceedCredits (liability accounts)
    tb_flags_history: bool  # History tracking enabled


# 18 system account definitions per currency.
# The order matters — last entry is used for TigerBeetle linked-account batch optimisation.
SYSTEM_ACCOUNTS: list[SystemAccountDef] = [
    # Asset accounts (debits > credits)
    SystemAccountDef(1101, "Cash Vault", True, False, True),
    SystemAccountDef(1110, "Central Bank Reserve", True, False, True),
    SystemAccountDef(1120, "Correspondent Nostro", True, False, True),
    SystemAccountDef(1201, "Settlement Account", True, False, True),
    SystemAccountDef(1301, "Suspense Asset", False, False, True),
    SystemAccountDef(1501, "Accrued Interest Receivable (Loans)", False, False, True),
    SystemAccountDef(1601, "Liquidity Pool", True, False, False),
    # Liability accounts (credits > debits)
    SystemAccountDef(2201, "Accrued Interest Payable (Deposits)", False, True, True),
    SystemAccountDef(2301, "Customer Payables", False, True, True),
    # Equity accounts (bidirectional)
    SystemAccountDef(3101, "Share Capital", False, False, True),
    SystemAccountDef(3130, "Current Year P&L", False, False, True),
    # Income accounts (bidirectional)
    SystemAccountDef(4101, "Interest Income (Loans)", False, False, True),
    SystemAccountDef(4110, "Fee Income", False, False, True),
    # Expense accounts (bidirectional)
    SystemAccountDef(5101, "Interest Expense (Deposits)", False, False, True),
    SystemAccountDef(5130, "Loan Write-Off Expense", False, False, True),
    # Clearing accounts (bidirectional)
    SystemAccountDef(6101, "Suspense Transaction", False, False, True),
    SystemAccountDef(6201, "Clearing Outbound", False, False, True),
    SystemAccountDef(6202, "Clearing Inbound", False, False, True),
]
