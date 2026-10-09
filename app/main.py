"""ASGI entrypoint and application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from alembic import command
from alembic.config import Config
from app import models  # noqa: F401 - register ORM tables in Base.metadata
from app.api.routes.batches import router as batches_router
from app.api.routes.certificates import router as certificates_router
from app.api.routes.health import router as health_router
from app.api.routes.verify import router as verify_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, request_id_context
from app.db.session import Database, create_database
from app.infrastructure.clock import Clock, SystemClock
from app.infrastructure.numbering import (
    CertificateNumberGenerator,
    RandomCertificateNumberGenerator,
)
from app.infrastructure.renderer.base import CertificateRenderer
from app.infrastructure.renderer.reportlab_renderer import ReportLabCertificateRenderer
from app.infrastructure.storage.base import StorageBackend
from app.infrastructure.storage.local import LocalDiskStorage
from app.worker.recovery import recover_processing
from app.worker.runner import WorkerRunner

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to logs and return it to the caller."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_context.reset(token)


class RequestBodyTooLarge(Exception):
    """Raised when a request body exceeds the configured byte limit."""


class BodySizeLimitMiddleware:
    """Reject oversized HTTP request bodies, including chunked bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await self._respond_too_large(scope, receive, send)
                    return
            except ValueError:
                await self._respond_too_large(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._respond_too_large(scope, receive, send)

    @staticmethod
    async def _respond_too_large(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_body_too_large",
                    "message": "Request body exceeds the configured size limit",
                    "details": None,
                }
            },
        )
        await response(scope, receive, send)


def run_migrations(database: Database) -> None:
    """Run checked-in Alembic migrations against this app's configured database."""
    config_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    config = Config(str(config_path))
    database_url = database.engine.url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["app_supplied_database_url"] = True
    command.upgrade(config, "head")


def create_app(
    settings: Settings | None = None,
    *,
    storage_backend: StorageBackend | None = None,
    certificate_renderer: CertificateRenderer | None = None,
    number_generator: CertificateNumberGenerator | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create an independently configured FastAPI application."""
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    database = create_database(app_settings.effective_database_url)
    storage = storage_backend or LocalDiskStorage(app_settings.storage_dir)
    renderer = certificate_renderer or ReportLabCertificateRenderer()
    number_generator = number_generator or RandomCertificateNumberGenerator()
    app_clock = clock or SystemClock()
    worker = WorkerRunner(
        database.session_factory,
        renderer,
        storage,
        app_settings.public_base_url,
        app_clock,
        threads=app_settings.worker_threads,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            run_migrations(database)
            recover_processing(database.session_factory)
            if app_settings.worker_enabled:
                worker.start()
            yield
        finally:
            worker.stop()
            database.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        description="Submit bulk certificate jobs and retrieve generated certificates.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database
    app.state.storage = storage
    app.state.renderer = renderer
    app.state.number_generator = number_generator
    app.state.clock = app_clock
    app.state.worker_runner = worker
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=app_settings.request_body_limit_bytes)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(batches_router)
    app.include_router(certificates_router)
    app.include_router(verify_router)
    return app


app = create_app()
