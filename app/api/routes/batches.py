"""Batch submission, progress, and certificate listing routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from app.api.deps import get_batch_service, require_api_key
from app.models.certificate import CertificateStatus
from app.schemas.batch import (
    BatchAcceptedResponse,
    BatchCreateRequest,
    BatchProgressResponse,
)
from app.schemas.certificate import CertificatePage
from app.schemas.common import Page
from app.services.batch_service import BatchService, CreateBatchCommand

router = APIRouter(
    prefix="/api/batches",
    tags=["batches"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "",
    response_model=BatchAcceptedResponse,
    status_code=202,
    summary="Submit one batch of certificates",
)
def create_batch(
    payload: BatchCreateRequest,
    response: Response,
    service: BatchService = Depends(get_batch_service),  # noqa: B008
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BatchAcceptedResponse:
    """Validate recipients independently and enqueue one bulk batch."""
    submission = service.create_batch(
        CreateBatchCommand(
            event_title=payload.event_title,
            issuer_name=payload.issuer_name,
            issue_date=payload.issue_date,
            description=payload.description,
            recipients=payload.recipients,
        ),
        idempotency_key,
    )
    batch_id = submission.batch.id
    if submission.replayed:
        response.status_code = 200
    return BatchAcceptedResponse(
        batch_id=batch_id,
        status=submission.batch.status,
        accepted_count=submission.batch.total,
        rejected=[{"index": item.index, "reason": item.reason} for item in submission.rejected],
        status_url=f"/api/batches/{batch_id}",
        download_url=f"/api/batches/{batch_id}/download",
    )


@router.get(
    "/{batch_id}",
    response_model=BatchProgressResponse,
    summary="Get batch status and progress",
)
def get_batch(
    batch_id: uuid.UUID,
    service: BatchService = Depends(get_batch_service),  # noqa: B008
) -> BatchProgressResponse:
    """Return status plus aggregate generation progress."""
    progress = service.get_progress(batch_id)
    batch = progress.batch
    return BatchProgressResponse(
        batch_id=batch.id,
        status=batch.status,
        event_title=batch.event_title,
        issuer_name=batch.issuer_name,
        issue_date=batch.issue_date,
        total=batch.total,
        succeeded=batch.succeeded,
        failed=batch.failed,
        pending=progress.pending,
        rejected_count=batch.rejected_count,
        percent_complete=progress.percent_complete,
        created_at=batch.created_at,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
    )


@router.get(
    "/{batch_id}/certificates",
    response_model=CertificatePage,
    summary="List certificates in a batch",
)
def list_certificates(
    batch_id: uuid.UUID,
    service: BatchService = Depends(get_batch_service),  # noqa: B008
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: CertificateStatus | None = None,
) -> CertificatePage:
    """Return a paginated list with an optional generation-status filter."""
    certificates, total = service.list_certificates(
        batch_id,
        limit=limit,
        offset=offset,
        status=status,
    )
    return CertificatePage(
        items=certificates,
        page=Page(limit=limit, offset=offset, total=total),
    )
