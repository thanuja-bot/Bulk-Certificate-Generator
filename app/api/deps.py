"""FastAPI dependency providers; this is the only layer that reads request app state."""

import secrets
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.errors import Unauthorized
from app.db.session import Database, get_session
from app.infrastructure.clock import Clock
from app.infrastructure.numbering import CertificateNumberGenerator
from app.infrastructure.renderer.base import CertificateRenderer
from app.infrastructure.storage.base import StorageBackend
from app.models.certificate import Certificate
from app.repositories.batch_repository import BatchRepository
from app.repositories.certificate_repository import CertificateRepository
from app.services.batch_service import BatchService
from app.services.certificate_service import CertificateService


def require_api_key(
    request: Request,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Protect management routes when an API key is configured."""
    expected = request.app.state.settings.api_key
    if expected is None:
        return
    if api_key is None or not secrets.compare_digest(
        expected.encode("utf-8"),
        api_key.encode("utf-8"),
    ):
        raise Unauthorized()


def get_db(request: Request) -> Iterator[Session]:
    """Yield a request-scoped session from the current application."""
    database: Database = request.app.state.database
    yield from get_session(database)


def get_batch_service(
    request: Request,
    session: Session = Depends(get_db),  # noqa: B008
) -> BatchService:
    """Construct a batch use case from per-application infrastructure."""
    return BatchService(
        batches=BatchRepository(session),
        certificates=CertificateRepository(session),
        settings=request.app.state.settings,
        number_generator=request.app.state.number_generator,
        clock=request.app.state.clock,
    )


def get_certificate_service(
    request: Request,
    session: Session = Depends(get_db),  # noqa: B008
) -> CertificateService:
    """Construct certificate read operations from repositories and storage."""
    session_factory = request.app.state.database.session_factory

    def archive_source(batch_id: uuid.UUID) -> Iterator[Certificate]:
        archive_session = session_factory()
        try:
            yield from CertificateRepository(archive_session).iter_ready_for_batch(batch_id)
        finally:
            archive_session.close()

    return CertificateService(
        batches=BatchRepository(session),
        certificates=CertificateRepository(session),
        storage=request.app.state.storage,
        archive_source=archive_source,
    )


def get_infrastructure(
    request: Request,
) -> tuple[
    StorageBackend,
    CertificateRenderer,
    CertificateNumberGenerator,
    Clock,
]:
    """Expose the infrastructure adapters for lifecycle and worker wiring."""
    return (
        request.app.state.storage,
        request.app.state.renderer,
        request.app.state.number_generator,
        request.app.state.clock,
    )
