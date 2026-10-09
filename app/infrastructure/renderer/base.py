"""Renderer interface and immutable certificate content."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class CertificateContent:
    """Only the fields needed to render one certificate."""

    certificate_number: str
    recipient_name: str
    event_title: str
    description: str | None
    issue_date: date
    issuer_name: str
    verification_url: str


class CertificateRenderer(Protocol):
    """Contract for a PDF renderer."""

    def render_certificate(self, content: CertificateContent) -> bytes:
        """Return a complete PDF document."""
