"""Health endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.repositories.health_repository import HealthRepository
from app.services.health_service import HealthService

router = APIRouter(tags=["health"])


@router.get("/health", summary="Check API and database health")
def health_check(session: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
    """Return healthy only when the configured database accepts a query."""
    return HealthService(HealthRepository(session)).check()
