"""Thread-pool worker that claims queued certificate rows from the database."""

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait

from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import batch_id_context, certificate_id_context
from app.infrastructure.clock import Clock
from app.infrastructure.renderer.base import CertificateRenderer
from app.infrastructure.storage.base import StorageBackend
from app.models.certificate import CertificateStatus
from app.repositories.batch_repository import BatchRepository
from app.repositories.certificate_repository import CertificateRepository
from app.services.batch_service import BatchFinalizationService
from app.services.generation_service import GenerationService

logger = logging.getLogger(__name__)


class WorkerRunner:
    """Poll and process pending rows with a bounded pool of worker threads."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        renderer: CertificateRenderer,
        storage: StorageBackend,
        public_base_url: str,
        clock: Clock,
        *,
        threads: int,
        poll_interval: float = 0.25,
    ) -> None:
        self._session_factory = session_factory
        self._renderer = renderer
        self._storage = storage
        self._public_base_url = public_base_url
        self._clock = clock
        self._threads = threads
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._coordinator: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None

    def start(self) -> None:
        """Start the coordinator and fixed-size worker pool."""
        if self._coordinator and self._coordinator.is_alive():
            return
        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self._threads,
            thread_name_prefix="certificate-worker",
        )
        self._coordinator = threading.Thread(
            target=self._run,
            name="certificate-coordinator",
            daemon=True,
        )
        self._coordinator.start()

    def stop(self) -> None:
        """Stop claiming work and wait for in-flight jobs to finish."""
        self._stop_event.set()
        if self._coordinator:
            self._coordinator.join()
            self._coordinator = None
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None

    def run_one_batch_synchronously(self, chunk_size: int | None = None) -> int:
        """Deterministically process queued jobs; intended for tests and scripts."""
        processed = 0
        while True:
            claimed = self._claim(chunk_size or self._threads)
            if not claimed:
                return processed
            for certificate_id in claimed:
                self._process_one(certificate_id)
                processed += 1

    def _run(self) -> None:
        """Fill available worker slots, collect completions, and poll when idle."""
        assert self._executor is not None
        futures: set[Future[None]] = set()
        while not self._stop_event.is_set():
            futures = {future for future in futures if not future.done()}
            available = self._threads - len(futures)
            if available > 0:
                for certificate_id in self._claim(available):
                    futures.add(self._executor.submit(self._process_one, certificate_id))
            if not futures:
                self._stop_event.wait(self._poll_interval)
            else:
                wait(futures, timeout=self._poll_interval)
        if futures:
            wait(futures)

    def _claim(self, limit: int) -> list[uuid.UUID]:
        """Claim a small set of rows in a short-lived coordinator session."""
        session = self._session_factory()
        try:
            return CertificateRepository(session).claim_pending(limit)
        except Exception:
            session.rollback()
            logger.exception("Worker failed while claiming pending certificates")
            return []
        finally:
            session.close()

    def _process_one(self, certificate_id: uuid.UUID) -> None:
        """Generate one file, record its result, and try to finalize its batch."""
        certificate_token = certificate_id_context.set(str(certificate_id))
        session = self._session_factory()
        try:
            certificates = CertificateRepository(session)
            result = certificates.get_render_data(certificate_id)
            if result is None:
                logger.error("Claimed certificate could not be loaded")
                return
            certificate, batch = result
            batch_token = batch_id_context.set(str(batch.id))
            generator = GenerationService(
                certificates=certificates,
                renderer=self._renderer,
                storage=self._storage,
                public_base_url=self._public_base_url,
            )
            try:
                file_key = generator.generate_certificate(certificate, batch)
                batch_id = certificates.mark_done(certificate_id, file_key)
                if batch_id:
                    BatchFinalizationService(
                        BatchRepository(session),
                        self._clock,
                    ).finalize_if_finished(batch_id)
                    logger.info("Generated certificate PDF")
            except Exception as exc:
                logger.exception("Certificate generation failed")
                session.rollback()
                self._record_failure(certificate_id, exc)
            finally:
                batch_id_context.reset(batch_token)
        except Exception as exc:
            session.rollback()
            logger.exception("Worker failed to process certificate")
            self._record_failure(certificate_id, exc)
        finally:
            session.close()
            certificate_id_context.reset(certificate_token)

    def _record_failure(self, certificate_id: uuid.UUID, error: Exception) -> None:
        """Use a fresh transaction to retry or finalize after an unexpected job error."""
        session = self._session_factory()
        try:
            certificates = CertificateRepository(session)
            certificate = certificates.get(certificate_id)
            if certificate is None:
                return
            batch_id: uuid.UUID | None
            if certificate.status in {CertificateStatus.DONE, CertificateStatus.FAILED}:
                batch_id = certificate.batch_id
            else:
                generator = GenerationService(
                    certificates=certificates,
                    renderer=self._renderer,
                    storage=self._storage,
                    public_base_url=self._public_base_url,
                )
                batch_id = generator.record_failure(certificate_id, error)
            if batch_id:
                BatchFinalizationService(
                    BatchRepository(session),
                    self._clock,
                ).finalize_if_finished(batch_id)
                logger.warning(
                    "Certificate attempt ended",
                    extra={"batch_id": str(batch_id)},
                )
        except Exception:
            session.rollback()
            logger.exception("Worker could not record certificate failure")
        finally:
            session.close()
