"""Optional API-key protection for certificate management endpoints."""

import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _payload() -> dict[str, object]:
    return {
        "event_title": "API key integration test",
        "issuer_name": "Example Institute",
        "issue_date": "2026-10-09",
        "recipients": [{"name": "Ada Lovelace", "email": "ada@example.org"}],
    }


def test_unset_api_key_keeps_management_routes_open(client: TestClient) -> None:
    """Leaving API_KEY unset preserves the demo-friendly open behavior."""
    response = client.post("/api/batches", json=_payload())
    assert response.status_code == 202
    batch_id = response.json()["batch_id"]
    listed = client.get(f"/api/batches/{batch_id}/certificates")
    assert listed.status_code == 200


def test_api_key_protects_management_routes_but_not_public_routes(tmp_path: Path) -> None:
    """Missing/wrong keys fail consistently; the correct key and public routes work."""
    app: FastAPI = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'api-key.db'}",
            storage_dir=tmp_path / "certificates",
            worker_enabled=False,
            api_key="integration-test-secret",
        )
    )
    with TestClient(app) as client:
        protected_url = f"/api/batches/{uuid.uuid4()}"
        for headers in ({}, {"X-API-Key": "wrong-key"}):
            unauthorized = client.get(protected_url, headers=headers)
            assert unauthorized.status_code == 401
            assert unauthorized.json()["error"]["code"] == "unauthorized"

        for headers in ({}, {"X-API-Key": "wrong-key"}):
            unauthorized = client.post("/api/batches", json=_payload(), headers=headers)
            assert unauthorized.status_code == 401
            assert unauthorized.json()["error"]["code"] == "unauthorized"

        correct_headers = {"X-API-Key": "integration-test-secret"}
        created = client.post("/api/batches", json=_payload(), headers=correct_headers)
        assert created.status_code == 202
        batch_id = created.json()["batch_id"]
        assert (
            client.get(
                f"/api/batches/{batch_id}/certificates",
                headers=correct_headers,
            ).status_code
            == 200
        )
        assert client.get(f"/api/batches/{batch_id}/certificates").status_code == 401
        certificate_id = client.get(
            f"/api/batches/{batch_id}/certificates",
            headers=correct_headers,
        ).json()["items"][0]["id"]
        for protected_path in (
            f"/api/certificates/{certificate_id}/download",
            f"/api/batches/{batch_id}/download",
        ):
            assert client.get(protected_path).status_code == 401
            assert client.get(protected_path, headers={"X-API-Key": "wrong-key"}).status_code == 401

        assert client.get("/health").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200

        assert client.app.state.worker_runner.run_one_batch_synchronously() == 1
        certificate = client.get(
            f"/api/batches/{batch_id}/certificates",
            headers=correct_headers,
        ).json()["items"][0]
        assert (
            client.get(
                f"/api/certificates/{certificate['id']}/download",
                headers=correct_headers,
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/batches/{batch_id}/download",
                headers=correct_headers,
            ).status_code
            == 200
        )
        verification = client.get(f"/api/verify/{certificate['certificate_number']}")
        assert verification.status_code == 200
        assert verification.json()["valid"] is True
