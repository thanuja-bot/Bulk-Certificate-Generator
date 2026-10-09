"""Public certificate verification route."""

from fastapi import APIRouter, Depends

from app.api.deps import get_certificate_service
from app.schemas.certificate import VerificationResponse
from app.services.certificate_service import CertificateService

router = APIRouter(prefix="/api/verify", tags=["verification"])


@router.get(
    "/{certificate_number}",
    response_model=VerificationResponse,
    summary="Publicly verify a certificate number",
)
def verify_certificate(
    certificate_number: str,
    service: CertificateService = Depends(get_certificate_service),  # noqa: B008
) -> VerificationResponse:
    """Return verified certificate fields; recipient email is never exposed."""
    return VerificationResponse.model_validate(service.verify(certificate_number))
