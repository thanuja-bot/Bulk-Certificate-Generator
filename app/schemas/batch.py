"""Batch creation and progress schemas."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.batch import BatchStatus


class BatchCreateRequest(BaseModel):
    """One event plus a bulk list of raw recipient records.

    Recipient validation is intentionally done item-by-item in the service so one
    malformed recipient does not reject otherwise valid recipients in the request.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "event_title": "Python Fundamentals",
                    "issuer_name": "Northstar Academy",
                    "issue_date": "2026-09-30",
                    "description": "Successful completion of the training course.",
                    "recipients": [
                        {"name": "José Müller", "email": "jose@example.org"},
                        {"name": "प्रिया शर्मा", "email": "priya@example.org"},
                    ],
                }
            ]
        }
    )

    event_title: str = Field(min_length=1, max_length=200)
    issuer_name: str = Field(min_length=1, max_length=200)
    issue_date: date
    description: str | None = Field(default=None, max_length=2000)
    recipients: list[Any] = Field(min_length=1)


class RejectedRecipient(BaseModel):
    """One recipient skipped during partial-success validation."""

    index: int
    reason: str


class BatchAcceptedResponse(BaseModel):
    """Acknowledgement returned after one bulk batch submission."""

    batch_id: uuid.UUID
    status: BatchStatus
    accepted_count: int
    rejected: list[RejectedRecipient]
    status_url: str
    download_url: str


class BatchProgressResponse(BaseModel):
    """Batch status and current certificate progress."""

    batch_id: uuid.UUID
    status: BatchStatus
    event_title: str
    issuer_name: str
    issue_date: date
    total: int
    succeeded: int
    failed: int
    pending: int
    rejected_count: int
    percent_complete: float
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
