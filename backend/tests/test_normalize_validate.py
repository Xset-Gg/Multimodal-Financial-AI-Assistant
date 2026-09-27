"""Normalization, validation, duplicate detection and totals cross-checks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.processing.dates import DateContext
from app.processing.normalize import normalize_all, normalize_one
from app.processing.types import RawTransaction, StatementMeta
from app.processing.validate import (
    cross_check_totals,
    flag_possible_duplicates,
    remove_extraction_duplicates,
    text_layer_check,
    validate_fields,
)

META = StatementMeta(currency="QAR", currency_confident=True, period_start=date(2025, 9, 1),
                     period_end=date(2025, 9, 30))
CTX = DateContext(order="DMY", order_confident=True, period_start=META.period_start, period_end=META.period_end)


def raw(date_text: str | None = "15/09/2025", desc: str = "STARBUCKS DOHA QA", amount: str | None = "31.00",
        method: str = "docling_table", **kw: object) -> RawTransaction:
    return RawTransaction(page_number=1, source_method=method, raw_text=f"{date_text} {desc} {amount}",
                          date_text=date_text, description=desc, amount_text=amount, **kw)  # type: ignore[arg-type]


def test_normalizes_to_canonical_schema() -> None:
    t = normalize_one(raw(original_amount_text="USD 8.50", original_currency="USD"), 0, META, CTX)
    assert t.txn_date == date(2025, 9, 15)
    assert t.amount == Decimal("31.00") and t.currency == "QAR"
    assert t.original_amount == Decimal("8.50") and t.original_currency == "USD"
    assert t.merchant == "Starbucks" and t.category == "Dining" and t.txn_type == "debit"
    assert t.uncertain_fields == [] and t.review_status == "ok"


def test_missing_values_are_marked_uncertain_not_invented() -> None:
    t = normalize_one(raw(date_text=None, amount="illegible"), 0, META, CTX)
    assert t.txn_date is None and t.amount is None
    assert {"txn_date", "amount"} <= set(t.uncertain_fields)
    assert t.review_status == "needs_review"
    assert t.confidence < 0.8


def test_credit_detection_from_marker_and_wording() -> None:
    assert normalize_one(raw(amount="159.00 CR", desc="ZARA DOHA"), 0, META, CTX).txn_type == "credit"
    assert normalize_one(raw(desc="REFUND AMAZON", amount="20.00"), 0, META, CTX).txn_type == "credit"
    assert normalize_one(raw(amount="(45.00)"), 0, META, CTX).txn_type == "credit"


def test_same_row_from_two_passes_is_merged() -> None:
    txns = normalize_all([raw(method="docling_table"), raw(method="docling_text")], META, CTX)
    kept, removed = remove_extraction_duplicates(txns)
    assert removed == 1 and len(kept) == 1


def test_identical_rows_from_same_pass_are_kept_and_flagged() -> None:
    txns = normalize_all([raw(desc="TALABAT", amount="64.50"), raw(desc="TALABAT", amount="64.50")], META, CTX)
    kept, removed = remove_extraction_duplicates(txns)
    assert removed == 0 and len(kept) == 2
    assert flag_possible_duplicates(kept) == 1
    assert "possible_duplicate" in kept[1].flags and kept[1].duplicate_of_seq == kept[0].seq
    assert "possible_duplicate" not in kept[0].flags


def test_validate_fields_flags_out_of_period_future_and_zero() -> None:
    txns = normalize_all([
        raw(date_text="15/01/2025"),
        raw(date_text="15/09/2099"),
        raw(amount="0.00", desc="INTEREST CHARGE"),
    ], META, CTX)
    validate_fields(txns, META, today=date(2026, 1, 1))
    assert "date_outside_statement_period" in txns[0].flags
    assert "future_date" in txns[1].flags and "txn_date" in txns[1].uncertain_fields
    assert "zero_amount" in txns[2].flags


def test_cross_check_totals_pass_and_warn() -> None:
    txns = normalize_all([raw(amount="100.00"), raw(amount="50.00", desc="CARREFOUR")], META, CTX)
    ok = StatementMeta(currency="QAR", printed_totals={"total_debits": "150.00"})
    bad = StatementMeta(currency="QAR", printed_totals={"total_debits": "214.50"})
    assert cross_check_totals(txns, ok)[0]["status"] == "pass"
    check = cross_check_totals(txns, bad)[0]
    assert check["status"] == "warn" and "64.50" in check["message"]


def test_balance_reconciliation() -> None:
    txns = normalize_all([raw(amount="100.00"), raw(amount="30.00 CR", desc="REFUND")], META, CTX)
    meta = StatementMeta(currency="QAR", printed_totals={"previous_balance": "1000.00", "closing_balance": "1070.00"})
    check = next(c for c in cross_check_totals(txns, meta) if c["name"] == "balance_reconciliation")
    assert check["status"] == "pass"


def test_text_layer_check_flags_amounts_not_in_pdf() -> None:
    txns = normalize_all([raw(amount="31.00"), raw(amount="99.99", desc="OTHER")], META, CTX)
    result = text_layer_check(txns, {1: "15/09/2025 STARBUCKS 31.00"})
    assert result == {"checked": 2, "confirmed": 1}
    assert "amount_not_found_in_text_layer" in txns[1].flags
