"""A second, structurally different statement: US format, month-first dates, `$` amounts, no table
grid, negative payment, foreign amount printed on a continuation line, several totals per line.
It must be extracted as accurately as the grid-table demo, with no bank-specific code."""

from __future__ import annotations

import io
import json
from decimal import Decimal

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.jobs.worker import run_pending_jobs

ROWS = [
    ("08/28", "08/29", "WHOLE FOODS MARKET #10234 AUSTIN TX", "87.43"),
    ("08/30", "08/31", "SHELL OIL 5744 AUSTIN TX", "41.20"),
    ("09/02", "09/02", "PAYMENT THANK YOU", "-1,200.00"),
    ("09/03", "09/05", "UBER EATS SAN FRANCISCO CA", "32.18"),
    ("09/14", "09/15", "HOTEL LE MARAIS PARIS FR", "412.66"),
    ("09/19", "09/20", "NETFLIX.COM LOS GATOS CA", "15.49"),
    ("09/21", "09/22", "AMAZON MKTPLACE PMTS SEATTLE WA", "129.99"),
]


def us_statement_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 750, "Example Card Services  (DEMO - FICTIONAL)")
    c.setFont("Helvetica", 9)
    c.drawString(50, 732, "Billing Cycle: 08/25/2025 - 09/24/2025      Amounts in USD")
    c.drawString(50, 718, "Previous Balance $1,500.00    New Balance $1,018.95    Minimum Payment Due $35.00")
    y = 690
    for label, x in (("Trans", 50), ("Post", 90), ("Description", 140)):
        c.drawString(x, y, label)
    c.drawRightString(560, y, "Amount")
    for t, p, d, a in ROWS:
        y -= 16
        c.drawString(50, y, t)
        c.drawString(90, y, p)
        c.drawString(140, y, d)
        c.drawRightString(560, y, a)
        if "PARIS" in d:
            y -= 12
            c.drawString(140, y, "EUR 378.50 @ 1.09027")
    c.drawString(50, y - 24, "Total Purchases $718.95      Total Payments $1,200.00")
    c.save()
    return buf.getvalue()


def test_text_only_us_layout(client: TestClient) -> None:
    sid = client.post("/api/statements", files={"file": ("us.pdf", us_statement_pdf(), "application/pdf")}).json()["id"]
    run_pending_jobs()
    s = client.get(f"/api/statements/{sid}").json()
    assert s["status"] == "ready", s["error_message"]
    assert s["statement_currency"] == "USD"
    assert (s["period_start"], s["period_end"]) == ("2025-08-25", "2025-09-24")
    checks = {c["name"]: c["status"] for c in s["verification"]["checks"]}
    assert checks == {"total_debits": "pass", "total_credits": "pass", "balance_reconciliation": "pass", "text_layer": "pass"}

    txns = client.get(f"/api/transactions?statement_id={sid}&sort=date").json()["items"]
    assert [(t["txn_date"], t["merchant"], t["amount"], t["txn_type"]) for t in txns] == [
        ("2025-08-28", "Whole Foods Market", "87.43", "debit"),
        ("2025-08-30", "Shell Oil", "41.20", "debit"),
        ("2025-09-02", "Payment Thank You", "1200.00", "credit"),
        ("2025-09-03", "Uber Eats", "32.18", "debit"),
        ("2025-09-14", "Hotel Le Marais", "412.66", "debit"),
        ("2025-09-19", "Netflix.com", "15.49", "debit"),
        ("2025-09-21", "Amazon Mktplace Pmts", "129.99", "debit"),
    ]
    hotel = txns[4]
    assert (hotel["original_amount"], hotel["original_currency"]) == ("378.50", "EUR")
    assert all(t["review_status"] == "ok" for t in txns)

    conv = client.post("/api/conversations", json={"statement_id": sid}).json()["id"]
    r = client.post(f"/api/conversations/{conv}/messages", json={"content": "How much did I spend this month?"})
    final = json.loads([b for b in r.text.split("\n\n") if b.startswith("event: final")][0].split("data: ", 1)[1])
    # "This month" = September (statement ends 24 Sep), purchases only.
    expected = sum((Decimal(x) for x in ("32.18", "412.66", "15.49", "129.99")), Decimal("0"))
    assert Decimal(final["result"]["metrics"][0]["value"]) == expected
