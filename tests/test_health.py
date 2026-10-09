"""Stage-one health endpoint and migration tests."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect


def test_health_checks_database_and_returns_request_id(client: TestClient) -> None:
    """The health endpoint should execute against the configured database."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert response.headers["x-request-id"]


def test_startup_applies_initial_migration(app: FastAPI, client: TestClient) -> None:
    """App startup should create tables from Alembic, not metadata.create_all."""
    inspector = inspect(app.state.database.engine)

    assert {"alembic_version", "batches", "certificates"} <= set(inspector.get_table_names())
    assert "ix_certificates_batch_status" in {
        index["name"] for index in inspector.get_indexes("certificates")
    }
