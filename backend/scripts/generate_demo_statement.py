"""Generate the bundled DEMO statement (fictional bank, fictional data).

Run:  python scripts/generate_demo_statement.py
Writes app/demo/demo_statement.pdf and app/demo/demo_statement_expected.json
(the ground truth used by integration tests).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parents[1] / "app" / "demo"

# (txn date, post date, description, billed QAR, original "CUR amount" or None, credit?)
TXNS: list[tuple[str, str, str, str, str | None, bool]] = [
    ("01/09/2025", "02/09/2025", "CARREFOUR HYPERMARKET DOHA QA", "412.75", None, False),
    ("02/09/2025", "03/09/2025", "STARBUCKS CITY CENTER DOHA QA", "28.00", None, False),
    ("02/09/2025", "03/09/2025", "KARWA TAXI DOHA QA", "35.50", None, False),
    ("03/09/2025", "04/09/2025", "OOREDOO POSTPAID BILL DOHA QA", "250.00", None, False),
    ("04/09/2025", "05/09/2025", "AMAZON.COM*MK12 SEATTLE US", "331.16", "USD 89.99", False),
    ("05/09/2025", "06/09/2025", "TALABAT FOOD DELIVERY DOHA QA", "86.25", None, False),
    ("06/09/2025", "07/09/2025", "NOVO CINEMAS DOHA QA", "120.00", None, False),
    ("07/09/2025", "08/09/2025", "LULU HYPERMARKET AL GHARRAFA QA", "267.40", None, False),
    ("08/09/2025", "09/09/2025", "WOQOD FUEL STATION LUSAIL QA", "140.00", None, False),
    ("09/09/2025", "10/09/2025", "APPLE.COM/BILL CORK IE", "36.76", "USD 9.99", False),
    ("10/09/2025", "10/09/2025", "PAYMENT RECEIVED - THANK YOU", "5,000.00", None, True),
    ("10/09/2025", "11/09/2025", "SHAKE SHACK MALL OF QATAR QA", "94.00", None, False),
    ("11/09/2025", "12/09/2025", "WELLCARE PHARMACY DOHA QA", "58.30", None, False),
    ("12/09/2025", "13/09/2025", "ZARA VILLAGGIO MALL DOHA QA", "689.00", None, False),
    ("13/09/2025", "14/09/2025", "UBER *TRIP LONDON GB", "112.18", "GBP 23.40", False),
    ("14/09/2025", "15/09/2025", "BOOKING.COM AMSTERDAM NL", "1,701.00", "EUR 420.00", False),
    ("15/09/2025", "16/09/2025", "STARBUCKS CITY CENTER DOHA QA", "31.00", None, False),
    ("15/09/2025", "16/09/2025", "NOBU RESTAURANT FOUR SEASONS DOHA QA", "845.00", None, False),
    ("15/09/2025", "16/09/2025", "AL MEERA SUPERMARKET DOHA QA", "173.90", None, False),
    ("16/09/2025", "17/09/2025", "VIRGIN MEGASTORE DOHA QA", "219.00", None, False),
    ("17/09/2025", "18/09/2025", "QATAR AIRWAYS DOHA QA", "2,450.00", None, False),
    ("18/09/2025", "19/09/2025", "CARREFOUR HYPERMARKET DOHA QA", "356.20", None, False),
    ("19/09/2025", "20/09/2025", "NETFLIX.COM LOS GATOS US", "56.00", "USD 15.49", False),
    ("20/09/2025", "21/09/2025", "SPOTIFY STOCKHOLM SE", "21.99", None, False),
    ("21/09/2025", "22/09/2025", "TALABAT FOOD DELIVERY DOHA QA", "64.50", None, False),
    ("21/09/2025", "22/09/2025", "TALABAT FOOD DELIVERY DOHA QA", "64.50", None, False),
    ("22/09/2025", "23/09/2025", "IKEA DOHA QA", "1,125.00", None, False),
    ("23/09/2025", "24/09/2025", "FITNESS FIRST DOHA QA", "350.00", None, False),
    ("24/09/2025", "25/09/2025", "ZARA VILLAGGIO MALL DOHA QA", "159.00", None, True),
    ("25/09/2025", "26/09/2025", "COSTA COFFEE LUSAIL QA", "24.00", None, False),
    ("26/09/2025", "27/09/2025", "KAHRAMAA UTILITY PAYMENT DOHA QA", "310.45", None, False),
    ("27/09/2025", "28/09/2025", "THE CHEESECAKE FACTORY DOHA QA", "212.00", None, False),
    ("28/09/2025", "29/09/2025", "AMAZON.COM*RT55 SEATTLE US", "147.08", "USD 39.97", False),
    ("29/09/2025", "30/09/2025", "KARWA TAXI DOHA QA", "42.00", None, False),
    ("30/09/2025", "30/09/2025", "FOREIGN TRANSACTION FEE", "59.60", None, False),
    ("30/09/2025", "30/09/2025", "INTEREST CHARGE", "0.00", None, False),
]
PREVIOUS_BALANCE = Decimal("5,432.10".replace(",", ""))


def dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    debits = sum((dec(a) for _, _, _, a, _, c in TXNS if not c), Decimal("0"))
    credits = sum((dec(a) for _, _, _, a, _, c in TXNS if c), Decimal("0"))
    new_balance = PREVIOUS_BALANCE + debits - credits
    minimum = (new_balance * Decimal("0.05")).quantize(Decimal("0.01"))

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=18, alignment=0, spaceAfter=2)

    doc = SimpleDocTemplate(str(OUT / "demo_statement.pdf"), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title="Demo Credit Card Statement",
                            author="SpendLens Demo Bank (fictional)")
    story = [
        Paragraph("SpendLens Demo Bank", title),
        Paragraph("DEMO DOCUMENT — FICTIONAL DATA FOR TESTING ONLY. NOT A REAL BANK OR ACCOUNT.", small),
        Spacer(1, 4 * mm),
        Paragraph("Platinum Credit Card Statement", styles["Heading3"]),
        Paragraph("Cardholder: Demo Customer &nbsp;&nbsp; Card number: XXXX XXXX XXXX 4821", styles["Normal"]),
        Paragraph("Statement Period: 01/09/2025 to 30/09/2025 &nbsp;&nbsp; Statement Date: 30/09/2025", styles["Normal"]),
        Paragraph("Billing Currency: QAR &nbsp;&nbsp; Payment Due Date: 25/10/2025", styles["Normal"]),
        Spacer(1, 5 * mm),
    ]
    summary = Table([
        ["Account Summary", ""],
        ["Previous Balance", f"{PREVIOUS_BALANCE:,.2f}"],
        ["Payments & Credits", f"{credits:,.2f}"],
        ["Purchases & Charges", f"{debits:,.2f}"],
        ["New Balance", f"{new_balance:,.2f}"],
        ["Minimum Payment Due", f"{minimum:,.2f}"],
    ], colWidths=[70 * mm, 40 * mm])
    summary.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story += [summary, Spacer(1, 6 * mm), Paragraph("Transaction Details", styles["Heading3"])]

    header = ["Trans Date", "Post Date", "Description", "Foreign Amount", "Amount (QAR)"]

    def table_for(rows: list[tuple[str, str, str, str, str | None, bool]]) -> Table:
        data = [header] + [[t, p, d, o or "", f"{a} CR" if c else a] for t, p, d, a, o, c in rows]
        tbl = Table(data, colWidths=[22 * mm, 22 * mm, 80 * mm, 26 * mm, 28 * mm], repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return tbl

    story += [table_for(TXNS[:20]), PageBreak(), Paragraph("Transaction Details (continued)", styles["Heading3"]),
              table_for(TXNS[20:]), Spacer(1, 6 * mm),
              Paragraph(f"Total Purchases & Charges: QAR {debits:,.2f}", styles["Normal"]),
              Paragraph(f"Total Payments & Credits: QAR {credits:,.2f}", styles["Normal"]),
              Spacer(1, 4 * mm),
              Paragraph("Foreign currency transactions are converted to QAR at the network rate on the posting date "
                        "and include a 2.5% foreign transaction fee billed separately.", small),
              Paragraph("This is a demo document generated for SpendLens AI. All names and figures are fictional.", small)]
    doc.build(story)

    expected = {
        "currency": "QAR",
        "period_start": "2025-09-01",
        "period_end": "2025-09-30",
        "transaction_count": len(TXNS),
        "total_debits": str(debits),
        "total_credits": str(credits),
        "previous_balance": str(PREVIOUS_BALANCE),
        "new_balance": str(new_balance),
        "minimum_due": str(minimum),
        "transactions": [
            {"date": t, "description": d, "amount": str(dec(a)), "credit": c,
             "original": o} for t, _, d, a, o, c in TXNS
        ],
    }
    (OUT / "demo_statement_expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print(f"debits={debits} credits={credits} new_balance={new_balance} minimum={minimum}")


if __name__ == "__main__":
    build()
