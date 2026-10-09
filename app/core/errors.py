"""Application errors and consistent HTTP error responses."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected application errors."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFound(AppError):
    """An expected resource lookup failed."""

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__("not_found", message, status_code=404)


class Unauthorized(AppError):
    """An API key is missing or does not match the configured key."""

    def __init__(self, message: str = "Missing or invalid API key") -> None:
        super().__init__("unauthorized", message, status_code=401)


class Conflict(AppError):
    """The request conflicts with the current resource state."""

    def __init__(
        self,
        message: str = "Request conflicts with current state",
        *,
        code: str = "conflict",
    ) -> None:
        super().__init__(code, message, status_code=409)


class ValidationFailed(AppError):
    """Business-level validation failed."""

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__("validation_failed", message, status_code=422, details=details)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"error": {"code": code, "message": message, "details": details}}),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register stable error envelopes without exposing server internals."""

    @app.exception_handler(AppError)
    def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    def handle_request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            422,
            "request_validation_failed",
            "Request validation failed",
            exc.errors(),
        )

    @app.exception_handler(StarletteHTTPException)
    def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "HTTP request failed"
        return _error_response(exc.status_code, "http_error", message)

    @app.exception_handler(Exception)
    def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled application error", exc_info=exc)
        return _error_response(500, "internal_error", "An unexpected error occurred")
