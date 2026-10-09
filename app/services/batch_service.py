"""Batch submission, recipient validation, and progress use cases."""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from email_validator import EmailNotValidError, validate_email

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.infrastructure.clock import Clock
from app.infrastructure.numbering import CertificateNumberGenerator
from app.models.batch import Batch, BatchStatus
from app.models.certificate import Certificate, CertificateStatus
from app.repositories.batch_repository import BatchRepository
from app.repositories.certificate_repository import CertificateRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecipientRejection:
    """Service-layer result for one rejected recipient."""

    index: int
    reason: str


@dataclass(frozen=True)
class CreateBatchCommand:
    """Validated event-level input plus raw items for partial recipient validation."""

    event_title: str
    issuer_name: str
    issue_date: date
    description: str | None
    recipients: list[Any]


@dataclass(frozen=True)
class BatchSubmission:
    """Service result distinguishing a new batch from an idempotent replay."""

    batch: Batch
    rejected: list[RecipientRejection]
    replayed: bool


@dataclass(frozen=True)
class BatchProgress:
    """Current batch state and derived certificate counts."""

    batch: Batch
    pending: int
    percent_complete: float


class BatchService:
    """Use cases for batch creation and read-only progress."""

    def __init__(
        self,
        batches: BatchRepository,
        certificates: CertificateRepository,
        settings: Settings,
        number_generator: CertificateNumberGenerator,
        clock: Clock,
    ) -> None:
        self._batches = batches
        self._certificates = certificates
        self._settings = settings
        self._number_generator = number_generator
        self._clock = clock

    def create_batch(
        self,
        payload: CreateBatchCommand,
        idempotency_key: str | None,
    ) -> BatchSubmission:
        """Validate recipients independently and create one atomic batch."""
        request_hash = self._request_hash(payload)
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key or len(idempotency_key) > 255:
                raise ValidationFailed("Idempotency-Key must contain 1 to 255 characters")
            previous = self._batches.get_by_idempotency_key(idempotency_key)
            if previous:
                return self._replay(previous, request_hash)

        if len(payload.recipients) > self._settings.max_recipients:
            raise ValidationFailed(
                f"Recipient limit is {self._settings.max_recipients}",
                {"maximum": self._settings.max_recipients},
            )

        event_title = payload.event_title.strip()
        issuer_name = payload.issuer_name.strip()
        if not event_title or not issuer_name:
            raise ValidationFailed("Event title and issuer name must not be blank")

        accepted: list[Certificate] = []
        rejected: list[RecipientRejection] = []
        seen_emails: set[str] = set()
        for index, raw_recipient in enumerate(payload.recipients):
            result = self._validate_recipient(index, raw_recipient, seen_emails)
            if isinstance(result, RecipientRejection):
                rejected.append(result)
                continue

            name, email, extra = result
            seen_emails.add(email)
            accepted.append(
                Certificate(
                    certificate_number=self._number_generator.generate(),
                    recipient_name=name,
                    recipient_email=email,
                    extra_fields=extra,
                    status=CertificateStatus.PENDING,
                )
            )

        if not accepted:
            raise ValidationFailed(
                "No valid recipients were provided",
                {"rejected": [{"index": item.index, "reason": item.reason} for item in rejected]},
            )

        batch = Batch(
            status=BatchStatus.PENDING,
            event_title=event_title,
            issuer_name=issuer_name,
            issue_date=payload.issue_date,
            description=payload.description.strip() if payload.description else None,
            total=len(accepted),
            rejected_count=len(rejected),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_at=self._clock.now(),
        )
        persisted, replayed = self._batches.create(
            batch,
            accepted,
            [item.__dict__ for item in rejected],
        )
        if replayed:
            return self._replay(persisted, request_hash)

        logger.info(
            "Created certificate batch",
            extra={"batch_id": str(persisted.id), "accepted": len(accepted)},
        )
        return BatchSubmission(persisted, rejected, replayed=False)

    def get_progress(self, batch_id: uuid.UUID) -> BatchProgress:
        """Return batch progress counts for the status endpoint."""
        batch = self._batches.get(batch_id)
        if batch is None:
            raise NotFound("Batch not found")
        counts = self._certificates.count_by_status(batch_id)
        completed = counts.get(CertificateStatus.DONE, 0)
        failed = counts.get(CertificateStatus.FAILED, 0)
        pending = max(batch.total - completed - failed, 0)
        percent = ((completed + failed) / batch.total * 100) if batch.total else 100.0
        return BatchProgress(batch, pending, round(percent, 2))

    def list_certificates(
        self,
        batch_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: CertificateStatus | None,
    ) -> tuple[list[Certificate], int]:
        """Return a paginated certificate list after verifying its parent batch."""
        if self._batches.get(batch_id) is None:
            raise NotFound("Batch not found")
        return self._certificates.list_for_batch(
            batch_id,
            limit=limit,
            offset=offset,
            status=status,
        )

    @staticmethod
    def _validate_recipient(
        index: int,
        raw: Any,
        seen_emails: set[str],
    ) -> tuple[str, str, dict[str, Any] | None] | RecipientRejection:
        """Return a normalized recipient or a concise validation reason."""
        if not isinstance(raw, dict):
            return RecipientRejection(index=index, reason="recipient must be an object")
        name_value = raw.get("name")
        if not isinstance(name_value, str):
            return RecipientRejection(index=index, reason="name must be a string")
        name = name_value.strip()
        if not name or len(name) > 120:
            return RecipientRejection(index=index, reason="name must be 1 to 120 characters")

        email_value = raw.get("email")
        if not isinstance(email_value, str):
            return RecipientRejection(index=index, reason="email must be a string")
        try:
            email = validate_email(
                email_value.strip(),
                check_deliverability=False,
            ).normalized.lower()
        except EmailNotValidError:
            return RecipientRejection(index=index, reason="email is invalid")
        if email in seen_emails:
            return RecipientRejection(index=index, reason="duplicate email in request")

        extra = raw.get("extra")
        if extra is not None and not isinstance(extra, dict):
            return RecipientRejection(index=index, reason="extra must be an object")
        return name, email, extra

    @staticmethod
    def _request_hash(payload: CreateBatchCommand) -> str:
        """Hash a stable JSON encoding so object-key order does not affect replays."""
        canonical_body = json.dumps(
            {
                "event_title": payload.event_title,
                "issuer_name": payload.issuer_name,
                "issue_date": payload.issue_date.isoformat(),
                "description": payload.description,
                "recipients": payload.recipients,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()

    @staticmethod
    def _replay(batch: Batch, request_hash: str) -> BatchSubmission:
        """Return a stored result only when its request body matches this replay."""
        if batch.request_hash != request_hash:
            raise Conflict(
                "Idempotency-Key was already used with a different request body",
                code="idempotency_key_reused",
            )
        rejected = [
            RecipientRejection(index=int(str(item["index"])), reason=str(item["reason"]))
            for item in batch.rejected_recipients
        ]
        return BatchSubmission(batch, rejected, replayed=True)


class BatchFinalizationService:
    """Choose and persist a terminal batch status once all rows finish."""

    def __init__(self, batches: BatchRepository, clock: Clock) -> None:
        self._batches = batches
        self._clock = clock

    def finalize_if_finished(self, batch_id: uuid.UUID) -> BatchStatus | None:
        """Finalize success, partial success, or all-failed batches."""
        batch = self._batches.get(batch_id)
        if batch is None or batch.status not in {BatchStatus.PENDING, BatchStatus.PROCESSING}:
            return None
        counts = self._batches.get_certificate_counts(batch_id)
        if counts.get(CertificateStatus.PENDING, 0) or counts.get(
            CertificateStatus.PROCESSING,
            0,
        ):
            return None

        failures = counts.get(CertificateStatus.FAILED, 0)
        if failures == batch.total:
            status = BatchStatus.FAILED
        elif failures:
            status = BatchStatus.COMPLETED_WITH_ERRORS
        else:
            status = BatchStatus.COMPLETED
        changed = self._batches.finalize_if_no_unfinished(batch_id, status, self._clock.now())
        return status if changed else None
