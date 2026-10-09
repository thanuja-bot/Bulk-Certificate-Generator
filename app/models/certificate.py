"""Certificate ORM entity and generation status."""

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.batch import Batch


class CertificateStatus(str, enum.Enum):
    """Generation state for one recipient's certificate."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Certificate(Base):
    """A recipient record and its generated file reference."""

    __tablename__ = "certificates"
    __table_args__ = (
        Index("ix_certificates_batch_status", "batch_id", "status"),
        UniqueConstraint("certificate_number", name="uq_certificates_certificate_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    certificate_number: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    extra_fields: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[CertificateStatus] = mapped_column(
        Enum(
            CertificateStatus,
            native_enum=False,
            length=16,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=CertificateStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped["Batch"] = relationship(back_populates="certificates")
