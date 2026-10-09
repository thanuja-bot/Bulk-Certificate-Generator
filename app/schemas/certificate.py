"""Certificate list and public verification schemas."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.certificate import CertificateStatus
from app.schemas.common import Page


class CertificateResponse(BaseModel):
    """Certificate details for a batch owner."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID
    certificate_number: str
    recipient_name: str
    recipient_email: str
    extra_fields: dict[str, Any] | None
    status: CertificateStatus
    attempts: int
    error_message: str | None
    created_at: datetime
    generated_at: datetime | None


class CertificatePage(BaseModel):
    """Paginated certificate collection."""

    items: list[CertificateResponse]
    page: Page


class VerificationResponse(BaseModel):
    """Publicly safe certificate verification result; never includes email."""

    valid: bool
    certificate_number: str
    recipient_name: str
    event_title: str
    issue_date: date
    issuer: str
