"""Shared temporary-app fixtures for integration tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """Build an app backed by an isolated SQLite database."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "certificates",
        worker_enabled=False,
    )
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Yield a client while the app lifespan is active."""
    with TestClient(app) as test_client:
        yield test_client
