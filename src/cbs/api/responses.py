"""Standard API response envelope and helpers.

Mirrors corebanking/pkg/httputil/respond.go — provides functions that
build dict responses matching the API envelope spec. Litestar serializes
the dicts to JSON automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone


def success_response(data: dict, request_id: str) -> dict:
    """Build a success envelope.

    Args:
        data: The response payload (e.g., serialized domain object).
        request_id: Unique identifier for tracing.

    Returns:
        Dict with ``status``, ``data``, ``request_id``, and ``timestamp``.
    """
    return {
        "status": "success",
        "data": data,
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def error_response(
    code: str,
    message: str,
    request_id: str,
    details: dict | None = None,
) -> dict:
    """Build an error envelope.

    Args:
        code: Machine-readable error code (e.g., ``"INVALID_REQUEST"``).
        message: Human-readable error description.
        request_id: Unique identifier for tracing.
        details: Optional extra context (e.g., field-level validation errors).

    Returns:
        Dict with ``status``, ``error`` (containing code/message/details),
        ``request_id``, and ``timestamp``. The ``details`` key is omitted
        when *None*.
    """
    error: dict = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "status": "error",
        "error": error,
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def validation_error_response(request_id: str, message: str) -> dict:
    """Build a 400 INVALID_REQUEST error envelope."""
    return error_response("INVALID_REQUEST", message, request_id)


def not_found_response(request_id: str, message: str) -> dict:
    """Build a 404 NOT_FOUND error envelope."""
    return error_response("NOT_FOUND", message, request_id)


def internal_error_response(request_id: str) -> dict:
    """Build a 500 INTERNAL_ERROR envelope with a generic message."""
    return error_response("INTERNAL_ERROR", "an unexpected error occurred", request_id)
