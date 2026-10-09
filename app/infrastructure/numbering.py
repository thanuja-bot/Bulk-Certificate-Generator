"""Non-guessable certificate number generator."""

import secrets
from datetime import UTC, datetime
from typing import Protocol


class CertificateNumberGenerator(Protocol):
    """Contract for generating unique public certificate identifiers."""

    def generate(self) -> str:
        """Return one high-entropy public number."""


class RandomCertificateNumberGenerator(CertificateNumberGenerator):
    """Generate CERT-year- plus 64 bits of random hexadecimal entropy."""

    def generate(self) -> str:
        """Return a short, URL-safe verification identifier."""
        year = datetime.now(UTC).year
        return f"CERT-{year}-{secrets.token_hex(8).upper()}"
