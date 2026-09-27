"""End-to-end: upload → extraction (real Docling) → storage → questions → deletion.

The vision model is replaced with a fake for image / scanned-PDF flows; Docling runs for real.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from app.jobs.worker import run_pending_jobs
from app.processing.docling_extractor import render_pdf_page
from app.processing.vision import VisionPageResult, parse_vision_payload

EXPECTED = json.loads((Path(__file__).resolve().parents[1] / "app" / "demo" / "demo_statement_expected.json").read_text())


def sse_events(text: str) -> list[tuple[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event:
            out.append((event, data))
    return out


def ask(client: TestClient, conv_id: str, question: str) -> dict[str, Any]:
    r = client.post(f"/api/conversations/{conv_id}/messages", json={"content": question})
    assert r.status_code == 200
    events = sse_events(r.text)
    kinds = [e for e, _ in events]
    assert "error" not in kinds, events
    assert kinds[-1] == "final"
    return dict(events)["final"]


def upload_demo(client: TestClient) -> str:
    r = client.post("/api/statements/demo")
    assert r.status_code == 201, r.text
    assert r.json()["is_demo"] and r.json()["display_name"].startswith("[DEMO]")
    return r.json()["id"]


def test_full_flow_demo_statement(client: TestClient, storage_dir: Path) -> None:
    sid = upload_demo(client)
    assert client.get(f"/api/statements/{sid}").json()["status"] == "uploaded"
    assert run_pending_jobs() == 1

    stmt = client.get(f"/api/statements/{sid}").json()
    assert stmt["status"] == "ready", stmt["error_message"]
    assert stmt["statement_currency"] == "QAR"
    assert (stmt["period_start"], stmt["period_end"]) == (EXPECTED["period_start"], EXPECTED["period_end"])
    assert "docling" in stmt["extraction_methods"]
    assert stmt["transaction_count"] == EXPECTED["transaction_count"]

    # Every check against the statement's own printed totals passes.
    checks = {c["name"]: c for c in stmt["verification"]["checks"]}
    assert checks["total_debits"]["status"] == "pass", checks["total_debits"]
    assert checks["total_credits"]["status"] == "pass"
    assert checks["balance_reconciliation"]["status"] == "pass"
    assert checks["text_layer"]["status"] == "pass"
    assert stmt["detected_totals"]["minimum_due"] == EXPECTED["minimum_due"]

    # Extracted rows match the ground truth, in order.
    txns = client.get(f"/api/transactions?statement_id={sid}&limit=500&sort=date").json()["items"]
    got = sorted((t["txn_date"], t["amount"], t["txn_type"]) for t in txns)
    want = sorted((
        "-".join(reversed(e["date"].split("/"))), e["amount"] if "." in e["amount"] else e["amount"] + ".00",
        "credit" if e["credit"] else "debit",
    ) for e in EXPECTED["transactions"])
    assert got == want
    by_merchant = {t["merchant"]: t for t in txns}
    assert by_merchant["Amazon.com"]["original_currency"] == "USD"
    assert all(t["source_method"] == "docling_table" and t["page_number"] in (1, 2) for t in txns)
    # The two identical Talabat orders on 21/09 are both kept; the second is flagged for review.
    talabat = [t for t in txns if t["txn_date"] == "2025-09-21"]
    assert len(talabat) == 2 and sum("possible_duplicate" in t["flags"] for t in talabat) == 1
    assert any("zero_amount" in t["flags"] for t in txns)  # the 0.00 interest line

    # Original document and extracted pages are available for verification.
    assert client.get(f"/api/statements/{sid}/file").headers["content-type"] == "application/pdf"
    pages = client.get(f"/api/statements/{sid}/pages").json()
    assert [p["page_number"] for p in pages] == [1, 2] and pages[0]["tables"]
    stages = [e["stage"] for e in client.get(f"/api/statements/{sid}/events").json()]
    assert stages[0] == "uploaded" and stages[-1] == "ready"
    for stage in ("extracting", "structuring", "verifying"):
        assert stage in stages

    # SSE stream replays real events and ends when processing is complete.
    stream = sse_events(client.get(f"/api/statements/{sid}/stream").text)
    assert stream[-1][0] == "end" and stream[-1][1]["status"] == "ready"

    # Explorer filters.
    over = client.get(f"/api/transactions?statement_id={sid}&min_amount=500&currency=QAR").json()
    assert {t["merchant"] for t in over["items"]} >= {"Qatar Airways", "Booking.com"}
    dining = client.get(f"/api/transactions?statement_id={sid}&category=Dining&txn_type=debit").json()
    assert dining["total"] >= 6
    # Totals cover the whole filtered set, not just the returned page.
    paged = client.get(f"/api/transactions?statement_id={sid}&limit=5").json()
    assert len(paged["items"]) == 5 and paged["totals"]["QAR"]["debits"] == EXPECTED["total_debits"]

    # Analytics are computed from the stored rows.
    analytics = client.get(f"/api/analytics?statement_id={sid}").json()
    assert analytics["currency"] == "QAR"
    assert analytics["totals"]["QAR"]["debits"] == EXPECTED["total_debits"]
    assert Decimal(analytics["totals"]["QAR"]["credits"]) == Decimal(EXPECTED["total_credits"])

    # Questions.
    conv = client.post("/api/conversations", json={"statement_id": sid}).json()["id"]
    a = ask(client, conv, "How much did I spend this month?")
    assert a["result"]["metrics"][0]["value"] == EXPECTED["total_debits"]
    assert len(a["result"]["transaction_ids"]) == EXPECTED["transaction_count"] - 2  # payment + refund excluded
    assert "10,914.52 QAR" in a["content"]

    a = ask(client, conv, "Which merchant did I spend the most with?")
    assert a["result"]["table"]["rows"][0]["label"] == "Qatar Airways"

    a = ask(client, conv, "How many transactions were made in USD?")
    assert a["result"]["metrics"][0]["value"] == 4

    a = ask(client, conv, "What did I spend on September 15?")
    assert a["result"]["metrics"][0]["value"] == "1049.90"

    a = ask(client, conv, "What's my credit score?")
    assert a["result"]["not_found"] is True and a["result"]["transaction_ids"] == []

    # "View transactions used for this answer" resolves the exact rows.
    a = ask(client, conv, "Show transactions above 500 QAR")
    used = client.post("/api/transactions/lookup", json={"ids": a["result"]["transaction_ids"]}).json()
    assert all(Decimal(t["amount"]) > 500 for t in used) and len(used) == a["result"]["transaction_count"]

    history = client.get(f"/api/conversations/{conv}").json()["messages"]
    assert len(history) == 12 and history[-1]["role"] == "assistant"

    # User correction marks the row verified.
    tid = txns[0]["id"]
    patched = client.patch(f"/api/transactions/{tid}", json={"category": "Shopping"}).json()
    assert patched["category"] == "Shopping" and patched["review_status"] == "user_verified"

    # Deletion removes the file, transactions, pages and the statement's conversations.
    assert client.delete(f"/api/statements/{sid}").status_code == 204
    assert list(storage_dir.glob("*")) == []
    assert client.get(f"/api/transactions?statement_id={sid}").json()["total"] == 0
    assert client.get(f"/api/conversations/{conv}").status_code == 404


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeVision:
    """Stands in for the Groq vision model: returns transcribed rows as printed."""

    calls: list[int] = []

    def extract_page(self, image: bytes, mime: str, page_number: int) -> VisionPageResult:
        FakeVision.calls.append(page_number)
        assert image[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0") and mime.startswith("image/")
        return parse_vision_payload({
            "is_statement": True,
            "statement": {"currency": "QAR", "period_start": "01/09/2025", "period_end": "30/09/2025",
                          "total_purchases": "876.00"},
            "transactions": [
                {"date": "15/09/2025", "description": "NOBU RESTAURANT DOHA QA", "amount": "845.00",
                 "currency": "QAR", "type": "debit", "category_guess": "Dining", "uncertain_fields": [],
                 "raw_text": "15/09/2025 NOBU RESTAURANT DOHA QA 845.00"},
                {"date": "15/09/2025", "description": "STARBUCKS DOHA QA", "amount": "31.00", "currency": "QAR",
                 "type": "debit", "uncertain_fields": ["amount"], "raw_text": "15/09/2025 STARBUCKS 31.00"},
                {"date": "31/02/2025", "description": "SMUDGED ROW", "amount": None, "type": "debit",
                 "uncertain_fields": ["date", "amount"], "raw_text": "?? SMUDGED ??"},
            ],
            "page_text": "Statement Period 01/09/2025 - 30/09/2025\nTotal Purchases 876.00",
        }, page_number)


def test_image_upload_uses_vision_pipeline(client: TestClient) -> None:
    FakeVision.calls = []
    img = Image.new("RGB", (800, 600), "white")
    sid = client.post("/api/statements", files={"file": ("photo.png", _png(img), "image/png")}).json()["id"]
    run_pending_jobs(vision_factory=FakeVision)
    stmt = client.get(f"/api/statements/{sid}").json()
    assert stmt["status"] == "ready" and stmt["extraction_methods"] == ["vision"]
    assert FakeVision.calls == [1]
    txns = client.get(f"/api/transactions?statement_id={sid}").json()["items"]
    assert len(txns) == 3
    smudged = next(t for t in txns if t["raw_text"] == "?? SMUDGED ??")
    assert smudged["amount"] is None and smudged["txn_date"] is None
    assert {"amount", "txn_date"} <= set(smudged["uncertain_fields"]) and smudged["review_status"] == "needs_review"
    starbucks = next(t for t in txns if t["merchant"] == "Starbucks")
    assert "amount" in starbucks["uncertain_fields"]  # model-reported uncertainty is preserved
    assert all(t["source_method"] == "vision" for t in txns)
    checks = {c["name"]: c for c in stmt["verification"]["checks"]}
    assert checks["total_debits"]["status"] == "pass"


def _scanned_pdf(demo_pdf: Path) -> bytes:
    page = Image.open(io.BytesIO(render_pdf_page(demo_pdf, 1, 60))).convert("RGB")
    buf = io.BytesIO()
    page.save(buf, format="PDF")
    return buf.getvalue()


def test_scanned_pdf_without_vision_key_fails_with_clear_message(client: TestClient, demo_pdf: Path) -> None:
    sid = client.post("/api/statements", files={"file": ("scan.pdf", _scanned_pdf(demo_pdf), "application/pdf")}).json()["id"]
    run_pending_jobs()
    stmt = client.get(f"/api/statements/{sid}").json()
    assert stmt["status"] == "failed"
    assert "GROQ_API_KEY" in stmt["error_message"] and "scanned" in stmt["error_message"]
    events = client.get(f"/api/statements/{sid}/events").json()
    assert events[-1]["level"] == "error"
    # Retry re-queues; with a vision model available it now succeeds.
    assert client.post(f"/api/statements/{sid}/retry").json()["status"] == "uploaded"
    FakeVision.calls = []
    run_pending_jobs(vision_factory=FakeVision)
    stmt = client.get(f"/api/statements/{sid}").json()
    assert stmt["status"] == "ready" and "vision" in stmt["extraction_methods"]
    assert FakeVision.calls == [1]
    pages = client.get(f"/api/statements/{sid}/pages").json()
    assert pages[0]["is_scanned"] and pages[0]["method"] == "docling+vision"


def test_pipeline_failure_is_reported_not_hidden(client: TestClient) -> None:
    class BrokenVision:
        def extract_page(self, image: bytes, mime: str, page_number: int) -> VisionPageResult:
            from app.processing.vision import VisionError

            raise VisionError("Vision API returned HTTP 503.")

    img = Image.new("RGB", (100, 100), "white")
    sid = client.post("/api/statements", files={"file": ("p.png", _png(img), "image/png")}).json()["id"]
    run_pending_jobs(vision_factory=BrokenVision)
    stmt = client.get(f"/api/statements/{sid}").json()
    assert stmt["status"] == "failed" and "HTTP 503" in stmt["error_message"]
