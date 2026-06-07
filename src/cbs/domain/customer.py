"""Customer domain model — request types for customer operations.

Mirrors corebanking/internal/domain/customer.go (request side only;
the persistence-side Customer/CustomerAccount live in customer_repo).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cbs.domain.errors import ValidationError


# --- request types -------------------------------------------------------

@dataclass
class RegisterCustomerRequest:
    """Input for registering a new customer."""

    customer_ref: str = ""
    name: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate the request fields.

        Raises ``ValidationError`` on any problem.
        """
        if not self.customer_ref:
            raise ValidationError("customer_ref is required")
        if not _is_valid_uuid(self.customer_ref):
            raise ValidationError("customer_ref must be a valid UUIDv7")
        if not self.name:
            raise ValidationError("name is required")


# --- helpers -------------------------------------------------------------

def _is_valid_uuid(s: str) -> bool:
    """Check that *s* is a valid UUID format (hex + dash positions)."""
    if len(s) != 36:
        return False
    for i, c in enumerate(s):
        if i in (8, 13, 18, 23):
            if c != "-":
                return False
            continue
        if not ((c >= "0" and c <= "9") or (c >= "a" and c <= "f") or (c >= "A" and c <= "F")):
            return False
    return True
