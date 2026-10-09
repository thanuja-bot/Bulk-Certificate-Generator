"""Generation, retrieval, ZIP safety, verification, and retry integration tests."""

import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.infrastructure.renderer.base import CertificateContent
from app.infrastructure.renderer.reportlab_renderer import ReportLabCertificateRenderer
from app.main import create_app
from app.models.batch import Batch, BatchStatus
from app.models.certificate import Certificate, CertificateStatus


def _create_batch(client: TestClient) -> str:
    """Create a two-recipient batch with accented and Devanagari names."""
    response = client.post(
        "/api/batches",
        json={
            "event_title": "Advanced Python",
            "issuer_name": "Example Institute",
            "issue_date": "2026-10-01",
            "description": "Workshop completion",
            "recipients": [
                {"name": "José Müller", "email": "jose@example.org"},
                {"name": "प्रिया शर्मा", "email": "priya@example.org"},
            ],
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["batch_id"]


def test_worker_generates_pdf_download_zip_and_public_verification(
    client: TestClient,
) -> None:
    """The whole async job path produces downloadable PDFs without public email."""
    batch_id = _create_batch(client)
    processed = client.app.state.worker_runner.run_one_batch_synchronously()

    assert processed == 2
    detail = client.get(f"/api/batches/{batch_id}").json()
    assert detail["status"] == "completed"
    assert detail["succeeded"] == 2
    assert detail["percent_complete"] == 100

    items = client.get(f"/api/batches/{batch_id}/certificates").json()["items"]
    assert len(items) == 2
    single = client.get(f"/api/certificates/{items[0]['id']}/download")
    assert single.status_code == 200
    assert single.headers["content-type"].startswith("application/pdf")
    assert single.content.startswith(b"%PDF")

    archive = client.get(f"/api/batches/{batch_id}/download")
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(BytesIO(archive.content)) as zip_file:
        names = zip_file.namelist()
        assert len(names) == 2
        assert all("/" not in name and ".." not in name for name in names)
        assert all(zip_file.read(name).startswith(b"%PDF") for name in names)

    number = items[0]["certificate_number"]
    verification = client.get(f"/api/verify/{number}")
    assert verification.status_code == 200
    assert verification.json()["valid"] is True
    assert "recipient_email" not in verification.json()
    assert "jose@example.org" not in verification.text


def test_hindi_recipient_renders_end_to_end(client: TestClient) -> None:
    """A Devanagari recipient produces a non-empty downloadable PDF."""
    response = client.post(
        "/api/batches",
        json={
            "event_title": "हिंदी प्रशिक्षण",
            "issuer_name": "उदाहरण संस्थान",
            "issue_date": "2026-10-09",
            "description": "नमस्ते दुनिया",
            "recipients": [{"name": "प्रिया शर्मा", "email": "priya@example.org"}],
        },
    )
    assert response.status_code == 202
    batch_id = response.json()["batch_id"]
    assert client.app.state.worker_runner.run_one_batch_synchronously() == 1

    certificate = client.get(f"/api/batches/{batch_id}/certificates").json()["items"][0]
    pdf = client.get(f"/api/certificates/{certificate['id']}/download")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert len(pdf.content) > 1000


class FailingRenderer:
    """Renderer test double that always fails, to exercise bounded retries."""

    def render_certificate(self, content: CertificateContent) -> bytes:
        """Raise a predictable render error."""
        raise RuntimeError(f"injected failure for {content.certificate_number}")


class FailsOnceRenderer:
    """Fail a certificate's first render attempt, then delegate to ReportLab."""

    def __init__(self) -> None:
        self.renderer = ReportLabCertificateRenderer()
        self.attempts: dict[str, int] = {}

    def render_certificate(self, content: CertificateContent) -> bytes:
        """Recover after one injected failure for every certificate number."""
        attempts = self.attempts.get(content.certificate_number, 0) + 1
        self.attempts[content.certificate_number] = attempts
        if attempts == 1:
            raise RuntimeError("injected transient failure")
        return self.renderer.render_certificate(content)


class OneRecipientFailsRenderer:
    """Fail one item while rendering all other recipients normally."""

    def __init__(self) -> None:
        self.renderer = ReportLabCertificateRenderer()

    def render_certificate(self, content: CertificateContent) -> bytes:
        """Inject a recipient-specific failure for partial-batch coverage."""
        if content.recipient_name == "José Müller":
            raise RuntimeError("injected failure")
        return self.renderer.render_certificate(content)


def test_renderer_failure_retries_then_finalizes_as_failed(tmp_path: Path) -> None:
    """A permanent renderer failure is retried once and never stops the batch."""
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'retry.db'}",
            storage_dir=tmp_path / "certificates",
            worker_enabled=False,
        ),
        certificate_renderer=FailingRenderer(),
    )
    with TestClient(app) as client:
        batch_id = _create_batch(client)
        assert client.app.state.worker_runner.run_one_batch_synchronously() == 6

        progress = client.get(f"/api/batches/{batch_id}").json()
        assert progress["status"] == "failed"
        assert progress["failed"] == 2
        certificates = client.get(f"/api/batches/{batch_id}/certificates").json()["items"]
        assert all(item["attempts"] == 3 for item in certificates)
        assert all(item["status"] == "failed" for item in certificates)


def test_transient_renderer_failure_succeeds_on_first_retry(tmp_path: Path) -> None:
    """A renderer that recovers produces a certificate on its second attempt."""
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'transient.db'}",
            storage_dir=tmp_path / "certificates",
            worker_enabled=False,
        ),
        certificate_renderer=FailsOnceRenderer(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/batches",
            json={
                "event_title": "Transient Retry Test",
                "issuer_name": "Example Institute",
                "issue_date": "2026-10-09",
                "recipients": [{"name": "Ada Lovelace", "email": "ada@example.org"}],
            },
        )
        assert response.status_code == 202
        batch_id = response.json()["batch_id"]
        assert client.app.state.worker_runner.run_one_batch_synchronously() == 2

        progress = client.get(f"/api/batches/{batch_id}").json()
        certificates = client.get(f"/api/batches/{batch_id}/certificates").json()["items"]
        assert progress["status"] == "completed"
        assert progress["succeeded"] == 1
        assert certificates[0]["attempts"] == 2
        assert certificates[0]["status"] == "done"


def test_mixed_success_and_failure_finalizes_with_errors(tmp_path: Path) -> None:
    """One terminal failure and one success yield completed_with_errors."""
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'partial.db'}",
            storage_dir=tmp_path / "certificates",
            worker_enabled=False,
        ),
        certificate_renderer=OneRecipientFailsRenderer(),
    )
    with TestClient(app) as client:
        batch_id = _create_batch(client)
        assert client.app.state.worker_runner.run_one_batch_synchronously() == 4

        progress = client.get(f"/api/batches/{batch_id}").json()
        assert progress["status"] == "completed_with_errors"
        assert progress["failed"] == 1
        assert progress["succeeded"] == 1


def test_not_ready_and_unknown_downloads_have_expected_errors(client: TestClient) -> None:
    """Pending rows return conflict and unknown IDs return not found."""
    batch_id = _create_batch(client)
    item = client.get(f"/api/batches/{batch_id}/certificates").json()["items"][0]

    pending = client.get(f"/api/certificates/{item['id']}/download")
    unknown = client.get(f"/api/certificates/{uuid.uuid4()}/download")
    assert pending.status_code == 409
    assert unknown.status_code == 404


def test_startup_recovery_resets_processing_certificates(client: TestClient) -> None:
    """Crash recovery returns processing certificates to pending and reopens batch."""
    batch_id = _create_batch(client)
    application: FastAPI = client.app
    session = application.state.database.session_factory()
    try:
        batch = session.get(Batch, uuid.UUID(batch_id))
        assert batch is not None
        certificate = session.scalar(select(Certificate).where(Certificate.batch_id == batch.id))
        assert certificate is not None
        batch.status = BatchStatus.PROCESSING
        certificate.status = CertificateStatus.PROCESSING
        session.commit()
    finally:
        session.close()

    from app.worker.recovery import recover_processing

    assert recover_processing(application.state.database.session_factory) == 1
    detail = client.get(f"/api/batches/{batch_id}").json()
    assert detail["status"] == "pending"
    assert detail["pending"] == 2
