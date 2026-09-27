"""Deterministic transaction filtering and analytics.

Everything numeric shown in the UI or stated by the assistant is computed here
from stored transactions. Amounts in different currencies are never summed together.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import Statement, Transaction

SORT_FIELDS = {
    "date": Transaction.txn_date,
    "amount": Transaction.amount,
    "merchant": Transaction.merchant,
    "category": Transaction.category,
    "confidence": Transaction.confidence,
}


@dataclass
class TxnFilter:
    statement_ids: list[uuid.UUID]
    date_from: date | None = None
    date_to: date | None = None
    merchant: str | None = None  # substring match on merchant / description
    categories: list[str] = field(default_factory=list)
    currency: str | None = None  # billed currency OR original currency
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    min_exclusive: bool = False
    max_exclusive: bool = False
    txn_type: str | None = None  # debit | credit
    search: str | None = None
    review: str | None = None  # needs_review | ok | user_verified
    include_excluded: bool = False

    def describe(self) -> list[str]:
        parts = []
        if self.date_from and self.date_to and self.date_from == self.date_to:
            parts.append(f"on {self.date_from.isoformat()}")
        else:
            if self.date_from:
                parts.append(f"from {self.date_from.isoformat()}")
            if self.date_to:
                parts.append(f"to {self.date_to.isoformat()}")
        if self.merchant:
            parts.append(f'merchant matching "{self.merchant}"')
        if self.categories:
            parts.append("category " + " / ".join(self.categories))
        if self.currency:
            parts.append(f"currency {self.currency}")
        if self.min_amount is not None:
            parts.append(f"amount {'>' if self.min_exclusive else '≥'} {self.min_amount}")
        if self.max_amount is not None:
            parts.append(f"amount {'<' if self.max_exclusive else '≤'} {self.max_amount}")
        if self.txn_type:
            parts.append("purchases/debits only" if self.txn_type == "debit" else "payments/credits only")
        if self.search:
            parts.append(f'text contains "{self.search}"')
        return parts


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_query(f: TxnFilter) -> Select[Any]:
    q = select(Transaction).where(Transaction.statement_id.in_(f.statement_ids))
    if not f.include_excluded:
        q = q.where(Transaction.excluded.is_(False))
    if f.date_from:
        q = q.where(Transaction.txn_date >= f.date_from)
    if f.date_to:
        q = q.where(Transaction.txn_date <= f.date_to)
    if f.merchant:
        pat = f"%{_escape_like(f.merchant.strip())}%"
        q = q.where(or_(Transaction.merchant.ilike(pat, escape="\\"), Transaction.description.ilike(pat, escape="\\")))
    if f.categories:
        q = q.where(Transaction.category.in_(f.categories))
    if f.currency:
        cur = f.currency.upper()
        q = q.where(or_(Transaction.currency == cur, Transaction.original_currency == cur))
    def lower(col: Any) -> Any:
        return col > f.min_amount if f.min_exclusive else col >= f.min_amount

    def upper(col: Any) -> Any:
        return col < f.max_amount if f.max_exclusive else col <= f.max_amount

    for bound, active in ((lower, f.min_amount is not None), (upper, f.max_amount is not None)):
        if not active:
            continue
        if f.currency:
            # With a currency, compare the amount printed in that currency (billed or original).
            cur = f.currency.upper()
            q = q.where(or_(
                (Transaction.currency == cur) & bound(Transaction.amount),
                (Transaction.original_currency == cur) & bound(Transaction.original_amount),
            ))
        else:
            q = q.where(bound(Transaction.amount))
    if f.txn_type:
        q = q.where(Transaction.txn_type == f.txn_type)
    if f.search:
        pat = f"%{_escape_like(f.search.strip())}%"
        q = q.where(or_(
            Transaction.merchant.ilike(pat, escape="\\"),
            Transaction.description.ilike(pat, escape="\\"),
            Transaction.category.ilike(pat, escape="\\"),
        ))
    if f.review:
        q = q.where(Transaction.review_status == f.review)
    return q


def fetch(db: Session, f: TxnFilter, sort: str = "date", direction: str = "asc",
          limit: int | None = None, offset: int = 0) -> list[Transaction]:
    col = SORT_FIELDS.get(sort, Transaction.txn_date)
    order = col.desc().nulls_last() if direction == "desc" else col.asc().nulls_last()
    q = build_query(f).order_by(order, Transaction.statement_id, Transaction.seq)
    if offset:
        q = q.offset(offset)
    if limit:
        q = q.limit(limit)
    return list(db.scalars(q))


def count(db: Session, f: TxnFilter) -> int:
    return db.scalar(select(func.count()).select_from(build_query(f).subquery())) or 0


# --- aggregation -------------------------------------------------------------------

def jsonify(value: Any) -> Any:
    """Recursively convert Decimals to strings (exact) and dates to ISO strings."""
    if isinstance(value, Decimal):
        cents = value.quantize(Decimal("0.01"))
        return str(cents) if cents == value else str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonify(v) for v in value]
    return value


def _d(v: Decimal | None) -> Decimal:
    return v if v is not None else Decimal("0")


def totals_by_currency(txns: list[Transaction]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for t in txns:
        if t.amount is None:
            continue
        cur = t.currency or "UNKNOWN"
        row = out.setdefault(cur, {"debits": Decimal("0"), "credits": Decimal("0"), "debit_count": 0, "credit_count": 0})
        if t.txn_type == "credit":
            row["credits"] += t.amount
            row["credit_count"] += 1
        else:
            row["debits"] += t.amount
            row["debit_count"] += 1
    return out


def primary_currency(txns: list[Transaction], statements: list[Statement] | None = None) -> str | None:
    if statements:
        curs = {s.statement_currency for s in statements if s.statement_currency}
        if len(curs) == 1:
            return curs.pop()
    totals = totals_by_currency(txns)
    if not totals:
        return None
    return max(totals.items(), key=lambda kv: kv[1]["debit_count"])[0]


def group_sum(txns: list[Transaction], key: str, currency: str | None, txn_type: str = "debit") -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": Decimal("0"), "count": 0, "ids": []})
    for t in txns:
        if t.amount is None or t.txn_type != txn_type or (currency and t.currency != currency):
            continue
        if key == "merchant":
            label = t.merchant or "(unknown merchant)"
        elif key == "category":
            label = t.category
        elif key == "day":
            label = t.txn_date.isoformat() if t.txn_date else "(no date)"
        elif key == "month":
            label = t.txn_date.strftime("%Y-%m") if t.txn_date else "(no date)"
        elif key == "currency":
            label = t.original_currency or t.currency or "UNKNOWN"
        else:
            raise ValueError(key)
        g = groups[label]
        g["total"] += t.amount
        g["count"] += 1
        g["ids"].append(str(t.id))
    rows = [{"label": k, **v} for k, v in groups.items()]
    if key in ("day", "month"):
        rows.sort(key=lambda r: r["label"])
    else:
        rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


DISTRIBUTION_BUCKETS = [Decimal(x) for x in ("50", "100", "250", "500", "1000", "2500", "5000")]


def distribution(txns: list[Transaction], currency: str | None) -> list[dict[str, Any]]:
    edges = [Decimal("0"), *DISTRIBUTION_BUCKETS]
    bounds = [(low, edges[i + 1] if i + 1 < len(edges) else None) for i, low in enumerate(edges)]
    counts = [0] * len(bounds)
    totals = [Decimal("0")] * len(bounds)
    for t in txns:
        if t.amount is None or t.txn_type != "debit" or (currency and t.currency != currency):
            continue
        for i, (low, high) in enumerate(bounds):
            if t.amount >= low and (high is None or t.amount < high):
                counts[i] += 1
                totals[i] += t.amount
                break
    return [
        {
            "label": f"{low:,.0f}–{high:,.0f}" if high is not None else f"{low:,.0f}+",
            "min": str(low), "max": str(high) if high is not None else None,
            "count": counts[i], "total": totals[i],
        }
        for i, (low, high) in enumerate(bounds)
    ]


def currency_breakdown(txns: list[Transaction]) -> list[dict[str, Any]]:
    """Transactions grouped by the currency they were *made* in (original currency when foreign)."""
    groups: dict[str, dict[str, Any]] = {}
    for t in txns:
        if t.txn_type != "debit":
            continue
        made_in = t.original_currency or t.currency or "UNKNOWN"
        g = groups.setdefault(made_in, {"currency": made_in, "count": 0, "original_total": Decimal("0"),
                                        "billed": defaultdict(lambda: Decimal("0"))})
        g["count"] += 1
        if t.original_currency and t.original_amount is not None:
            g["original_total"] += t.original_amount
        elif t.amount is not None:
            g["original_total"] += t.amount
        if t.amount is not None:
            g["billed"][t.currency or "UNKNOWN"] += t.amount
    rows = []
    for g in groups.values():
        rows.append({**g, "billed": dict(g["billed"])})
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


def analytics(db: Session, f: TxnFilter, currency: str | None = None) -> dict[str, Any]:
    txns = fetch(db, f)
    statements = list(db.scalars(select(Statement).where(Statement.id.in_(f.statement_ids))))
    cur = currency or primary_currency(txns, statements)
    dated = [t.txn_date for t in txns if t.txn_date]
    totals = totals_by_currency(txns)
    needs_review = sum(1 for t in txns if t.review_status == "needs_review")
    return {
        "currency": cur,
        "currencies": sorted(totals.keys()),
        "totals": totals,
        "transaction_count": len(txns),
        "needs_review": needs_review,
        "date_range": [min(dated).isoformat(), max(dated).isoformat()] if dated else None,
        "by_category": group_sum(txns, "category", cur),
        "by_month": group_sum(txns, "month", cur),
        "by_day": group_sum(txns, "day", cur),
        "top_merchants": group_sum(txns, "merchant", cur)[:10],
        "distribution": distribution(txns, cur),
        "currency_breakdown": currency_breakdown(txns),
    }
