"""Batch submission, progress, and idempotency API tests."""

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _payload() -> dict[str, object]:
    return {
        "event_title": "Python Fundamentals",
        "issuer_name": "Northstar Academy",
        "issue_date": "2026-09-30",
        "description": "Completion of the course",
        "recipients": [
            {"name": "  José Müller  ", "email": "JOSE@Example.com", "extra": {"team": "A"}},
            {"name": " ", "email": "bad@example.com"},
            {"name": "Duplicate", "email": "jose@example.com"},
            42,
        ],
    }


def test_create_batch_accepts_valid_recipients_and_reports_rejections(
    client: TestClient,
) -> None:
    """One malformed or duplicate recipient does not reject the whole submission."""
    response = client.post(
        "/api/batches",
        json=_payload(),
        headers={"Idempotency-Key": "customer-request-1"},
    )

    assert response.status_code == 202
    result = response.json()
    assert result["accepted_count"] == 1
    assert [item["index"] for item in result["rejected"]] == [1, 2, 3]

    batch_id = result["batch_id"]
    detail = client.get(result["status_url"]).json()
    assert detail["batch_id"] == batch_id
    assert detail["total"] == 1
    assert detail["pending"] == 1
    assert detail["percent_complete"] == 0

    listed = client.get(f"/api/batches/{batch_id}/certificates?limit=10&offset=0")
    assert listed.status_code == 200
    assert listed.json()["page"]["total"] == 1
    recipient = listed.json()["items"][0]
    assert recipient["recipient_name"] == "José Müller"
    assert recipient["recipient_email"] == "jose@example.com"


def test_idempotency_key_returns_the_existing_batch(client: TestClient) -> None:
    """Repeating a request key returns the original rows and original rejections."""
    first = client.post(
        "/api/batches",
        json=_payload(),
        headers={"Idempotency-Key": "stable-key"},
    )
    second = client.post(
        "/api/batches",
        json=_payload(),
        headers={"Idempotency-Key": "stable-key"},
    )

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json() == first.json()
    certificates = client.get(f"{first.json()['status_url']}/certificates")
    assert certificates.json()["page"]["total"] == 1


def test_idempotency_key_rejects_a_different_request_body(client: TestClient) -> None:
    """A request key cannot silently return a batch created from another body."""
    first = client.post(
        "/api/batches",
        json=_payload(),
        headers={"Idempotency-Key": "body-bound-key"},
    )
    changed_payload = _payload()
    changed_payload["event_title"] = "A different event"
    second = client.post(
        "/api/batches",
        json=changed_payload,
        headers={"Idempotency-Key": "body-bound-key"},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_reused"
    assert "different request body" in second.json()["error"]["message"]


def test_idempotency_hash_ignores_json_object_key_order(client: TestClient) -> None:
    """Canonical request hashes treat reordered JSON object keys as the same body."""
    first_payload = _payload()
    first_payload["recipients"] = [
        {
            "name": "Ada Lovelace",
            "email": "ada@example.org",
            "extra": {"team": "A", "department": "Engineering"},
        }
    ]
    recipient = first_payload["recipients"][0]
    assert isinstance(recipient, dict)
    reordered_payload = dict(reversed(list(first_payload.items())))
    reordered_payload["recipients"] = [dict(reversed(list(recipient.items())))]

    first = client.post(
        "/api/batches",
        json=first_payload,
        headers={"Idempotency-Key": "canonical-key"},
    )
    second = client.post(
        "/api/batches",
        json=reordered_payload,
        headers={"Idempotency-Key": "canonical-key"},
    )

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["batch_id"] == first.json()["batch_id"]


def test_all_invalid_and_over_limit_requests_return_422(client: TestClient) -> None:
    """Empty success sets and recipient-count overages are explicit validation errors."""
    no_valid = _payload()
    no_valid["recipients"] = [
        {"name": "", "email": "not-an-email"},
        17,
    ]
    response = client.post("/api/batches", json=no_valid)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"

    too_many = _payload()
    too_many["recipients"] = [0] * 5001
    over_limit = client.post("/api/batches", json=too_many)
    assert over_limit.status_code == 422
    assert over_limit.json()["error"]["details"]["maximum"] == 5000


def test_invalid_recipient_is_partial_but_invalid_top_level_body_is_not(
    client: TestClient,
) -> None:
    """The raw recipient list is item-validated, while the batch envelope is typed."""
    response = client.post("/api/batches", json={"event_title": "missing other fields"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"


def test_request_body_limit_returns_structured_413(tmp_path: Path) -> None:
    """Oversized requests are rejected before the route reads or stores them."""
    app: FastAPI = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'body-limit.db'}",
            storage_dir=tmp_path / "certificates",
            worker_enabled=False,
            request_body_limit_bytes=32,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/batches",
            content=b"x" * 33,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_body_too_large"


def test_lifespan_worker_picks_up_new_batch_and_stops_cleanly(tmp_path: Path) -> None:
    """The configured worker pool processes a submitted batch during app lifespan."""
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'live-worker.db'}",
            storage_dir=tmp_path / "certificates",
            worker_enabled=True,
            worker_threads=2,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/batches",
            json={
                "event_title": "Worker Smoke Test",
                "issuer_name": "Example Institute",
                "issue_date": "2026-10-01",
                "recipients": [{"name": "Ada Lovelace", "email": "ada@example.org"}],
            },
        )
        assert response.status_code == 202
        batch_id = response.json()["batch_id"]

        deadline = time.monotonic() + 5
        status = "pending"
        while time.monotonic() < deadline:
            status = client.get(f"/api/batches/{batch_id}").json()["status"]
            if status == "completed":
                break
            time.sleep(0.05)

        assert status == "completed"
    assert isinstance(app, FastAPI)
