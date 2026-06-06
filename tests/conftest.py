"""Shared pytest fixtures and configuration."""

import pytest

pytest_plugins = []


@pytest.fixture
def sample_uuid():
    """Return a valid UUID string for testing."""
    return "0194e7c3-8f4a-7b2d-9c1e-4f5a6b7c8d9e"
