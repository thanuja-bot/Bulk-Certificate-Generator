"""Persistence operations for certificate rows and worker claims."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.models.batch import Batch
from app.models.certificate import Certificate, CertificateStatus


class CertificateRepository:
    """Repository for certificate lookup, listing, and worker state changes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, certificate_id: uuid.UUID) -> Certificate | None:
        """Find one certificate by UUID."""
        return self._session.get(Certificate, certificate_id)

    def get_with_batch(self, certificate_id: uuid.UUID) -> Certificate | None:
        """Find one certificate and eagerly load its batch."""
        return self._session.scalar(
            select(Certificate)
            .options(joinedload(Certificate.batch))
            .where(Certificate.id == certificate_id)
        )

    def get_by_number(self, certificate_number: str) -> Certificate | None:
        """Find a certificate and its batch using the public verification number."""
        return self._session.scalar(
            select(Certificate)
            .options(joinedload(Certificate.batch))
            .where(Certificate.certificate_number == certificate_number)
        )

    def get_for_download(self, certificate_id: uuid.UUID) -> Certificate | None:
        """Find a certificate for download and include its owning batch."""
        return self.get_with_batch(certificate_id)

    def list_for_batch(
        self,
        batch_id: uuid.UUID,
        *,
        offset: int,
        limit: int,
        status: CertificateStatus | None,
    ) -> tuple[list[Certificate], int]:
        """Return one page and total count for a batch's certificates."""
        statement = select(Certificate).where(Certificate.batch_id == batch_id)
        count_statement = select(func.count(Certificate.id)).where(Certificate.batch_id == batch_id)
        if status is not None:
            statement = statement.where(Certificate.status == status)
            count_statement = count_statement.where(Certificate.status == status)
        items = list(
            self._session.scalars(
                statement.order_by(Certificate.created_at, Certificate.id)
                .offset(offset)
                .limit(limit)
            )
        )
        total = self._session.scalar(count_statement) or 0
        return items, total

    def count_ready_for_batch(self, batch_id: uuid.UUID) -> int:
        """Count generated rows before constructing an archive response."""
        return (
            self._session.scalar(
                select(func.count(Certificate.id)).where(
                    Certificate.batch_id == batch_id,
                    Certificate.status == CertificateStatus.DONE,
                    Certificate.file_key.is_not(None),
                )
            )
            or 0
        )

    def iter_ready_for_batch(
        self,
        batch_id: uuid.UUID,
        *,
        chunk_size: int = 100,
    ) -> Iterator[Certificate]:
        """Yield archive metadata incrementally from the database."""
        statement = (
            select(Certificate)
            .where(
                Certificate.batch_id == batch_id,
                Certificate.status == CertificateStatus.DONE,
                Certificate.file_key.is_not(None),
            )
            .order_by(Certificate.created_at, Certificate.id)
            .execution_options(stream_results=True, yield_per=chunk_size)
        )
        yield from self._session.scalars(statement)

    def count_by_status(self, batch_id: uuid.UUID) -> dict[CertificateStatus, int]:
        """Count certificate rows by status for batch progress."""
        rows = self._session.execute(
            select(Certificate.status, func.count(Certificate.id))
            .where(Certificate.batch_id == batch_id)
            .group_by(Certificate.status)
        )
        return {status: count for status, count in rows}

    def claim_pending(self, limit: int) -> list[uuid.UUID]:
        """Atomically mark a small set of pending rows as processing."""
        dialect = self._session.get_bind().dialect.name
        if dialect == "sqlite":
            # IMMEDIATE prevents two SQLite workers from selecting the same rows.
            self._session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        statement = (
            select(Certificate.id)
            .where(Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.created_at, Certificate.id)
            .limit(limit)
        )
        if dialect == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        certificate_ids = list(self._session.scalars(statement))
        if not certificate_ids:
            self._session.commit()
            return []

        now = datetime.now(UTC)
        self._session.execute(
            update(Certificate)
            .where(
                Certificate.id.in_(certificate_ids),
                Certificate.status == CertificateStatus.PENDING,
            )
            .values(status=CertificateStatus.PROCESSING, attempts=Certificate.attempts + 1)
        )
        batch_ids = set(
            self._session.scalars(
                select(Certificate.batch_id).where(Certificate.id.in_(certificate_ids))
            )
        )
        self._session.execute(
            update(Batch)
            .where(Batch.id.in_(batch_ids), Batch.status == "pending")
            .values(status="processing", started_at=func.coalesce(Batch.started_at, now))
        )
        self._session.commit()
        return certificate_ids

    def mark_done(self, certificate_id: uuid.UUID, file_key: str) -> uuid.UUID | None:
        """Mark a claimed certificate done and atomically increment its batch."""
        certificate = self.get(certificate_id)
        if certificate is None or certificate.status != CertificateStatus.PROCESSING:
            return None
        batch_id = certificate.batch_id
        result = self._session.execute(
            update(Certificate)
            .where(
                Certificate.id == certificate_id,
                Certificate.status == CertificateStatus.PROCESSING,
            )
            .values(
                status=CertificateStatus.DONE,
                file_key=file_key,
                generated_at=datetime.now(UTC),
                error_message=None,
            )
        )
        if result.rowcount:
            self._session.execute(
                update(Batch).where(Batch.id == batch_id).values(succeeded=Batch.succeeded + 1)
            )
            self._session.commit()
            return batch_id
        self._session.rollback()
        return None

    def mark_attempt_failed(
        self,
        certificate_id: uuid.UUID,
        message: str,
        *,
        terminal_failure: bool,
    ) -> uuid.UUID | None:
        """Persist a retry or terminal failure chosen by the generation service."""
        certificate = self.get(certificate_id)
        if certificate is None or certificate.status != CertificateStatus.PROCESSING:
            return None
        batch_id = certificate.batch_id
        next_status = CertificateStatus.FAILED if terminal_failure else CertificateStatus.PENDING
        result = self._session.execute(
            update(Certificate)
            .where(
                Certificate.id == certificate_id,
                Certificate.status == CertificateStatus.PROCESSING,
            )
            .values(status=next_status, error_message=message[:500])
        )
        if result.rowcount:
            if terminal_failure:
                self._session.execute(
                    update(Batch).where(Batch.id == batch_id).values(failed=Batch.failed + 1)
                )
            self._session.commit()
            return batch_id
        self._session.rollback()
        return None

    def get_render_data(self, certificate_id: uuid.UUID) -> tuple[Certificate, Batch] | None:
        """Retrieve certificate and event data in a single repository operation."""
        certificate = self.get_with_batch(certificate_id)
        if certificate is None or certificate.batch is None:
            return None
        return certificate, certificate.batch
