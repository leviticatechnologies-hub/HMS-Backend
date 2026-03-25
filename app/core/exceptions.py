"""
Global exception handlers for consistent API error responses.
"""
from fastapi import Request, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, DBAPIError
import logging
from typing import List

logger = logging.getLogger(__name__)


def create_error_response(
    success: bool = False,
    message: str = "An error occurred",
    errors: List[str] = None,
    data: any = None
) -> dict:
    """Create standardized error response"""
    return {
        "success": success,
        "message": message,
        "errors": errors or [],
        "data": data
    }


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handle HTTPException with consistent format.
    
    Converts FastAPI HTTPException to standardized error response.
    """
    # Extract error details from HTTPException
    if isinstance(exc.detail, dict):
        message = exc.detail.get("message", "HTTP error occurred")
        errors = exc.detail.get("errors", [str(exc.detail)])
    elif isinstance(exc.detail, list):
        message = "Validation errors occurred"
        errors = [str(error) for error in exc.detail]
    else:
        message = str(exc.detail)
        errors = [message]
    
    # Log the error for debugging
    logger.warning(
        f"HTTP {exc.status_code} error: {message}",
        extra={
            "url": str(request.url),
            "method": request.method,
            "status_code": exc.status_code,
            "errors": errors
        }
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content=create_error_response(
            success=False,
            message=message,
            errors=errors,
            data=None
        )
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Handle Pydantic validation errors with consistent format.
    
    Converts request validation errors to standardized error response.
    """
    # Public DCM demo/contact forms and internal ticket-email expect flat { success, error } per integration spec.
    if (
        request.url.path.startswith("/demo")
        or request.url.path.startswith("/contact")
        or "ticket-email" in request.url.path
    ):
        errs = exc.errors()
        if not errs:
            friendly = "Invalid request"
        else:
            err = errs[0]
            loc = tuple(err.get("loc") or ())
            msg = err.get("msg", "Invalid value")
            err_type = err.get("type")
            if loc == ("body",) or not loc:
                friendly = "Invalid or empty JSON body"
            else:
                field = str(loc[-1]) if loc else "field"
                if field == "hospital_email":
                    friendly = "Hospital email is required" if err_type == "missing" else msg
                elif field == "email" and "email" in msg.lower():
                    friendly = "A valid work email is required"
                elif err_type == "missing" or "field required" in msg.lower():
                    friendly = f"{field.replace('_', ' ').title()} is required"
                elif "preferred_demo_date" in loc:
                    friendly = msg
                else:
                    friendly = msg
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "error": friendly},
        )

    errors = []
    for error in exc.errors():
        field_path = " -> ".join([str(loc) for loc in error["loc"]])
        error_msg = f"{field_path}: {error['msg']}"
        errors.append(error_msg)
    
    message = f"Request validation failed: {len(errors)} error(s)"
    
    # Log validation errors
    logger.warning(
        f"Validation error: {message}",
        extra={
            "url": str(request.url),
            "method": request.method,
            "errors": errors
        }
    )
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=create_error_response(
            success=False,
            message=message,
            errors=errors,
            data=None
        )
    )


def _integrity_error_detail(exc: IntegrityError) -> str:
    """Extract a developer-friendly message from IntegrityError (constraint name, detail)."""
    try:
        orig = getattr(exc, "orig", None)
        if orig is not None:
            # asyncpg/PostgreSQL: constraint_name, detail, message
            name = getattr(orig, "constraint_name", None)
            detail_msg = getattr(orig, "detail", None)
            msg = getattr(orig, "message", None)
            parts = []
            if name:
                parts.append(f"constraint '{name}'")
            if detail_msg:
                parts.append(str(detail_msg))
            elif msg:
                parts.append(str(msg))
            if parts:
                return "; ".join(parts)
            return str(orig)
    except Exception:
        pass
    return str(exc)


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """
    Handle database integrity errors (unique, foreign key, not null).
    Returns 409 with the actual constraint/error detail so you can see where to fix it.
    """
    detail = _integrity_error_detail(exc)
    logger.warning(
        f"Database integrity error: {detail}",
        extra={
            "url": str(request.url),
            "method": request.method,
            "detail": detail,
        }
    )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=create_error_response(
            success=False,
            message="Database constraint violated.",
            errors=[detail],
            data=None
        )
    )


async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """
    Handle database connectivity/operational errors.
    Returns 503 so clients know the service is temporarily unavailable.
    """
    logger.warning(
        f"Database operational error: {type(exc).__name__}",
        extra={
            "url": str(request.url),
            "method": request.method,
        }
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=create_error_response(
            success=False,
            message="Database is temporarily unavailable. Please try again later.",
            errors=["Service temporarily unavailable"],
            data=None
        )
    )


async def dbapi_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    """Handle SQLAlchemy DBAPI errors. Returns actual error so you can fix it."""
    orig = str(exc.orig) if getattr(exc, "orig", None) else str(exc)
    logger.exception(
        f"Database error: {type(exc).__name__}",
        extra={"url": str(request.url), "method": request.method, "exception_message": orig}
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=create_error_response(
            success=False,
            message="Database error",
            errors=[orig],
            data=None
        )
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unexpected exceptions. Returns actual error message so you can find and fix the cause.
    """
    logger.exception(
        f"Unhandled exception: {type(exc).__name__}",
        extra={
            "url": str(request.url),
            "method": request.method,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc)
        }
    )
    err_msg = f"{type(exc).__name__}: {str(exc)}"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=create_error_response(
            success=False,
            message="Internal server error",
            errors=[err_msg],
            data=None
        )
    )


# Custom business logic exceptions
class BusinessLogicError(Exception):
    """Custom exception for business logic errors"""
    def __init__(self, message: str, errors: List[str] = None):
        self.message = message
        self.errors = errors or [message]
        super().__init__(self.message)


class NotFoundError(Exception):
    """Exception raised when a resource is not found"""
    def __init__(self, message: str = "Resource not found"):
        self.message = message
        super().__init__(self.message)


class ValidationError(Exception):
    """Exception raised for validation errors"""
    def __init__(self, message: str, errors: List[str] = None):
        self.message = message
        self.errors = errors or [message]
        super().__init__(self.message)


class BillNotFoundError(NotFoundError):
    """Exception raised when a bill is not found"""
    def __init__(self, bill_id: str = None):
        message = f"Bill {bill_id} not found" if bill_id else "Bill not found"
        super().__init__(message)


class PaymentNotFoundError(NotFoundError):
    """Exception raised when a payment is not found"""
    def __init__(self, payment_id: str = None):
        message = f"Payment {payment_id} not found" if payment_id else "Payment not found"
        super().__init__(message)


class SettingsNotFoundError(NotFoundError):
    """Exception raised when settings are not found"""
    def __init__(self, message: str = "Settings not found"):
        super().__init__(message)


async def business_logic_exception_handler(request: Request, exc: BusinessLogicError) -> JSONResponse:
    """Handle custom business logic exceptions"""
    logger.warning(
        f"Business logic error: {exc.message}",
        extra={
            "url": str(request.url),
            "method": request.method,
            "errors": exc.errors
        }
    )
    
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=create_error_response(
            success=False,
            message=exc.message,
            errors=exc.errors,
            data=None
        )
    )