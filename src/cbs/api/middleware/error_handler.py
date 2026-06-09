"""Global exception handlers — map domain exceptions to HTTP responses."""

from __future__ import annotations

import traceback

import structlog
from litestar import Request, Response
from litestar.enums import MediaType

from cbs.api.responses import error_response, internal_error_response
from cbs.domain.errors import (
    DomainError,
    InsufficientBalanceError,
    TransferError,
    ValidationError,
)

log = structlog.get_logger()


def domain_error_handler(request: Request, exc: DomainError) -> Response:
    """Handle all DomainError subclasses — read status_code and error_code from the exception."""
    request_id = getattr(request.state, "request_id", "")
    body = error_response(
        code=exc.error_code,
        message=exc.message,
        request_id=request_id,
    )
    return Response(
        content=body,
        status_code=exc.status_code.value,
        media_type=MediaType.JSON,
    )


def validation_error_handler(request: Request, exc: ValidationError) -> Response:
    """Handle ValidationError (not a DomainError subclass)."""
    request_id = getattr(request.state, "request_id", "")
    body = error_response(
        code="INVALID_REQUEST",
        message=exc.message,
        request_id=request_id,
    )
    return Response(
        content=body,
        status_code=400,
        media_type=MediaType.JSON,
    )


def transfer_error_handler(request: Request, exc: TransferError) -> Response:
    """Handle TransferError (TB failures). InsufficientBalanceError is caught here too."""
    request_id = getattr(request.state, "request_id", "")
    # InsufficientBalanceError has status_code; plain TransferError does not.
    status = getattr(exc, "status_code", None)
    status_code = status.value if hasattr(status, "value") else 422
    body = error_response(
        code=exc.code,
        message=exc.message,
        request_id=request_id,
        details=exc.details if exc.details else None,
    )
    return Response(
        content=body,
        status_code=status_code,
        media_type=MediaType.JSON,
    )


def generic_exception_handler(request: Request, exc: Exception) -> Response:
    """Catch-all for unhandled exceptions — return 500, log stack trace."""
    request_id = getattr(request.state, "request_id", "")
    log.error(
        "unhandled_exception",
        error=str(exc),
        traceback=traceback.format_exc(),
        method=request.method,
        path=request.url.path,
        request_id=request_id,
    )
    body = internal_error_response(request_id)
    return Response(
        content=body,
        status_code=500,
        media_type=MediaType.JSON,
    )


# Dict to pass to Litestar(exception_handlers={...}).
# Order matters: more specific types before their base classes.
EXCEPTION_HANDLERS = {
    DomainError: domain_error_handler,
    ValidationError: validation_error_handler,
    InsufficientBalanceError: transfer_error_handler,
    TransferError: transfer_error_handler,
    Exception: generic_exception_handler,
}
