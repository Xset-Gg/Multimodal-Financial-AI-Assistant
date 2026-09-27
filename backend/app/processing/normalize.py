"""Convert raw transaction candidates into the canonical schema.

Every value is parsed deterministically from text found in the document. When a
field cannot be determined reliably it is recorded in ``uncertain_fields`` rather
than guessed.
"""

from __future__ import annotations

import re

from app.processing.amounts import parse_amount, quantize
from app.processing.categorize import CATEGORIES, categorize, clean_merchant, normalize_merchant_key
from app.processing.dates import DateContext, parse_date
from app.processing.parsing import CREDIT_WORDS_RE
from app.processing.types import NormalizedTransaction, RawTransaction, StatementMeta

BASE_CONFIDENCE = {"docling_table": 0.95, "docling_text": 0.9, "vision": 0.8}


def normalize_one(raw: RawTransaction, seq: int, meta: StatementMeta, ctx: DateContext) -> NormalizedTransaction:
    uncertain: set[str] = set(raw.uncertain_hints)
    flags: list[str] = []

    parsed_date = parse_date(raw.date_text, ctx)
    if parsed_date.value is None or parsed_date.uncertain:
        uncertain.add("txn_date")
    flags.extend(r for r in parsed_date.reasons if r not in flags)
    posting = parse_date(raw.posting_date_text, ctx).value if raw.posting_date_text else None

    parsed_amount = parse_amount(raw.amount_text, raw.currency_hint or meta.currency)
    amount = parsed_amount.value
    if amount is None or parsed_amount.uncertain:
        uncertain.add("amount")
    flags.extend(r for r in parsed_amount.reasons if r not in flags and r != "missing_amount")
    if amount is None:
        flags.append("missing_amount")

    currency = parsed_amount.currency or raw.currency_hint or meta.currency
    if currency is None or parsed_amount.currency_ambiguous:
        uncertain.add("currency")
    if currency and amount is not None:
        amount = quantize(abs(amount), currency)
    elif amount is not None:
        amount = abs(amount)

    original_amount = None
    original_currency = raw.original_currency
    if raw.original_amount_text:
        parsed_orig = parse_amount(raw.original_amount_text, raw.original_currency)
        if parsed_orig.value is not None:
            original_currency = parsed_orig.currency or original_currency
            original_amount = abs(parsed_orig.value)
            if original_currency:
                original_amount = quantize(original_amount, original_currency)
        else:
            uncertain.add("original_amount")
    if original_currency and original_currency == currency:
        if original_amount is not None and amount is not None and original_amount != amount:
            flags.append("original_amount_mismatch")
        original_amount, original_currency = None, None
    if original_amount is not None and not original_currency:
        uncertain.add("original_currency")

    if raw.type_hint in ("debit", "credit"):
        txn_type = raw.type_hint
    elif parsed_amount.is_credit or CREDIT_WORDS_RE.search(raw.description):
        txn_type = "credit"
    else:
        txn_type = "debit"

    description = re.sub(r"\s+", " ", raw.description).strip()
    merchant = (raw.merchant_hint or "").strip() or clean_merchant(description)
    if not merchant:
        uncertain.add("merchant")

    category, matched = categorize(description, merchant, txn_type)
    category_source = "rules"
    if not matched and raw.category_hint in CATEGORIES and raw.category_hint != "Uncategorized":
        category, category_source = raw.category_hint, "vision"  # type: ignore[assignment]
    if category == "Payments & Credits" and txn_type == "debit" and matched:
        # e.g. "REFUND" printed without a CR marker.
        txn_type = "credit"

    confidence = BASE_CONFIDENCE.get(raw.source_method, 0.8) - 0.15 * len(uncertain)
    return NormalizedTransaction(
        seq=seq,
        txn_date=parsed_date.value,
        posting_date=posting,
        merchant=merchant,
        merchant_normalized=normalize_merchant_key(merchant),
        description=description or (merchant or ""),
        amount=amount,
        currency=currency,
        original_amount=original_amount,
        original_currency=original_currency,
        txn_type=txn_type,
        category=category,
        category_source=category_source,
        page_number=raw.page_number,
        source_method=raw.source_method,
        raw_text=raw.raw_text[:2000],
        confidence=round(max(0.05, min(1.0, confidence)), 2),
        uncertain_fields=sorted(uncertain),
        flags=flags,
    )


def normalize_all(raws: list[RawTransaction], meta: StatementMeta, ctx: DateContext) -> list[NormalizedTransaction]:
    return [normalize_one(r, i, meta, ctx) for i, r in enumerate(raws)]
