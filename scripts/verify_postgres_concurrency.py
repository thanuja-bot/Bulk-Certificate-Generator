"""Exercise four concurrent certificate workers against PostgreSQL."""

from __future__ import annotations

import shutil
import time
import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import Settings
from app.main import create_app
from app.models.batch import Batch
from app.models.certificate import Certificate

RECIPIENT_COUNT = 200


def main() -> None:
    """Run and clean up an isolated 200-recipient PostgreSQL generation batch."""
    settings = Settings(worker_enabled=False, worker_threads=4)
    app = create_app(settings)
    if app.state.database.engine.dialect.name != "postgresql":
        raise RuntimeError("Set DATABASE_URL to a disposable PostgreSQL database.")

    batch_id: uuid.UUID | None = None
    storage_path = settings.storage_dir.resolve()
    with TestClient(app) as client:
        try:
            recipients = [
                {
                    "name": f"PostgreSQL Smoke Recipient {index:03d}",
                    "email": f"postgres-smoke-{uuid.uuid4().hex[:8]}-{index}@example.org",
                }
                for index in range(RECIPIENT_COUNT)
            ]
            headers = {"Idempotency-Key": f"postgres-smoke-{uuid.uuid4()}"}
            if settings.api_key is not None:
                headers["X-API-Key"] = settings.api_key
            response = client.post(
                "/api/batches",
                json={
                    "event_title": "PostgreSQL 4-worker concurrency verification",
                    "issuer_name": "Temporary verification batch",
                    "issue_date": date.today().isoformat(),
                    "recipients": recipients,
                },
                headers=headers,
            )
            if response.status_code != 202:
                raise RuntimeError(f"Batch creation failed: {response.status_code} {response.text}")
            batch_id = uuid.UUID(response.json()["batch_id"])

            worker = client.app.state.worker_runner
            worker.start()
            deadline = time.monotonic() + 120
            progress: dict[str, object] = {}
            while time.monotonic() < deadline:
                progress_response = client.get(
                    f"/api/batches/{batch_id}",
                    headers=headers,
                )
                progress_response.raise_for_status()
                progress = progress_response.json()
                if progress["status"] in {"completed", "completed_with_errors", "failed"}:
                    break
                time.sleep(0.1)
            worker.stop()

            certificates_response = client.get(
                f"/api/batches/{batch_id}/certificates?limit={RECIPIENT_COUNT}",
                headers=headers,
            )
            certificates_response.raise_for_status()
            certificates = certificates_response.json()["items"]
            assert progress["status"] == "completed", progress
            assert progress["succeeded"] == RECIPIENT_COUNT, progress
            assert len(certificates) == RECIPIENT_COUNT
            assert len({item["id"] for item in certificates}) == RECIPIENT_COUNT
            assert all(item["status"] == "done" for item in certificates)
            assert all(item["attempts"] == 1 for item in certificates)
            print(
                f"PostgreSQL concurrency check passed: {RECIPIENT_COUNT} certificates, "
                "4 workers, one attempt per certificate."
            )
        finally:
            app.state.worker_runner.stop()
            if batch_id is not None:
                session = app.state.database.session_factory()
                try:
                    session.execute(delete(Certificate).where(Certificate.batch_id == batch_id))
                    session.execute(delete(Batch).where(Batch.id == batch_id))
                    session.commit()
                finally:
                    session.close()
                shutil.rmtree(storage_path / str(batch_id), ignore_errors=True)


if __name__ == "__main__":
    main()
