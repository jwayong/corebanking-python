"""Customer service — business logic for customer operations.

Mirrors corebanking/internal/service/customer.go.
"""

from __future__ import annotations

import structlog
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from cbs.domain.customer import RegisterCustomerRequest
from cbs.domain.errors import ErrAlreadyExists, ErrNotFound, ValidationError
from cbs.store.postgres.customer_repo import Customer, CustomerAccount, CustomerRepo

log = structlog.get_logger()


class CustomerService:
    """Handles customer business logic.

    Delegates persistence to a ``CustomerRepo`` and enriches read
    responses with account data.
    """

    def __init__(self, repo: CustomerRepo) -> None:
        self._repo = repo
        self._log = log.bind(component="customer_service")

    async def register(
        self, session: "AsyncSession", req: RegisterCustomerRequest
    ) -> Customer:
        """Create a new customer reference in the CBS.

        Validates *req*, persists via the repo, and returns the created
        ``Customer``.  Re-raises ``ErrAlreadyExists`` unchanged when a
        duplicate *customer_ref* is detected.
        """
        req.validate()

        try:
            customer = await self._repo.create(
                session,
                customer_ref=req.customer_ref,
                name=req.name,
                labels=req.labels if req.labels else None,
            )
        except Exception as exc:  # noqa: BLE001
            if exc is ErrAlreadyExists:
                raise
            self._log.error(
                "failed to create customer",
                customer_ref=req.customer_ref,
                error=str(exc),
            )
            raise RuntimeError(f"register customer: {exc}") from exc

        self._log.info("customer_registered", customer_ref=customer.customer_ref)
        return customer

    async def get(
        self, session: "AsyncSession", ref: str
    ) -> Customer:
        """Retrieve a customer by reference, including their accounts.

        Raises ``ValidationError`` when *ref* is empty and
        ``ErrNotFound`` when no matching customer exists.
        """
        if not ref:
            raise ValidationError("customer_ref is required")

        customer = await self._repo.get_by_ref(session, ref)
        if customer is None:
            raise ErrNotFound

        try:
            accounts: list[CustomerAccount] = await self._repo.list_accounts_by_customer(
                session, ref
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "failed to list customer accounts",
                customer_ref=ref,
                error=str(exc),
            )
            raise RuntimeError(f"get customer accounts: {exc}") from exc

        customer.accounts = accounts
        return customer


def NewCustomerService(repo: CustomerRepo) -> CustomerService:
    """Factory — mirrors the Go constructor name."""
    return CustomerService(repo)
