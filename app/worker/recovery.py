"""Crash recovery for interrupted certificate jobs."""

from sqlalchemy.orm import Session, sessionmaker

from app.repositories.batch_repository import BatchRepository


def recover_processing(session_factory: sessionmaker[Session]) -> int:
    """Reset rows left in processing state before starting the worker."""
    session = session_factory()
    try:
        return BatchRepository(session).reset_processing_after_crash()
    finally:
        session.close()
