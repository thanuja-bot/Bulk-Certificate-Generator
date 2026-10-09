"""Health status use case."""

from app.repositories.health_repository import HealthRepository


class HealthService:
    """Service wrapper for database readiness."""

    def __init__(self, repository: HealthRepository) -> None:
        self._repository = repository

    def check(self) -> dict[str, str]:
        """Return a healthy status only when the database responds."""
        self._repository.check_connection()
        return {"status": "ok", "database": "ok"}
