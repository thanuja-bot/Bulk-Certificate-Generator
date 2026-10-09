"""Common API response types."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorDetails(BaseModel):
    """Consistent top-level API error envelope."""

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Consistent top-level API error envelope."""

    error: ErrorDetails


class Page(BaseModel):
    """Pagination metadata shared by collection responses."""

    model_config = ConfigDict(extra="forbid")

    limit: int
    offset: int
    total: int
