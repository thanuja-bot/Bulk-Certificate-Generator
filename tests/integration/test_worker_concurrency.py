"""Concurrent worker processing tests."""

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_four_workers_process_200_certificates_exactly_once(tmp_path: Path) -> None:
    """Four live SQLite workers finish 200 rows without retries or duplicates."""
    app: FastAPI = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'concurrency.db'}",
            storage_dir=tmp_path / "certificates",
            worker_enabled=True,
            worker_threads=4,
        )
    )
    recipients = [
        {"name": f"Concurrent Recipient {index:03d}", "email": f"person-{index}@example.org"}
        for index in range(200)
    ]

    with TestClient(app) as client:
        response = client.post(
            "/api/batches",
            json={
                "event_title": "Four-worker SQLite concurrency test",
                "issuer_name": "Example Institute",
                "issue_date": "2026-10-09",
                "recipients": recipients,
            },
        )
        assert response.status_code == 202, response.text
        batch_id = response.json()["batch_id"]

        deadline = time.monotonic() + 60
        progress: dict[str, object] = {}
        while time.monotonic() < deadline:
            progress = client.get(f"/api/batches/{batch_id}").json()
            if progress["status"] in {"completed", "completed_with_errors", "failed"}:
                break
            time.sleep(0.05)

        assert progress["status"] == "completed", progress
        assert progress["succeeded"] == 200, progress
        certificates = client.get(f"/api/batches/{batch_id}/certificates?limit=200").json()["items"]
        assert len(certificates) == 200
        assert len({item["id"] for item in certificates}) == 200
        assert all(item["status"] == "done" for item in certificates)
        assert all(item["attempts"] == 1 for item in certificates)
