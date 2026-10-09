"""HarfBuzz shaping and vector-outline drawing for Devanagari text."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import uharfbuzz as hb  # type: ignore[import-untyped]
from fontTools.pens.basePen import BasePen  # type: ignore[import-untyped]
from fontTools.pens.transformPen import TransformPen  # type: ignore[import-untyped]
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas

FONT_FILE = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansDevanagari.ttf"


@dataclass(frozen=True)
class PositionedGlyph:
    """One HarfBuzz glyph and its pen position, in font units."""

    glyph_id: int
    cluster: int
    x_advance: int
    x_offset: int
    y_offset: int


@dataclass(frozen=True)
class _FontAssets:
    """Read-only, shared font data initialized once for worker threads."""

    harfbuzz_font: hb.Font
    glyph_set: Any
    glyph_order: tuple[str, ...]
    units_per_em: int


class _CanvasPathPen(BasePen):  # type: ignore[misc]
    """Adapt fontTools outline commands to ReportLab's PDFPathObject API."""

    def __init__(self, glyph_set: Any, path: Any) -> None:
        super().__init__(glyph_set)
        self._path = path

    def _moveTo(self, point: tuple[float, float]) -> None:
        self._path.moveTo(*point)

    def _lineTo(self, point: tuple[float, float]) -> None:
        self._path.lineTo(*point)

    def _curveToOne(
        self,
        point1: tuple[float, float],
        point2: tuple[float, float],
        point3: tuple[float, float],
    ) -> None:
        self._path.curveTo(*point1, *point2, *point3)

    def _closePath(self) -> None:
        # ReportLab names this operation close(), unlike fontTools' pen protocol.
        self._path.close()


@lru_cache(maxsize=1)
def _load_font_assets() -> _FontAssets:
    """Cache the single bundled font face and its vector glyph outlines."""
    blob = hb.Blob.from_file_path(str(FONT_FILE))
    face = hb.Face(blob)
    harfbuzz_font = hb.Font(face)
    harfbuzz_font.scale = (face.upem, face.upem)
    hb.ot_font_set_funcs(harfbuzz_font)

    font = TTFont(str(FONT_FILE), recalcBBoxes=False, recalcTimestamp=False)
    return _FontAssets(
        harfbuzz_font=harfbuzz_font,
        glyph_set=font.getGlyphSet(),
        glyph_order=tuple(font.getGlyphOrder()),
        units_per_em=face.upem,
    )


def contains_devanagari(text: str) -> bool:
    """Return whether text includes Devanagari or the Devanagari Extended block."""
    return any(
        0x0900 <= ord(character) <= 0x097F or 0xA8E0 <= ord(character) <= 0xA8FF
        for character in text
    )


def shape_text(text: str) -> list[PositionedGlyph]:
    """Shape text into ordered glyph IDs with HarfBuzz positioning data."""
    if not text:
        return []

    assets = _load_font_assets()
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.cluster_level = hb.BufferClusterLevel.MONOTONE_GRAPHEMES
    buffer.guess_segment_properties()
    hb.shape(assets.harfbuzz_font, buffer)
    return [
        PositionedGlyph(
            glyph_id=info.codepoint,
            cluster=info.cluster,
            x_advance=position.x_advance,
            x_offset=position.x_offset,
            y_offset=position.y_offset,
        )
        for info, position in zip(buffer.glyph_infos, buffer.glyph_positions, strict=True)
    ]


def text_clusters(text: str) -> list[str]:
    """Split text only at HarfBuzz grapheme-cluster boundaries."""
    if not text:
        return []
    starts = {0, len(text)}
    starts.update(glyph.cluster for glyph in shape_text(text))
    ordered_starts = sorted(starts)
    return [
        text[start:end]
        for start, end in zip(ordered_starts, ordered_starts[1:], strict=False)
        if start < end
    ]


def shaped_text_width(text: str, font_size: float) -> float:
    """Measure a shaped run using HarfBuzz advances and the font's em size."""
    assets = _load_font_assets()
    advance = sum(glyph.x_advance for glyph in shape_text(text))
    return advance * font_size / assets.units_per_em


def draw_shaped_text(
    canvas: Canvas,
    text: str,
    center_x: float,
    baseline_y: float,
    font_size: float,
) -> float:
    """Draw a centered shaped run as filled ReportLab vector paths."""
    if not text:
        return 0.0

    assets = _load_font_assets()
    glyphs = shape_text(text)
    scale = font_size / assets.units_per_em
    width = sum(glyph.x_advance for glyph in glyphs) * scale
    origin_x = center_x - width / 2
    path = canvas.beginPath()
    cursor_x = 0.0

    for glyph in glyphs:
        if glyph.glyph_id < len(assets.glyph_order):
            glyph_name = assets.glyph_order[glyph.glyph_id]
            transform = TransformPen(
                _CanvasPathPen(assets.glyph_set, path),
                (
                    scale,
                    0,
                    0,
                    scale,
                    origin_x + cursor_x + glyph.x_offset * scale,
                    baseline_y + glyph.y_offset * scale,
                ),
            )
            assets.glyph_set[glyph_name].draw(transform)
        cursor_x += glyph.x_advance * scale

    canvas.drawPath(path, stroke=0, fill=1)
    return width
