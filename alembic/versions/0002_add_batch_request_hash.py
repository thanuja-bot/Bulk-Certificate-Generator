"""Store canonical request hashes for safe idempotent replays.

Revision ID: 0002_request_hash
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_request_hash"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable SHA-256 request hash without changing existing rows."""
    op.add_column("batches", sa.Column("request_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Remove the request-hash column."""
    op.drop_column("batches", "request_hash")
