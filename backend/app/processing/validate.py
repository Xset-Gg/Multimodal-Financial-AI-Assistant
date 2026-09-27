"""Validation and cross-checks for normalized transactions."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.processing.types import NormalizedTransaction, StatementMeta

LARGE_AMOUNT = Decimal("1000000")


def _content_key(t: NormalizedTransaction) -> tuple[Any, ...]:
    return (t.page_number, t.txn_date, t.amount, t.currency, t.merchant_normalized, t.txn_type)


def remove_extraction_duplicates(txns: list[NormalizedTransaction]) -> tuple[list[NormalizedTransaction], int]:
    """Drop records that are the *same printed row* captured twice.

    That happens when two extraction passes (e.g. a Docling table and the text lines, or
    Docling and the vision model) both read one row. Identical rows from the *same* pass at
    different positions are distinct printed rows and are kept (then flagged as possible
    duplicates for the user to judge).
    """
    by_key: dict[tuple[Any, ...], list[NormalizedTransaction]] = defaultdict(list)
    kept: list[NormalizedTransaction] = []
    for t in txns:
        key = _content_key(t)
        prior = by_key[key]
        same_pass = [p for p in prior if p.source_method == t.source_method]
        other_pass = [p for p in prior if p.source_method != t.source_method]
        # Captured by another pass, and this pass hasn't produced more copies than that pass did.
        if other_pass and len(same_pass) < len(other_pass):
            prior.append(t)
            continue
        prior.append(t)
        kept.append(t)
    for i, t in enumerate(kept):
        t.seq = i
    return kept, len(txns) - len(kept)


def flag_possible_duplicates(txns: list[NormalizedTransaction]) -> int:
    """Flag distinct rows that share date, amount, currency and merchant. They are not removed:
    two identical purchases on the same day can be legitimate, so the user decides."""
    groups: dict[tuple[Any, ...], list[NormalizedTransaction]] = defaultdict(list)
    for t in txns:
        if t.txn_date and t.amount is not None and t.merchant_normalized:
            groups[(t.txn_date, t.amount, t.currency, t.merchant_normalized, t.txn_type)].append(t)
    flagged = 0
    for members in groups.values():
        for dup in members[1:]:
            if "possible_duplicate" not in dup.flags:
                dup.flags.append("possible_duplicate")
                dup.duplicate_of_seq = members[0].seq
                flagged += 1
    return flagged


def validate_fields(txns: list[NormalizedTransaction], meta: StatementMeta, today: date | None = None) -> None:
    today = today or date.today()
    start = meta.period_start - timedelta(days=45) if meta.period_start else None
    end = meta.period_end + timedelta(days=10) if meta.period_end else None
    for t in txns:
        if t.txn_date:
            if t.txn_date > today + timedelta(days=1):
                t.flags.append("future_date")
                if "txn_date" not in t.uncertain_fields:
                    t.uncertain_fields.append("txn_date")
            elif (start and t.txn_date < start) or (end and t.txn_date > end):
                t.flags.append("date_outside_statement_period")
        if t.amount is not None:
            if t.amount == 0:
                t.flags.append("zero_amount")
            elif t.amount >= LARGE_AMOUNT:
                t.flags.append("unusually_large_amount")
        if t.flags or t.uncertain_fields:
            t.confidence = round(max(0.05, t.confidence - 0.05 * len(t.flags)), 2)


def _sum(txns: list[NormalizedTransaction], txn_type: str, currency: str | None) -> Decimal:
    return sum(
        (t.amount for t in txns if t.txn_type == txn_type and t.amount is not None and t.currency == currency),
        Decimal("0"),
    )


def cross_check_totals(txns: list[NormalizedTransaction], meta: StatementMeta) -> list[dict[str, Any]]:
    """Compare extracted sums with the totals printed on the statement."""
    checks: list[dict[str, Any]] = []
    cur = meta.currency
    for key, txn_type, label in (
        ("total_debits", "debit", "Total purchases/debits printed on statement"),
        ("total_credits", "credit", "Total payments/credits printed on statement"),
    ):
        if key not in meta.printed_totals:
            continue
        expected = Decimal(meta.printed_totals[key])
        actual = _sum(txns, txn_type, cur)
        diff = abs(expected - actual)
        tolerance = max(Decimal("0.05"), expected * Decimal("0.001"))
        checks.append({
            "name": key,
            "label": label,
            "status": "pass" if diff <= tolerance else "warn",
            "expected": str(expected),
            "actual": str(actual),
            "currency": cur,
            "message": (
                "Extracted transactions add up to the printed total."
                if diff <= tolerance
                else f"Extracted {txn_type} transactions differ from the printed total by {diff} {cur or ''}. "
                "Some rows may be missing or misread."
            ).strip(),
        })
    if {"previous_balance", "closing_balance"} <= meta.printed_totals.keys():
        prev = Decimal(meta.printed_totals["previous_balance"])
        closing = Decimal(meta.printed_totals["closing_balance"])
        expected_closing = prev + _sum(txns, "debit", cur) - _sum(txns, "credit", cur)
        diff = abs(expected_closing - closing)
        checks.append({
            "name": "balance_reconciliation",
            "label": "Previous balance + debits − credits = new balance",
            "status": "pass" if diff <= Decimal("0.05") else "warn",
            "expected": str(closing),
            "actual": str(expected_closing),
            "currency": cur,
            "message": "Balances reconcile." if diff <= Decimal("0.05")
            else f"Balances do not reconcile (difference {diff} {cur or ''}); fees or rows may be missing.",
        })
    return checks


def text_layer_check(txns: list[NormalizedTransaction], page_texts: dict[int, str]) -> dict[str, Any]:
    """For text PDFs: confirm each amount appears in the PDF's own text layer on the cited page.

    This is an independent read of the file (pdfium) compared against the Docling/vision result.
    """
    checked = confirmed = 0
    for t in txns:
        if t.amount is None or t.page_number is None or t.page_number not in page_texts:
            continue
        checked += 1
        page = page_texts[t.page_number].replace(",", "").replace(" ", "")
        needle = f"{t.amount:f}".rstrip("0").rstrip(".") if t.amount % 1 == 0 else f"{t.amount:f}"
        variants = {f"{t.amount:.2f}", needle, f"{t.amount:.2f}".replace(".", ",")}
        if any(v in page for v in variants):
            confirmed += 1
        else:
            t.flags.append("amount_not_found_in_text_layer")
            if "amount" not in t.uncertain_fields:
                t.uncertain_fields.append("amount")
    return {"checked": checked, "confirmed": confirmed}
