"""Database connectivity probe."""

from sqlalchemy import text
from sqlalchemy.orm import Session


class HealthRepository:
    """Read-only database connection check."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def check_connection(self) -> None:
        """Execute a minimal SQL statement and propagate database failures."""
        self._session.execute(text("SELECT 1"))
