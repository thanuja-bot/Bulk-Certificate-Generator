"""Certificate downloads, ZIP archives, and public verification."""

from __future__ import annotations

import queue
import re
import threading
import unicodedata
import uuid
import zipfile
from collections.abc import Callable, Iterator
from typing import Any, BinaryIO, cast

from app.core.errors import Conflict, NotFound
from app.infrastructure.storage.base import StorageBackend
from app.models.certificate import Certificate, CertificateStatus
from app.repositories.batch_repository import BatchRepository
from app.repositories.certificate_repository import CertificateRepository

ARCHIVE_CHUNK_BYTES = 64 * 1024
ARCHIVE_QUEUE_CHUNKS = 8
_ARCHIVE_END = object()


def sanitize_filename(name: str, used: set[str]) -> str:
    """Convert a recipient name into a safe, unique flat PDF filename."""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized).strip("_-") or "certificate"
    base = base[:100]
    candidate = f"{base}.pdf"
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}.pdf"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


class CertificateService:
    """Read-only certificate operations used by the HTTP layer."""

    def __init__(
        self,
        batches: BatchRepository,
        certificates: CertificateRepository,
        storage: StorageBackend,
        archive_source: Callable[[uuid.UUID], Iterator[Certificate]],
    ) -> None:
        self._batches = batches
        self._certificates = certificates
        self._storage = storage
        self._archive_source = archive_source

    def open_certificate(self, certificate_id: uuid.UUID) -> tuple[BinaryIO, str]:
        """Return a ready certificate file stream and download name."""
        certificate = self._certificates.get_for_download(certificate_id)
        if certificate is None:
            raise NotFound("Certificate not found")
        if certificate.status != CertificateStatus.DONE or not certificate.file_key:
            raise Conflict("Certificate PDF is not ready")
        try:
            stream = self._storage.open_file(certificate.file_key)
        except FileNotFoundError as exc:
            raise NotFound("Certificate file is missing from storage") from exc
        filename = sanitize_filename(certificate.recipient_name, set())
        return stream, filename

    def open_batch_archive(self, batch_id: uuid.UUID) -> tuple[Iterator[bytes], str]:
        """Return a bounded-memory generator for one batch's completed PDFs."""
        batch = self._batches.get(batch_id)
        if batch is None:
            raise NotFound("Batch not found")
        if self._certificates.count_ready_for_batch(batch_id) == 0:
            raise Conflict("No generated certificates are available for this batch")
        archive_name = sanitize_filename(batch.event_title, set()).removesuffix(".pdf") + ".zip"
        return self._stream_zip(batch_id), archive_name

    def verify(self, certificate_number: str) -> dict[str, object]:
        """Return public verification details only for completed certificates."""
        certificate = self._certificates.get_by_number(certificate_number)
        if certificate is None or certificate.status != CertificateStatus.DONE:
            raise NotFound("Valid certificate not found")
        batch = certificate.batch
        return {
            "valid": True,
            "certificate_number": certificate.certificate_number,
            "recipient_name": certificate.recipient_name,
            "event_title": batch.event_title,
            "issue_date": batch.issue_date,
            "issuer": batch.issuer_name,
        }

    def _stream_zip(self, batch_id: uuid.UUID) -> Iterator[bytes]:
        """Produce a ZIP in a worker thread and transfer chunks through a bounded queue."""
        chunks: queue.Queue[object] = queue.Queue(maxsize=ARCHIVE_QUEUE_CHUNKS)
        cancelled = threading.Event()

        class QueueWriter:
            """Non-seekable file-like ZIP sink that applies backpressure."""

            def __init__(self) -> None:
                self.offset = 0

            def write(self, data: bytes) -> int:
                count = len(data)
                for start in range(0, count, ARCHIVE_CHUNK_BYTES):
                    chunk = bytes(data[start : start + ARCHIVE_CHUNK_BYTES])
                    while not cancelled.is_set():
                        try:
                            chunks.put(chunk, timeout=0.1)
                            break
                        except queue.Full:
                            continue
                    if cancelled.is_set():
                        raise BrokenPipeError("ZIP client stopped reading")
                self.offset += count
                return count

            def tell(self) -> int:
                return self.offset

            def flush(self) -> None:
                return

        def produce_archive() -> None:
            try:
                sink = QueueWriter()
                with zipfile.ZipFile(
                    cast(Any, sink),
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                    allowZip64=True,
                ) as archive:
                    certificates = self._archive_source(batch_id)
                    try:
                        for certificate in certificates:
                            if cancelled.is_set():
                                break
                            if not certificate.file_key:
                                continue
                            readable_name = sanitize_filename(
                                certificate.recipient_name,
                                set(),
                            ).removesuffix(".pdf")[:50]
                            filename = f"{readable_name}_{certificate.id.hex}.pdf"
                            with self._storage.open_file(certificate.file_key) as pdf_file:
                                with archive.open(filename, mode="w") as zip_entry:
                                    while chunk := pdf_file.read(ARCHIVE_CHUNK_BYTES):
                                        zip_entry.write(chunk)
                    finally:
                        close_source = getattr(certificates, "close", None)
                        if callable(close_source):
                            close_source()
                while not cancelled.is_set():
                    try:
                        chunks.put(_ARCHIVE_END, timeout=0.1)
                        break
                    except queue.Full:
                        continue
            except Exception as exc:
                while not cancelled.is_set():
                    try:
                        chunks.put(exc, timeout=0.1)
                        break
                    except queue.Full:
                        continue

        producer = threading.Thread(target=produce_archive, name="certificate-zip", daemon=True)
        producer.start()
        try:
            while True:
                item = chunks.get()
                if item is _ARCHIVE_END:
                    break
                if isinstance(item, Exception):
                    raise RuntimeError("Failed to build ZIP archive") from item
                if isinstance(item, bytes):
                    yield item
        finally:
            cancelled.set()
            producer.join(timeout=1)
