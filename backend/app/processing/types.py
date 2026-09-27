from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class PageContent:
    """Everything extracted from one page, before transaction parsing."""

    page_number: int
    method: str  # docling | vision
    lines: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    is_scanned: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class RawTransaction:
    """A transaction candidate with values exactly as found in the document."""

    page_number: int | None
    source_method: str  # docling_table | docling_text | vision
    raw_text: str
    date_text: str | None
    description: str
    amount_text: str | None
    posting_date_text: str | None = None
    currency_hint: str | None = None
    original_amount_text: str | None = None
    original_currency: str | None = None
    type_hint: str | None = None  # debit | credit
    merchant_hint: str | None = None
    category_hint: str | None = None
    uncertain_hints: list[str] = field(default_factory=list)


@dataclass
class NormalizedTransaction:
    seq: int
    txn_date: date | None
    posting_date: date | None
    merchant: str | None
    merchant_normalized: str | None
    description: str
    amount: Decimal | None
    currency: str | None
    original_amount: Decimal | None
    original_currency: str | None
    txn_type: str
    category: str
    category_source: str
    page_number: int | None
    source_method: str
    raw_text: str
    confidence: float
    uncertain_fields: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    duplicate_of_seq: int | None = None

    @property
    def review_status(self) -> str:
        return "needs_review" if (self.uncertain_fields or self.flags) else "ok"


@dataclass
class StatementMeta:
    issuer: str | None = None
    currency: str | None = None
    currency_confident: bool = False
    period_start: date | None = None
    period_end: date | None = None
    printed_totals: dict[str, str] = field(default_factory=dict)  # label -> amount (as decimal string)
