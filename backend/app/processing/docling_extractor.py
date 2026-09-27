"""Docling-based PDF extraction.

Docling performs layout analysis and table-structure recognition. We keep its
tables as cell grids and rebuild visual *lines* from its text items using their
bounding boxes, because statements print one transaction per visual row even
when the layout model splits that row into several column blocks.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from app.core.config import get_settings
from app.processing.amounts import find_amounts
from app.processing.dates import DateContext, find_dates
from app.processing.types import PageContent

log = logging.getLogger(__name__)

_converter: Any = None
_converter_lock = threading.Lock()


class DoclingUnavailable(RuntimeError):
    pass


def _get_converter() -> Any:
    global _converter
    with _converter_lock:
        if _converter is None:
            try:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption
            except ImportError as exc:  # pragma: no cover - depends on install
                raise DoclingUnavailable("Docling is not installed") from exc
            settings = get_settings()
            opts = PdfPipelineOptions()
            # Scanned pages are routed to the Groq vision model instead of local OCR.
            opts.do_ocr = False
            opts.do_table_structure = settings.docling_table_structure
            _converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
        return _converter


@dataclass
class _Item:
    page: int
    text: str
    left: float
    top: float  # distance from top of page
    bottom: float

    @property
    def height(self) -> float:
        return max(0.1, self.bottom - self.top)


def _to_items(doc: Any) -> tuple[dict[int, list[_Item]], dict[int, list[list[list[str]]]]]:
    from docling_core.types.doc import TableItem, TextItem

    items: dict[int, list[_Item]] = {}
    tables: dict[int, list[list[list[str]]]] = {}
    for node, _level in doc.iterate_items():
        if not getattr(node, "prov", None):
            continue
        prov = node.prov[0]
        page_no = int(prov.page_no)
        if isinstance(node, TableItem):
            grid = [[(cell.text or "").strip() for cell in row] for row in node.data.grid]
            if grid:
                tables.setdefault(page_no, []).append(grid)
            continue
        if not isinstance(node, TextItem):
            continue
        text = (node.text or "").strip()
        if not text:
            continue
        page_height = doc.pages[page_no].size.height if page_no in doc.pages else None
        bbox = prov.bbox
        if page_height is not None:
            bbox = bbox.to_top_left_origin(page_height=page_height)
        items.setdefault(page_no, []).append(_Item(page_no, text, bbox.l, bbox.t, bbox.b))
    return items, tables


def group_lines(items: list[_Item]) -> list[str]:
    """Group text items that sit on the same visual row, ordered left-to-right."""
    if not items:
        return []
    med_h = median(i.height for i in items)
    tall = [i for i in items if i.height > 2.5 * med_h]  # paragraphs: keep as their own line
    rows: list[list[_Item]] = []
    for it in sorted((i for i in items if i not in tall), key=lambda i: (i.top, i.left)):
        center = (it.top + it.bottom) / 2
        for row in rows:
            r_top = min(x.top for x in row)
            r_bottom = max(x.bottom for x in row)
            if r_top - 1 <= center <= r_bottom + 1:
                row.append(it)
                break
        else:
            rows.append([it])
    ordered: list[tuple[float, str]] = [
        (min(x.top for x in row), "   ".join(x.text for x in sorted(row, key=lambda x: x.left))) for row in rows
    ]
    ordered += [(t.top, t.text) for t in tall]
    return [text for _, text in sorted(ordered, key=lambda x: x[0])]


# Start of a summary label phrase (split here, never inside "New Balance").
_LABEL_START = re.compile(
    r"\b(?:total\s+\w+|(?:previous|opening|closing|new|statement|outstanding|current)\s+balance|"
    r"minimum\s+(?:payment|amount|due)|payment\s+due|credit\s+limit|available\s+(?:credit|limit))",
    re.I,
)


def split_merged_rows(line: str, ctx: DateContext) -> list[str]:
    """Split a text block that contains several transactions run together (a layout
    model sometimes merges consecutive rows into one paragraph).

    Candidate split points are dates and summary labels ("Total ...", "New Balance");
    a split happens only when the text since the previous split already contains a
    description and an amount, so "date date description amount" rows stay intact."""
    points = sorted({s for s, _e, _d in find_dates(line, ctx)} | {m.start() for m in _LABEL_START.finditer(line)})
    points = [p for p in points if p > 0]
    if not points:
        return [line]
    parts: list[str] = []
    start = 0
    for s in points:
        segment = line[start:s]
        if find_amounts(segment) and re.search(r"[A-Za-z]{2}", segment):
            parts.append(segment.strip())
            start = s
    parts.append(line[start:].strip())
    return [p for p in parts if p]


def extract_pdf_with_docling(path: Path, scanned_threshold: int) -> list[PageContent]:
    converter = _get_converter()
    result = converter.convert(str(path))
    doc = result.document
    items, tables = _to_items(doc)
    page_numbers = sorted(set(doc.pages.keys()) | set(items) | set(tables))
    # Only date *positions* matter for row splitting; a default year lets year-less dates
    # ("08/28", "15 SEP") be recognized.
    ctx = DateContext(default_year=date.today().year)
    pages: list[PageContent] = []
    for n in page_numbers:
        lines: list[str] = []
        for line in group_lines(items.get(n, [])):
            lines.extend(split_merged_rows(line, ctx))
        page_tables = tables.get(n, [])
        char_count = sum(len(line) for line in lines) + sum(len(c) for t in page_tables for r in t for c in r)
        pages.append(
            PageContent(
                page_number=n,
                method="docling",
                lines=lines,
                tables=page_tables,
                is_scanned=char_count < scanned_threshold,
            )
        )
    log.info("docling extracted %d pages (%d tables)", len(pages), sum(len(p.tables) for p in pages))
    return pages


def pdf_text_layer(path: Path) -> dict[int, str]:
    """Independent text-layer read via pdfium, used to cross-check extracted amounts."""
    import pypdfium2 as pdfium

    out: dict[int, str] = {}
    pdf = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            out[i + 1] = textpage.get_text_range()
            textpage.close()
            page.close()
    finally:
        pdf.close()
    return out


def pdf_text_lines(path: Path, scanned_threshold: int) -> list[PageContent]:
    """Fallback when Docling itself fails: plain text-layer lines per page."""
    pages: list[PageContent] = []
    for n, text in pdf_text_layer(path).items():
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        pages.append(
            PageContent(
                page_number=n,
                method="pdf_text",
                lines=lines,
                is_scanned=len(text.strip()) < scanned_threshold,
                notes=["Docling failed; used the PDF text layer directly."],
            )
        )
    return pages


def render_pdf_page(path: Path, page_number: int, dpi: int) -> bytes:
    """Render one page to PNG bytes for the vision model."""
    import io

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_number - 1]
        bitmap = page.render(scale=dpi / 72)
        image = bitmap.to_pil()
        # Groq limits base64 images to ~4MB; downscale very large renders.
        max_side = 2400
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG", optimize=True)
        page.close()
        return buf.getvalue()
    finally:
        pdf.close()
