"""The statement analysis pipeline.

Upload → validation (at upload time) → Docling extraction → vision fallback →
transaction parsing → normalization → validation → database storage.

Each stage writes a ProcessingEvent and updates the statement status so the UI
reflects real backend state.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AnalysisJob,
    Document,
    ExtractedPage,
    Statement,
    StatementStatus,
    Transaction,
)
from app.processing import docling_extractor
from app.processing.amounts import parse_amount
from app.processing.dates import DateContext, detect_order, parse_date
from app.processing.metadata import detect_metadata
from app.processing.normalize import normalize_all
from app.processing.parsing import parse_page, transaction_signal_count
from app.processing.types import NormalizedTransaction, PageContent, RawTransaction, StatementMeta
from app.processing.validate import (
    cross_check_totals,
    flag_possible_duplicates,
    remove_extraction_duplicates,
    text_layer_check,
    validate_fields,
)
from app.processing.vision import GroqVisionClient, VisionError, VisionPageResult, VisionUnavailable
from app.services import storage
from app.services.events import emit

log = logging.getLogger(__name__)


class VisionClient(Protocol):
    def extract_page(self, image: bytes, mime: str, page_number: int) -> VisionPageResult: ...


class PipelineError(RuntimeError):
    """A failure whose message is safe to show to the user."""


@dataclass
class ExtractionResult:
    pages: list[PageContent]
    vision_results: list[VisionPageResult] = field(default_factory=list)
    text_layer: dict[int, str] = field(default_factory=dict)
    vision_pages_skipped: list[int] = field(default_factory=list)
    vision_unavailable_reason: str | None = None
    methods: set[str] = field(default_factory=set)


def _default_vision_factory() -> VisionClient:
    return GroqVisionClient()


class Pipeline:
    def __init__(self, db: Session, statement_id: uuid.UUID, job_id: uuid.UUID | None = None,
                 vision_factory: Any = None) -> None:
        self.db = db
        self.statement_id = statement_id
        self.job_id = job_id
        self.settings = get_settings()
        self._vision_factory = vision_factory or _default_vision_factory
        self._vision: VisionClient | None = None

    # -- helpers ---------------------------------------------------------------
    def _stage(self, status: StatementStatus, message: str, **data: Any) -> None:
        stmt = self.db.get(Statement, self.statement_id)
        assert stmt is not None
        stmt.status = status.value
        if self.job_id:
            job = self.db.get(AnalysisJob, self.job_id)
            if job:
                job.stage = status.value
        emit(self.db, self.statement_id, status.value, message, job_id=self.job_id, data=data)

    def _event(self, stage: str, message: str, level: str = "info", **data: Any) -> None:
        emit(self.db, self.statement_id, stage, message, level=level, job_id=self.job_id, data=data)

    def _vision_client(self) -> VisionClient:
        if self._vision is None:
            self._vision = self._vision_factory()
        return self._vision

    # -- stages ----------------------------------------------------------------
    def run(self) -> None:
        stmt = self.db.get(Statement, self.statement_id)
        if stmt is None:
            raise PipelineError("Statement no longer exists.")
        doc = self.db.scalar(select(Document).where(Document.statement_id == stmt.id))
        if doc is None:
            raise PipelineError("The uploaded document is missing.")
        path = storage.path_for_key(doc.storage_key)
        if not path.exists():
            raise PipelineError("The stored file is missing; please upload it again.")

        self._stage(StatementStatus.extracting, "Extracting document content", kind=doc.kind, pages=doc.page_count)
        extraction = self._extract(doc, path)

        self._stage(StatementStatus.structuring, "Parsing and normalizing transactions")
        meta, ctx, txns = self._structure(extraction)

        self._stage(StatementStatus.verifying, "Validating extracted transactions", candidates=len(txns))
        txns, verification = self._verify(txns, meta, extraction)

        if not txns:
            if extraction.vision_unavailable_reason:
                raise PipelineError(
                    "No transactions could be extracted. This document appears to be scanned or image-based, "
                    "and vision extraction is unavailable: " + extraction.vision_unavailable_reason
                )
            verification["checks"].append({
                "name": "transactions_found", "label": "Transactions found", "status": "warn",
                "message": "No transaction rows were recognized in this document.",
            })

        self._store(stmt, doc, extraction, meta, txns, verification)
        n_review = sum(1 for t in txns if t.review_status == "needs_review")
        self._stage(
            StatementStatus.ready,
            f"Ready: {len(txns)} transactions extracted, {n_review} flagged for review",
            transactions=len(txns),
            needs_review=n_review,
        )

    def _extract(self, doc: Document, path: Path) -> ExtractionResult:
        threshold = self.settings.scanned_page_char_threshold
        if doc.kind == "image":
            result = ExtractionResult(pages=[], methods={"vision"})
            self._event("extracting", "Image upload: sending to the vision model")
            try:
                vr = self._vision_client().extract_page(path.read_bytes(), doc.mime_type, 1)
            except VisionUnavailable as exc:
                raise PipelineError(str(exc)) from exc
            except VisionError as exc:
                raise PipelineError(f"Vision extraction failed: {exc}") from exc
            result.vision_results.append(vr)
            result.pages.append(vr.to_page_content())
            self._event("extracting", f"Vision model read {len(vr.transactions)} transaction rows", rows=len(vr.transactions))
            return result

        # PDF: Docling first.
        result = ExtractionResult(pages=[])
        try:
            result.text_layer = docling_extractor.pdf_text_layer(path)
        except Exception:  # noqa: BLE001 - verification aid only
            log.warning("pdf text layer read failed for statement %s", self.statement_id)
        pages: list[PageContent]
        if self.settings.docling_enabled:
            try:
                pages = docling_extractor.extract_pdf_with_docling(path, threshold)
                result.methods.add("docling")
                self._event(
                    "extracting",
                    f"Docling analyzed {len(pages)} page(s) and found {sum(len(p.tables) for p in pages)} table(s)",
                    pages=len(pages), tables=sum(len(p.tables) for p in pages),
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("docling failed for statement %s", self.statement_id)
                self._event("extracting", f"Docling failed ({type(exc).__name__}); using the PDF text layer", "warning")
                pages = docling_extractor.pdf_text_lines(path, threshold)
                result.methods.add("pdf_text")
        else:
            pages = docling_extractor.pdf_text_lines(path, threshold)
            result.methods.add("pdf_text")

        # Decide which pages need the vision fallback.
        order, confident = detect_order([p.text for p in pages])
        probe_ctx = DateContext(order=order, order_confident=confident, default_year=date.today().year)
        needs_vision: list[tuple[PageContent, str]] = []
        for page in pages:
            if page.is_scanned:
                needs_vision.append((page, "scanned"))
                continue
            signals = transaction_signal_count(page, probe_ctx)
            parsed = len(parse_page(page, probe_ctx, None))
            if signals >= 2 and parsed == 0:
                needs_vision.append((page, "unparsed"))
        if needs_vision:
            reasons = {r for _, r in needs_vision}
            self._event(
                "extracting",
                f"{len(needs_vision)} page(s) need the vision model ("
                + ", ".join(sorted("image-only" if r == "scanned" else "layout not parseable" for r in reasons)) + ")",
                pages=[p.page_number for p, _ in needs_vision],
            )
            try:
                client = self._vision_client()
            except VisionUnavailable as exc:
                result.vision_unavailable_reason = str(exc)
                result.vision_pages_skipped = [p.page_number for p, _ in needs_vision]
                for p, _ in needs_vision:
                    p.notes.append("Vision fallback needed but unavailable (GROQ_API_KEY not configured).")
                self._event("extracting", "Vision fallback unavailable: GROQ_API_KEY is not configured", "warning")
                client = None
            if client is not None:
                for i, (page, reason) in enumerate(needs_vision):
                    if i >= self.settings.vision_max_pages:
                        result.vision_pages_skipped.append(page.page_number)
                        page.notes.append("Skipped: vision page limit reached.")
                        continue
                    self._event("extracting", f"Vision model reading page {page.page_number}", page=page.page_number)
                    try:
                        image = docling_extractor.render_pdf_page(path, page.page_number, self.settings.vision_render_dpi)
                        vr = client.extract_page(image, "image/png", page.page_number)
                    except VisionError as exc:
                        page.notes.append(f"Vision extraction failed: {exc}")
                        result.vision_pages_skipped.append(page.page_number)
                        self._event("extracting", f"Vision failed on page {page.page_number}: {exc}", "warning")
                        continue
                    result.vision_results.append(vr)
                    result.methods.add("vision")
                    page.method = "docling+vision" if page.method == "docling" else "vision"
                    page.notes.append(f"Transactions read by vision model ({reason}).")
                    # Keep the vision transcription as the page text for traceability.
                    if vr.page_text:
                        page.lines = page.lines + ["", "[vision transcription]"] + vr.page_text.splitlines()
                    self._event("extracting", f"Vision model read {len(vr.transactions)} rows on page {page.page_number}",
                                page=page.page_number, rows=len(vr.transactions))
        result.pages = pages
        return result

    def _vision_hint(self, extraction: ExtractionResult) -> StatementMeta | None:
        if not extraction.vision_results:
            return None
        hint = StatementMeta()
        texts = [vr.page_text for vr in extraction.vision_results] + [
            " ".join(filter(None, [t.date_text or ""])) for vr in extraction.vision_results for t in vr.transactions
        ]
        order, confident = detect_order(texts)
        ctx = DateContext(order=order, order_confident=confident)
        for vr in extraction.vision_results:
            s = vr.statement
            if s.get("currency") and not hint.currency and len(s["currency"]) == 3:
                hint.currency, hint.currency_confident = s["currency"].upper(), True
            hint.issuer = hint.issuer or s.get("issuer")
            for key, label in (("total_purchases", "total_debits"), ("total_credits", "total_credits"),
                               ("previous_balance", "previous_balance"), ("new_balance", "closing_balance")):
                if s.get(key) and label not in hint.printed_totals:
                    parsed = parse_amount(s[key])
                    if parsed.value is not None:
                        hint.printed_totals[label] = str(abs(parsed.value))
            if s.get("period_start") and not hint.period_start:
                hint.period_start = parse_date(s["period_start"], ctx).value
            if s.get("period_end") and not hint.period_end:
                hint.period_end = parse_date(s["period_end"], ctx).value
        return hint

    def _structure(self, extraction: ExtractionResult) -> tuple[StatementMeta, DateContext, list[NormalizedTransaction]]:
        meta, ctx = detect_metadata(extraction.pages, self._vision_hint(extraction))
        vision_pages = {vr.page_number for vr in extraction.vision_results}
        raws: list[RawTransaction] = []
        for page in extraction.pages:
            if page.page_number in vision_pages:
                continue  # vision rows replace the (failed) text parse for this page
            raws.extend(parse_page(page, ctx, meta.currency))
        for vr in extraction.vision_results:
            raws.extend(vr.transactions)
        raws.sort(key=lambda r: (r.page_number or 0))
        txns = normalize_all(raws, meta, ctx)
        self._event(
            "structuring",
            f"Parsed {len(txns)} transaction candidate(s)"
            + (f"; statement currency {meta.currency}" if meta.currency else "; statement currency not detected"),
            candidates=len(txns), currency=meta.currency,
            period=[str(meta.period_start) if meta.period_start else None, str(meta.period_end) if meta.period_end else None],
        )
        return meta, ctx, txns

    def _verify(self, txns: list[NormalizedTransaction], meta: StatementMeta,
                extraction: ExtractionResult) -> tuple[list[NormalizedTransaction], dict[str, Any]]:
        txns, removed = remove_extraction_duplicates(txns)
        validate_fields(txns, meta)
        dupes = flag_possible_duplicates(txns)
        checks = cross_check_totals(txns, meta)
        text_pages = {n: t for n, t in extraction.text_layer.items() if len(t.strip()) >= 40}
        docling_txns = [t for t in txns if t.source_method.startswith("docling") or t.source_method == "pdf_text"]
        layer = text_layer_check(docling_txns, text_pages) if text_pages else {"checked": 0, "confirmed": 0}
        if layer["checked"]:
            checks.append({
                "name": "text_layer",
                "label": "Amounts confirmed in the PDF text layer",
                "status": "pass" if layer["confirmed"] == layer["checked"] else "warn",
                "expected": str(layer["checked"]),
                "actual": str(layer["confirmed"]),
                "message": f"{layer['confirmed']} of {layer['checked']} amounts were found verbatim on their source page.",
            })
        if extraction.vision_pages_skipped:
            checks.append({
                "name": "vision_skipped",
                "label": "Pages not fully processed",
                "status": "warn",
                "message": "Pages " + ", ".join(map(str, extraction.vision_pages_skipped))
                + " needed vision extraction but could not be processed; transactions on them may be missing.",
            })
        n_review = sum(1 for t in txns if t.review_status == "needs_review")
        self._event(
            "verifying",
            f"Validation complete: {removed} duplicate extraction(s) merged, {dupes} possible duplicate(s), "
            f"{n_review} transaction(s) need review",
            merged=removed, possible_duplicates=dupes, needs_review=n_review,
        )
        verification = {
            "checks": checks,
            "needs_review": n_review,
            "possible_duplicates": dupes,
            "merged_extraction_duplicates": removed,
        }
        return txns, verification

    def _store(self, stmt: Statement, doc: Document, extraction: ExtractionResult, meta: StatementMeta,
               txns: list[NormalizedTransaction], verification: dict[str, Any]) -> None:
        db = self.db
        db.execute(delete(Transaction).where(Transaction.statement_id == stmt.id))
        db.execute(delete(ExtractedPage).where(ExtractedPage.document_id == doc.id))
        for page in extraction.pages:
            text = page.text
            db.add(ExtractedPage(
                document_id=doc.id,
                page_number=page.page_number,
                method=page.method,
                text=text[:200_000],
                tables=page.tables,
                char_count=len(text),
                is_scanned=page.is_scanned,
                notes=page.notes,
            ))
        seq_to_id: dict[int, uuid.UUID] = {}
        rows: list[Transaction] = []
        for t in txns:
            row = Transaction(
                id=uuid.uuid4(),
                statement_id=stmt.id,
                seq=t.seq,
                txn_date=t.txn_date,
                posting_date=t.posting_date,
                merchant=t.merchant,
                merchant_normalized=t.merchant_normalized,
                description=t.description,
                amount=t.amount,
                currency=t.currency,
                original_amount=t.original_amount,
                original_currency=t.original_currency,
                txn_type=t.txn_type,
                category=t.category,
                category_source=t.category_source,
                page_number=t.page_number,
                source_method=t.source_method,
                raw_text=t.raw_text,
                confidence=t.confidence,
                uncertain_fields=sorted(set(t.uncertain_fields)),
                flags=t.flags,
                review_status=t.review_status,
            )
            seq_to_id[t.seq] = row.id
            rows.append(row)
        for t, row in zip(txns, rows, strict=True):
            if t.duplicate_of_seq is not None:
                row.duplicate_of_id = seq_to_id.get(t.duplicate_of_seq)
        db.add_all(rows)

        dated = [t.txn_date for t in txns if t.txn_date]
        stmt.issuer = meta.issuer
        stmt.statement_currency = meta.currency
        stmt.period_start = meta.period_start or (min(dated) if dated else None)
        stmt.period_end = meta.period_end or (max(dated) if dated else None)
        stmt.detected_totals = {k: str(Decimal(v)) for k, v in meta.printed_totals.items()}
        stmt.verification = verification
        stmt.extraction_methods = sorted(extraction.methods)
        stmt.error_message = None
        db.commit()
