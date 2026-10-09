"""Clock abstraction for deterministic service tests."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Time source used where a use case needs a current timestamp."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""


class SystemClock(Clock):
    """Production UTC clock."""

    def now(self) -> datetime:
        """Return current UTC time."""
        return datetime.now(UTC)
