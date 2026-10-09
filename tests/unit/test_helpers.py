"""Small unit tests for reusable validation and formatting helpers."""

import re
import uuid
from datetime import UTC, datetime

from app.infrastructure.clock import Clock
from app.infrastructure.numbering import RandomCertificateNumberGenerator
from app.infrastructure.renderer.reportlab_renderer import (
    NAME_MAX_WIDTH,
    ReportLabCertificateRenderer,
    fit_recipient_name,
    measure_text,
)
from app.infrastructure.renderer.shaping import contains_devanagari, text_clusters
from app.models.batch import Batch, BatchStatus
from app.models.certificate import CertificateStatus
from app.services.batch_service import BatchFinalizationService
from app.services.certificate_service import sanitize_filename


def test_certificate_numbers_are_unique_and_non_guessable() -> None:
    """Numbers use the public format and a high-entropy random suffix."""
    generator = RandomCertificateNumberGenerator()
    first = generator.generate()
    second = generator.generate()

    assert re.fullmatch(r"CERT-\d{4}-[A-F0-9]{16}", first)
    assert first != second


def test_filename_sanitizer_removes_paths_and_deduplicates() -> None:
    """Archive names remain flat and unique even for duplicate recipient names."""
    used: set[str] = set()

    first = sanitize_filename("../../José Müller", used)
    second = sanitize_filename("../../José Müller", used)

    assert first == "Jose_Muller.pdf"
    assert second == "Jose_Muller_2.pdf"
    assert "/" not in first and ".." not in first


def test_long_and_unicode_names_fit_the_page_width() -> None:
    """Unicode names stay inside a two-line block no smaller than 14 points."""
    ReportLabCertificateRenderer()
    for value in (
        "José Müller",
        "प्रिया शर्मा",
        "A" * 120,
        "बहुतलंबानाम" * 8,
    ):
        wrapped, size = fit_recipient_name(value, max_width=NAME_MAX_WIDTH)
        assert 14 <= size <= 34
        assert len(wrapped.splitlines()) <= 2
        assert all(measure_text(line, size) <= NAME_MAX_WIDTH for line in wrapped.splitlines())


def test_shaping_reorders_prebase_i_and_forms_devanagari_conjuncts() -> None:
    """HarfBuzz moves ि before प्र and combines conjunct glyphs."""
    from pathlib import Path

    import uharfbuzz as hb
    from fontTools.ttLib import TTFont

    font_path = (
        Path(__file__).resolve().parents[2] / "app" / "assets" / "fonts" / "NotoSansDevanagari.ttf"
    )
    font_data = hb.Blob.from_file_path(str(font_path))
    face = hb.Face(font_data)
    font = hb.Font(face)
    font.scale = (face.upem, face.upem)
    hb.ot_font_set_funcs(font)
    glyph_order = TTFont(str(font_path)).getGlyphOrder()

    buffer = hb.Buffer()
    buffer.add_str("प्रिया")
    buffer.cluster_level = hb.BufferClusterLevel.MONOTONE_GRAPHEMES
    buffer.guess_segment_properties()
    hb.shape(font, buffer)
    shaped_names = [glyph_order[info.codepoint] for info in buffer.glyph_infos]
    matra_index = next(
        index for index, name in enumerate(shaped_names) if name.startswith("uni093F")
    )
    conjunct_index = next(
        index for index, name in enumerate(shaped_names) if "uni092A094D0930" in name
    )
    assert matra_index < conjunct_index
    assert buffer.glyph_infos[matra_index].cluster == buffer.glyph_infos[conjunct_index].cluster

    conjunct_buffer = hb.Buffer()
    conjunct_buffer.add_str("क्षत्रिय")
    conjunct_buffer.guess_segment_properties()
    hb.shape(font, conjunct_buffer)
    assert len(conjunct_buffer.glyph_infos) < len("क्षत्रिय")


def test_fit_name_limits_long_inputs_to_two_measured_lines() -> None:
    """Long names and unbroken tokens fit or truncate without splitting clusters."""
    ReportLabCertificateRenderer()
    for value in ("N" * 90, "A" * 120, "प्र" * 60):
        wrapped, size = fit_recipient_name(value, NAME_MAX_WIDTH)
        lines = wrapped.splitlines()
        assert len(lines) <= 2
        assert size >= 14
        assert all(measure_text(line, size) <= NAME_MAX_WIDTH for line in lines)

    # A shaped consonant + virama + consonant sequence stays an indivisible cluster.
    assert text_clusters("प्रिया") == ["प्रि", "या"]
    assert all(cluster != "्" for cluster in text_clusters("क्षत्रिय"))
    assert contains_devanagari("Devanagari Extended: \ua8e0")


class FixedClock(Clock):
    """Stable test clock for finalization timestamps."""

    def now(self) -> datetime:
        """Return one UTC timestamp."""
        return datetime(2026, 10, 8, tzinfo=UTC)


class FinalizationRepository:
    """Minimal repository double that records the selected batch status."""

    def __init__(self, counts: dict[CertificateStatus, int]) -> None:
        self.batch = Batch(id=uuid.uuid4(), status=BatchStatus.PROCESSING, total=2)
        self.counts = counts
        self.finalized: BatchStatus | None = None

    def get(self, batch_id: uuid.UUID) -> Batch:
        return self.batch

    def get_certificate_counts(
        self,
        batch_id: uuid.UUID,
    ) -> dict[CertificateStatus, int]:
        return self.counts

    def finalize_if_no_unfinished(
        self,
        batch_id: uuid.UUID,
        status: BatchStatus,
        completed_at: datetime,
    ) -> bool:
        self.finalized = status
        return True


def test_batch_finalization_selects_success_partial_or_failed_status() -> None:
    """Final status reflects whether failures are absent, partial, or total."""
    cases = (
        ({CertificateStatus.DONE: 2}, BatchStatus.COMPLETED),
        (
            {CertificateStatus.DONE: 1, CertificateStatus.FAILED: 1},
            BatchStatus.COMPLETED_WITH_ERRORS,
        ),
        ({CertificateStatus.FAILED: 2}, BatchStatus.FAILED),
    )
    for counts, expected in cases:
        repository = FinalizationRepository(counts)
        result = BatchFinalizationService(repository, FixedClock()).finalize_if_finished(
            repository.batch.id
        )
        assert result == expected
        assert repository.finalized == expected


def test_batch_is_not_finalized_while_work_remains() -> None:
    """Pending and processing certificate rows keep a batch open."""
    repository = FinalizationRepository({CertificateStatus.DONE: 1, CertificateStatus.PENDING: 1})
    result = BatchFinalizationService(repository, FixedClock()).finalize_if_finished(
        repository.batch.id
    )
    assert result is None
    assert repository.finalized is None
