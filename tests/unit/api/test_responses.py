"""Unit tests for API response envelope helpers.

Mirrors corebanking/pkg/httputil/respond_test.go — verifies that the
response builders produce dicts matching the API envelope spec.
"""

from __future__ import annotations

import pytest

from cbs.api.responses import (
    error_response,
    internal_error_response,
    not_found_response,
    success_response,
    validation_error_response,
)


# ---------------------------------------------------------------------------
# success_response()
# ---------------------------------------------------------------------------

class TestSuccessResponse:
    """Tests for ``success_response()``."""

    def test_success_response_fields(self):
        """Verify status, data, request_id, timestamp are present."""
        result = success_response({"id": "123"}, "req-001")

        assert result["status"] == "success"
        assert result["data"] == {"id": "123"}
        assert result["request_id"] == "req-001"
        assert "timestamp" in result

    def test_timestamp_is_utc_iso(self):
        """Verify timestamp is UTC ISO 8601 format."""
        result = success_response({}, "req-002")

        ts = result["timestamp"]
        assert "T" in ts  # ISO format separator
        assert "+00:00" in ts  # UTC offset


# ---------------------------------------------------------------------------
# error_response()
# ---------------------------------------------------------------------------

class TestErrorResponse:
    """Tests for ``error_response()``."""

    def test_error_response_fields(self):
        """Verify status, error.code, error.message, request_id, timestamp."""
        result = error_response("SOME_ERROR", "something went wrong", "req-003")

        assert result["status"] == "error"
        assert result["error"]["code"] == "SOME_ERROR"
        assert result["error"]["message"] == "something went wrong"
        assert result["request_id"] == "req-003"
        assert "timestamp" in result

    def test_error_response_with_details(self):
        """Verify details key is included when provided."""
        result = error_response(
            "SOME_ERROR", "something went wrong", "req-004",
            details={"field": "value"},
        )

        assert result["error"]["details"] == {"field": "value"}

    def test_error_response_without_details(self):
        """Verify details key is absent when None."""
        result = error_response("SOME_ERROR", "something went wrong", "req-005")

        assert "details" not in result["error"]


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

class TestValidationErrorResponse:
    """Tests for ``validation_error_response()``."""

    def test_validation_error_response(self):
        """Verify code is INVALID_REQUEST."""
        result = validation_error_response("req-006", "missing required field")

        assert result["status"] == "error"
        assert result["error"]["code"] == "INVALID_REQUEST"
        assert result["error"]["message"] == "missing required field"


class TestNotFoundResponse:
    """Tests for ``not_found_response()``."""

    def test_not_found_response(self):
        """Verify code is NOT_FOUND."""
        result = not_found_response("req-007", "account not found")

        assert result["status"] == "error"
        assert result["error"]["code"] == "NOT_FOUND"
        assert result["error"]["message"] == "account not found"


class TestInternalErrorResponse:
    """Tests for ``internal_error_response()``."""

    def test_internal_error_response(self):
        """Verify code is INTERNAL_ERROR with generic message."""
        result = internal_error_response("req-008")

        assert result["status"] == "error"
        assert result["error"]["code"] == "INTERNAL_ERROR"
        assert result["error"]["message"] == "an unexpected error occurred"
