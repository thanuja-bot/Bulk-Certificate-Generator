"""Persistence operations for certificate batches."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.batch import Batch, BatchStatus
from app.models.certificate import Certificate, CertificateStatus


class BatchRepository:
    """Repository for batch reads and state transitions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, batch_id: uuid.UUID) -> Batch | None:
        """Find a batch by primary key."""
        return self._session.get(Batch, batch_id)

    def get_by_idempotency_key(self, key: str) -> Batch | None:
        """Find a prior request by its idempotency key."""
        return self._session.scalar(select(Batch).where(Batch.idempotency_key == key))

    def create(
        self,
        batch: Batch,
        certificates: list[Certificate],
        rejected: list[dict[str, object]],
    ) -> tuple[Batch, bool]:
        """Persist one batch and all accepted certificate rows in one transaction."""
        batch.rejected_count = len(rejected)
        batch.rejected_recipients = rejected
        batch.certificates.extend(certificates)
        self._session.add(batch)
        try:
            self._session.commit()
            self._session.refresh(batch)
            return batch, False
        except IntegrityError:
            self._session.rollback()
            if batch.idempotency_key:
                previous = self.get_by_idempotency_key(batch.idempotency_key)
                if previous:
                    return previous, True
            raise

    def get_certificate_counts(self, batch_id: uuid.UUID) -> dict[CertificateStatus, int]:
        """Count certificate rows grouped by status."""
        rows = self._session.execute(
            select(Certificate.status, func.count(Certificate.id))
            .where(Certificate.batch_id == batch_id)
            .group_by(Certificate.status)
        )
        return {status: count for status, count in rows}

    def start_processing(self, batch_ids: set[uuid.UUID]) -> None:
        """Mark newly claimed batches as processing without replacing start time."""
        if not batch_ids:
            return
        self._session.execute(
            update(Batch)
            .where(Batch.id.in_(batch_ids), Batch.status == BatchStatus.PENDING)
            .values(
                status=BatchStatus.PROCESSING,
                started_at=func.coalesce(Batch.started_at, datetime.now(UTC)),
            )
        )

    def finalize_if_no_unfinished(
        self,
        batch_id: uuid.UUID,
        status: BatchStatus,
        completed_at: datetime,
    ) -> bool:
        """Persist a chosen final status only while the batch has no active work."""
        unfinished = (
            select(Certificate.id)
            .where(
                Certificate.batch_id == batch_id,
                Certificate.status.in_([CertificateStatus.PENDING, CertificateStatus.PROCESSING]),
            )
            .exists()
        )
        result = self._session.execute(
            update(Batch)
            .where(
                Batch.id == batch_id,
                Batch.status.in_([BatchStatus.PENDING, BatchStatus.PROCESSING]),
                ~unfinished,
            )
            .values(status=status, completed_at=completed_at)
        )
        self._session.commit()
        return result.rowcount == 1

    def reset_processing_after_crash(self) -> int:
        """Return interrupted work to the queue and reopen affected batches."""
        affected_batch_ids = set(
            self._session.scalars(
                select(Certificate.batch_id).where(
                    Certificate.status == CertificateStatus.PROCESSING
                )
            )
        )
        self._session.execute(
            update(Certificate)
            .where(Certificate.status == CertificateStatus.PROCESSING)
            .values(status=CertificateStatus.PENDING)
        )
        if affected_batch_ids:
            self._session.execute(
                update(Batch)
                .where(Batch.id.in_(affected_batch_ids))
                .values(status=BatchStatus.PENDING, completed_at=None)
            )
        self._session.commit()
        return len(affected_batch_ids)
