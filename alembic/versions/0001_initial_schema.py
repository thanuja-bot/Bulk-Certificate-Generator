"""Create initial batch and certificate tables.

Revision ID: 0001_initial
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> sa.types.TypeEngine[object]:
    """Use native UUID on PostgreSQL and portable UUID storage on SQLite."""
    return sa.Uuid(as_uuid=True).with_variant(postgresql.UUID(as_uuid=True), "postgresql")


def upgrade() -> None:
    """Create tables and lookup indexes."""
    batch_status = sa.Enum(
        "pending",
        "processing",
        "completed",
        "completed_with_errors",
        "failed",
        name="batchstatus",
        native_enum=False,
        length=32,
    )
    certificate_status = sa.Enum(
        "pending",
        "processing",
        "done",
        "failed",
        name="certificatestatus",
        native_enum=False,
        length=16,
    )

    op.create_table(
        "batches",
        sa.Column("id", _uuid_type(), nullable=False),
        sa.Column("status", batch_status, nullable=False),
        sa.Column("event_title", sa.String(length=200), nullable=False),
        sa.Column("issuer_name", sa.String(length=200), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("rejected_recipients", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_batches_idempotency_key"),
    )
    op.create_index("ix_batches_status", "batches", ["status"], unique=False)

    op.create_table(
        "certificates",
        sa.Column("id", _uuid_type(), nullable=False),
        sa.Column("batch_id", _uuid_type(), nullable=False),
        sa.Column("certificate_number", sa.String(length=64), nullable=False),
        sa.Column("recipient_name", sa.String(length=120), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("extra_fields", sa.JSON(), nullable=True),
        sa.Column("status", certificate_status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("file_key", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "certificate_number",
            name="uq_certificates_certificate_number",
        ),
    )
    op.create_index("ix_certificates_batch_id", "certificates", ["batch_id"], unique=False)
    op.create_index(
        "ix_certificates_batch_status",
        "certificates",
        ["batch_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the initial tables and indexes."""
    op.drop_index("ix_certificates_batch_status", table_name="certificates")
    op.drop_index("ix_certificates_batch_id", table_name="certificates")
    op.drop_table("certificates")
    op.drop_index("ix_batches_status", table_name="batches")
    op.drop_table("batches")
