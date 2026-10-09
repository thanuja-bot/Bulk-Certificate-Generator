"""Certificate PDF and batch ZIP downloads."""

import uuid
from collections.abc import Iterator
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.api.deps import get_certificate_service, require_api_key
from app.services.certificate_service import CertificateService

router = APIRouter(tags=["certificates"], dependencies=[Depends(require_api_key)])


def _close_iterator(iterator: Iterator[bytes]) -> None:
    """Close a streaming iterator when the response has finished or was abandoned."""
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _read_chunks(file_handle: BinaryIO, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """Yield a local file in bounded chunks and close it when complete."""
    try:
        while chunk := file_handle.read(chunk_size):
            yield chunk
    finally:
        file_handle.close()


@router.get(
    "/api/certificates/{certificate_id}/download",
    summary="Download one completed certificate PDF",
)
def download_certificate(
    certificate_id: uuid.UUID,
    service: CertificateService = Depends(get_certificate_service),  # noqa: B008
) -> StreamingResponse:
    """Stream one PDF without loading the file into memory."""
    file_handle, filename = service.open_certificate(certificate_id)
    return StreamingResponse(
        _read_chunks(file_handle),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get(
    "/api/batches/{batch_id}/download",
    summary="Download completed certificates as a streamed ZIP archive",
)
def download_batch(
    batch_id: uuid.UUID,
    service: CertificateService = Depends(get_certificate_service),  # noqa: B008
) -> StreamingResponse:
    """Stream an archive generated using bounded memory."""
    archive, filename = service.open_batch_archive(batch_id)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        background=BackgroundTask(_close_iterator, archive),
    )
