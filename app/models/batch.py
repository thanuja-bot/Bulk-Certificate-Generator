"""Batch ORM entity and lifecycle status."""

import enum
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Date, DateTime, Enum, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.certificate import Certificate


class BatchStatus(str, enum.Enum):
    """Overall generation state for a submitted batch."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class Batch(Base):
    """One bulk request and aggregate generation progress."""

    __tablename__ = "batches"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_batches_idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[BatchStatus] = mapped_column(
        Enum(
            BatchStatus,
            native_enum=False,
            length=32,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=BatchStatus.PENDING,
        nullable=False,
        index=True,
    )
    event_title: Mapped[str] = mapped_column(String(200), nullable=False)
    issuer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_recipients: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )
