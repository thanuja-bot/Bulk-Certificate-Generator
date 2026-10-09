"""One-page landscape A4 certificate renderer using ReportLab."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from app.infrastructure.renderer.base import CertificateContent, CertificateRenderer
from app.infrastructure.renderer.shaping import (
    contains_devanagari,
    draw_shaped_text,
    shaped_text_width,
    text_clusters,
)

FONT_NAME = "NotoSansDevanagari"
PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
PAGE_MARGIN = 34
NAME_MAX_WIDTH = PAGE_WIDTH - 2 * PAGE_MARGIN - 80
FONT_FILE = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansDevanagari.ttf"


def _wrap_measured(text: str, max_width: float, font_size: float) -> list[str]:
    """Wrap at spaces and only split an overlong token at grapheme boundaries."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if measure_text(candidate, font_size) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if measure_text(word, font_size) <= max_width:
            current = word
            continue
        segment = ""
        for cluster in text_clusters(word):
            candidate_segment = segment + cluster
            if segment and measure_text(candidate_segment, font_size) > max_width:
                lines.append(segment)
                segment = cluster
            else:
                segment = candidate_segment
        current = segment
    if current:
        lines.append(current)
    return lines


def measure_text(text: str, font_size: float) -> float:
    """Measure complex-script text after shaping and other text by ReportLab metrics."""
    if contains_devanagari(text):
        return shaped_text_width(text, font_size)
    return float(pdfmetrics.stringWidth(text, FONT_NAME, font_size))


def _truncate_with_ellipsis(text: str, width: float, font_size: float) -> str:
    """Truncate only between shaped grapheme clusters while keeping an ellipsis visible."""
    ellipsis = "…"
    if measure_text(ellipsis, font_size) > width:
        return ""
    result = ""
    for cluster in text_clusters(text):
        candidate = result + cluster
        if measure_text(candidate + ellipsis, font_size) > width:
            break
        result = candidate
    return f"{result.rstrip()}{ellipsis}"


def fit_recipient_name(
    name: str,
    max_width: float,
    preferred_size: float = 34,
) -> tuple[str, float]:
    """Fit a recipient on at most two lines, shrinking no smaller than 14 points."""
    clean_name = " ".join(name.split()) or "Participant"
    starting_size = max(14, min(34, int(preferred_size)))
    for font_size in range(starting_size, 13, -1):
        lines = _wrap_measured(clean_name, max_width, float(font_size))
        if len(lines) <= 2 and all(measure_text(line, font_size) <= max_width for line in lines):
            return "\n".join(lines), font_size

    lines = _wrap_measured(clean_name, max_width, 14.0)
    if not lines:
        return "…", 14
    lines = lines[:2]
    lines[-1] = _truncate_with_ellipsis(lines[-1], max_width, 14.0)
    return "\n".join(lines), 14


def _wrap_to_width(text: str, width: float, font_size: float, max_lines: int) -> list[str]:
    """Wrap a short text field to measured canvas width."""
    lines = _wrap_measured(text, width, font_size)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _truncate_with_ellipsis(lines[-1], width, font_size)
    return lines


def _draw_centered_text(
    canvas: Canvas,
    text: str,
    center_x: float,
    baseline_y: float,
    font_size: float,
) -> None:
    """Keep ordinary Latin selectable while drawing Devanagari with shaped outlines."""
    if contains_devanagari(text):
        draw_shaped_text(canvas, text, center_x, baseline_y, font_size)
        return
    canvas.setFont(FONT_NAME, font_size)
    canvas.drawCentredString(center_x, baseline_y, text)


class ReportLabCertificateRenderer(CertificateRenderer):
    """Draw a decorative certificate and verification QR code."""

    def __init__(self) -> None:
        if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_FILE)))

    def render_certificate(self, content: CertificateContent) -> bytes:
        """Render one landscape certificate as PDF bytes."""
        output = BytesIO()
        canvas = Canvas(output, pagesize=landscape(A4), pageCompression=1)
        canvas.setTitle(f"Certificate - {content.recipient_name}")
        canvas.setAuthor(content.issuer_name)
        self._draw_border(canvas)

        center_x = PAGE_WIDTH / 2
        canvas.setFillColor(colors.HexColor("#183153"))
        canvas.setFont(FONT_NAME, 28)
        canvas.drawCentredString(center_x, PAGE_HEIGHT - 100, "Certificate of Completion")

        canvas.setFillColor(colors.HexColor("#B17A2B"))
        canvas.setStrokeColor(colors.HexColor("#B17A2B"))
        canvas.setLineWidth(1.2)
        canvas.line(center_x - 76, PAGE_HEIGHT - 116, center_x + 76, PAGE_HEIGHT - 116)

        canvas.setFillColor(colors.HexColor("#3D4A5C"))
        canvas.setFont(FONT_NAME, 13)
        canvas.drawCentredString(center_x, PAGE_HEIGHT - 151, "This certificate is awarded to")

        wrapped_name, name_size = fit_recipient_name(content.recipient_name, NAME_MAX_WIDTH)
        name_lines = wrapped_name.splitlines()
        canvas.setFillColor(colors.HexColor("#183153"))
        name_start_y = PAGE_HEIGHT - 204
        line_height = name_size * 1.2
        for index, line in enumerate(name_lines):
            _draw_centered_text(
                canvas,
                line,
                center_x,
                name_start_y - index * line_height,
                name_size,
            )

        last_name_y = name_start_y - (len(name_lines) - 1) * line_height
        after_name_y = last_name_y - max(43, name_size * 1.25)
        canvas.setFillColor(colors.HexColor("#3D4A5C"))
        canvas.setFont(FONT_NAME, 12)
        canvas.drawCentredString(center_x, after_name_y, "for successfully completing")

        canvas.setFillColor(colors.HexColor("#183153"))
        event_lines = _wrap_to_width(
            content.event_title,
            PAGE_WIDTH - 2 * PAGE_MARGIN - 110,
            20,
            2,
        )
        event_y = after_name_y - 37
        for index, line in enumerate(event_lines):
            _draw_centered_text(canvas, line, center_x, event_y - index * 24, 20)

        details_y = event_y - (len(event_lines) - 1) * 24 - 37
        if content.description:
            canvas.setFillColor(colors.HexColor("#4B5563"))
            description_lines = _wrap_to_width(
                content.description,
                PAGE_WIDTH - 2 * PAGE_MARGIN - 140,
                10,
                2,
            )
            for index, line in enumerate(description_lines):
                _draw_centered_text(canvas, line, center_x, details_y - index * 15, 10)
            details_y -= len(description_lines) * 15

        canvas.setFillColor(colors.HexColor("#3D4A5C"))
        _draw_centered_text(
            canvas,
            f"Issued {content.issue_date.isoformat()} by {content.issuer_name}",
            center_x,
            details_y - 22,
            11,
        )

        signature_y = PAGE_MARGIN + 43
        canvas.setStrokeColor(colors.HexColor("#9AA4B2"))
        canvas.setLineWidth(0.8)
        canvas.line(center_x - 76, signature_y, center_x + 76, signature_y)
        canvas.setFillColor(colors.HexColor("#3D4A5C"))
        signature_name = _wrap_to_width(content.issuer_name, 190, 9, 1)[0]
        _draw_centered_text(
            canvas,
            signature_name,
            center_x,
            signature_y + 7,
            9,
        )

        self._draw_qr(canvas, content.verification_url)
        canvas.setFillColor(colors.HexColor("#5B6573"))
        canvas.setFont(FONT_NAME, 8)
        canvas.drawString(PAGE_MARGIN + 10, PAGE_MARGIN + 21, content.certificate_number)
        canvas.drawRightString(
            PAGE_WIDTH - PAGE_MARGIN - 10,
            PAGE_MARGIN + 21,
            "Scan to verify this certificate",
        )

        canvas.showPage()
        canvas.save()
        return output.getvalue()

    @staticmethod
    def _draw_border(canvas: Canvas) -> None:
        """Draw a simple double-line border with restrained corner accents."""
        canvas.setStrokeColor(colors.HexColor("#183153"))
        canvas.setLineWidth(2)
        canvas.roundRect(
            PAGE_MARGIN,
            PAGE_MARGIN,
            PAGE_WIDTH - 2 * PAGE_MARGIN,
            PAGE_HEIGHT - 2 * PAGE_MARGIN,
            8,
            stroke=1,
            fill=0,
        )
        canvas.setStrokeColor(colors.HexColor("#C9A45C"))
        canvas.setLineWidth(0.8)
        canvas.roundRect(
            PAGE_MARGIN + 7,
            PAGE_MARGIN + 7,
            PAGE_WIDTH - 2 * (PAGE_MARGIN + 7),
            PAGE_HEIGHT - 2 * (PAGE_MARGIN + 7),
            5,
            stroke=1,
            fill=0,
        )

    @staticmethod
    def _draw_qr(canvas: Canvas, verification_url: str) -> None:
        """Draw a compact verification QR code in the lower-left corner."""
        code = qrcode.QRCode(version=None, box_size=4, border=1)
        code.add_data(verification_url)
        code.make(fit=True)
        image = code.make_image(fill_color="#183153", back_color="white")
        image_buffer = BytesIO()
        image.save(image_buffer, format="PNG")
        image_buffer.seek(0)
        canvas.drawImage(
            ImageReader(image_buffer),
            PAGE_MARGIN + 12,
            PAGE_MARGIN + 35,
            width=62,
            height=62,
            preserveAspectRatio=True,
            mask="auto",
        )
