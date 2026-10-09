"""Render and store one certificate."""

import uuid

from app.infrastructure.renderer.base import CertificateContent, CertificateRenderer
from app.infrastructure.storage.base import StorageBackend
from app.models.batch import Batch
from app.models.certificate import Certificate, CertificateStatus
from app.repositories.certificate_repository import CertificateRepository

MAX_GENERATION_ATTEMPTS = 3  # One initial attempt plus the two retries in the service contract.


class GenerationService:
    """Use case for converting one claimed certificate row into a PDF."""

    def __init__(
        self,
        certificates: CertificateRepository,
        renderer: CertificateRenderer,
        storage: StorageBackend,
        public_base_url: str,
    ) -> None:
        self._certificates = certificates
        self._renderer = renderer
        self._storage = storage
        self._public_base_url = public_base_url.rstrip("/")

    def generate_certificate(self, certificate: Certificate, batch: Batch) -> str:
        """Render and store one processing certificate, returning its opaque file key."""
        if certificate.status != CertificateStatus.PROCESSING:
            raise RuntimeError("Certificate is not claimed for processing")

        payload = CertificateContent(
            certificate_number=certificate.certificate_number,
            recipient_name=certificate.recipient_name,
            event_title=batch.event_title,
            description=batch.description,
            issue_date=batch.issue_date,
            issuer_name=batch.issuer_name,
            verification_url=(
                f"{self._public_base_url}/api/verify/{certificate.certificate_number}"
            ),
        )
        pdf_bytes = self._renderer.render_certificate(payload)
        file_key = f"{batch.id}/{certificate.id}.pdf"
        self._storage.save(file_key, pdf_bytes)
        return file_key

    def record_failure(self, certificate_id: uuid.UUID, error: Exception) -> uuid.UUID | None:
        """Retry twice, then persist terminal failure through the repository."""
        certificate = self._certificates.get(certificate_id)
        if certificate is None or certificate.status != CertificateStatus.PROCESSING:
            return None
        terminal = certificate.attempts >= MAX_GENERATION_ATTEMPTS
        message = f"Certificate generation failed ({type(error).__name__})"
        return self._certificates.mark_attempt_failed(
            certificate_id,
            message,
            terminal_failure=terminal,
        )
