"""Shared pytest fixtures for service-layer unit tests.

Re-exports fixtures from the postgres repo conftest so service tests
can use ``mock_session`` without a real database connection.
"""

from __future__ import annotations

# Re-export mock_session so it is discoverable by tests in this directory.
from tests.unit.store.postgres.conftest import mock_session  # noqa: F401
