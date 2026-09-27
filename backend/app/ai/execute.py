"""Execute a QuerySpec deterministically against stored transactions.

Produces a structured answer: headline sentence, metrics, an optional table, the
IDs of every transaction the figures were computed from, and any caveats.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.spec import QuerySpec, QuestionContext
from app.models import Statement, Transaction
from app.processing.amounts import THREE_DECIMAL, ZERO_DECIMAL
from app.services import query as q

MAX_TABLE_ROWS = 50
MAX_IDS = 1000
FACT_LABELS = {
    "closing_balance": "New balance / total amount due",
    "minimum_due": "Minimum payment due",
    "total_debits": "Total purchases/debits",
    "total_credits": "Total payments/credits",
    "previous_balance": "Previous balance",
}


def money(value: Decimal, currency: str | None) -> str:
    places = 3 if currency in THREE_DECIMAL else 0 if currency in ZERO_DECIMAL else 2
    return f"{value:,.{places}f} {currency or ''}".strip()


@dataclass
class Answer:
    kind: str
    headline: str
    metrics: list[dict[str, Any]] = field(default_factory=list)
    table: dict[str, Any] | None = None
    transaction_ids: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    not_found: bool = False
    scope: dict[str, Any] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)  # plain-text facts the explanation may use

    def to_dict(self) -> dict[str, Any]:
        return q.jsonify({
            "kind": self.kind, "headline": self.headline, "metrics": self.metrics, "table": self.table,
            "transaction_ids": self.transaction_ids[:MAX_IDS], "transaction_count": len(self.transaction_ids),
            "filters": self.filters, "notes": self.notes, "warnings": self.warnings, "not_found": self.not_found,
            "scope": self.scope, "computed_by": "deterministic",
        })


def build_context(db: Session, statement_ids: list[uuid.UUID], previous_question: str | None = None,
                  previous_spec: QuerySpec | None = None) -> QuestionContext:
    txns = q.fetch(db, q.TxnFilter(statement_ids=statement_ids))
    stmts = list(db.scalars(select(Statement).where(Statement.id.in_(statement_ids)))) if statement_ids else []
    # The statement period counts too: "this month" on a statement ending 24 Sep means September
    # even if the last extracted purchase was in August.
    dated = [t.txn_date for t in txns if t.txn_date]
    dated += [d for s in stmts for d in (s.period_start, s.period_end) if d]
    merchants: dict[str, int] = defaultdict(int)
    for t in txns:
        if t.merchant:
            merchants[t.merchant] += 1
    return QuestionContext(
        statement_currency=q.primary_currency(txns, stmts),
        currencies=sorted({c for t in txns for c in (t.currency, t.original_currency) if c}),
        date_min=min(dated) if dated else None,
        date_max=max(dated) if dated else None,
        merchants=[m for m, _ in sorted(merchants.items(), key=lambda kv: -kv[1])],
        categories_present=sorted({t.category for t in txns}),
        previous_question=previous_question,
        previous_spec=previous_spec,
    )


def _row(t: Transaction) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "date": t.txn_date.isoformat() if t.txn_date else None,
        "merchant": t.merchant or t.description[:60],
        "category": t.category,
        "amount": t.amount,
        "currency": t.currency,
        "original_amount": t.original_amount,
        "original_currency": t.original_currency,
        "type": t.txn_type,
        "page": t.page_number,
        "review_status": t.review_status,
    }


TXN_COLUMNS = [
    {"key": "date", "label": "Date", "type": "date"},
    {"key": "merchant", "label": "Merchant", "type": "text"},
    {"key": "category", "label": "Category", "type": "text"},
    {"key": "amount", "label": "Amount", "type": "money"},
    {"key": "page", "label": "Page", "type": "number"},
]


def _txn_table(txns: list[Transaction], total: int | None = None) -> dict[str, Any]:
    rows = [_row(t) for t in txns[:MAX_TABLE_ROWS]]
    n = total if total is not None else len(txns)
    return {"columns": TXN_COLUMNS, "rows": rows, "total_rows": n, "truncated": n > len(rows)}


def _money_totals(txns: list[Transaction], currency: str | None) -> dict[str, Decimal]:
    """Totals keyed by currency. With a currency filter, foreign-currency rows contribute their
    original amount in that currency (and their billed amount under the billing currency)."""
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in txns:
        if currency and t.original_currency == currency and t.original_amount is not None:
            totals[currency] += t.original_amount
            if t.amount is not None and t.currency and t.currency != currency:
                totals[f"billed:{t.currency}"] += t.amount
        elif t.amount is not None:
            totals[t.currency or "UNKNOWN"] += t.amount
    return dict(totals)


def _totals_phrase(totals: dict[str, Decimal]) -> tuple[str, list[dict[str, Any]], list[str]]:
    main = {k: v for k, v in totals.items() if not k.startswith("billed:")}
    billed = {k.split(":", 1)[1]: v for k, v in totals.items() if k.startswith("billed:")}
    parts = [money(v, k) for k, v in sorted(main.items(), key=lambda kv: -kv[1])]
    metrics = [{"label": "Total", "value": v, "currency": k} for k, v in main.items()]
    phrase = " and ".join(parts) if parts else "0"
    if billed:
        b = ", ".join(money(v, k) for k, v in billed.items())
        phrase += f" (billed as {b})"
        metrics += [{"label": "Billed equivalent", "value": v, "currency": k} for k, v in billed.items()]
    return phrase, metrics, parts


def _warnings(txns: list[Transaction]) -> list[str]:
    out = []
    review = sum(1 for t in txns if t.review_status == "needs_review")
    if review:
        out.append(f"{review} of the {len(txns)} transaction(s) used here are flagged for review; "
                   "check them against the statement.")
    missing = sum(1 for t in txns if t.amount is None)
    if missing:
        out.append(f"{missing} matching transaction(s) have no readable amount and are not included in totals.")
    return out


def _filter_from_spec(spec: QuerySpec, statement_ids: list[uuid.UUID]) -> q.TxnFilter:
    return q.TxnFilter(
        statement_ids=statement_ids, date_from=spec.date_from, date_to=spec.date_to, merchant=spec.merchant,
        categories=spec.categories, currency=spec.currency, min_amount=spec.min_amount, max_amount=spec.max_amount,
        min_exclusive=spec.min_exclusive, max_exclusive=spec.max_exclusive, txn_type=spec.txn_type, search=spec.search,
    )


def _not_found(spec: QuerySpec, f: q.TxnFilter, ctx: QuestionContext) -> Answer:
    desc = ", ".join(f.describe()) or "your question"
    headline = f"I couldn't find any transactions matching {desc} in the selected statement data."
    notes = list(spec.notes)
    if ctx.date_min and ctx.date_max:
        out_of_range = (spec.date_to and spec.date_to < ctx.date_min) or (spec.date_from and spec.date_from > ctx.date_max)
        notes.append(f"Your extracted transactions cover {ctx.date_min.isoformat()} to {ctx.date_max.isoformat()}."
                     + (" The requested dates are outside that range." if out_of_range else ""))
    return Answer(kind=spec.intent, headline=headline, not_found=True, filters=f.describe(), notes=notes,
                  facts=[headline] + notes)


def execute(db: Session, spec: QuerySpec, statement_ids: list[uuid.UUID], ctx: QuestionContext) -> Answer:
    stmts = list(db.scalars(select(Statement).where(Statement.id.in_(statement_ids)))) if statement_ids else []
    scope = {
        "statements": [s.display_name for s in stmts],
        "date_range": [ctx.date_min.isoformat(), ctx.date_max.isoformat()] if ctx.date_min and ctx.date_max else None,
        "demo": any(s.is_demo for s in stmts),
    }

    if spec.intent == "help":
        return Answer(kind="help", scope=scope, headline=(
            "Ask me about the transactions in your statement: totals, merchants, categories, dates, amounts "
            "or currencies. Every number I give is calculated from your extracted transactions, and you can "
            "open the exact rows behind each answer."))
    if spec.intent == "unsupported":
        return Answer(kind="unsupported", scope=scope, not_found=True,
                      headline=spec.unsupported_reason or "I can't answer that from your statement data.")
    if not statement_ids:
        return Answer(kind=spec.intent, scope=scope, not_found=True,
                      headline="There is no processed statement to answer from yet. Upload a statement or load the demo.")

    if spec.intent == "statement_info":
        return _statement_info(spec, stmts, scope)

    f = _filter_from_spec(spec, statement_ids)
    sort_map = {"amount_desc": ("amount", "desc"), "amount_asc": ("amount", "asc"),
                "date_asc": ("date", "asc"), "date_desc": ("date", "desc")}
    sort, direction = sort_map.get(spec.sort or "date_asc", ("date", "asc"))
    txns = q.fetch(db, f, sort, direction)
    if not txns:
        ans = _not_found(spec, f, ctx)
        ans.scope = scope
        return ans

    ans = Answer(kind=spec.intent, headline="", filters=f.describe(), notes=list(spec.notes), scope=scope,
                 warnings=_warnings(txns))
    what = "purchases" if spec.txn_type == "debit" else "payments/credits" if spec.txn_type == "credit" else "transactions"

    if spec.intent == "sum":
        totals = _money_totals(txns, spec.currency)
        phrase, metrics, _ = _totals_phrase(totals)
        verb = "You spent" if spec.txn_type == "debit" else "You received" if spec.txn_type == "credit" else "The total is"
        ans.headline = f"{verb} {phrase} across {len(txns)} {what}."
        ans.metrics = metrics + [{"label": "Transactions", "value": len(txns), "currency": None}]
        ans.table = _txn_table(txns)
        ans.transaction_ids = [str(t.id) for t in txns]
    elif spec.intent == "list":
        totals = _money_totals(txns, spec.currency)
        phrase, metrics, _ = _totals_phrase(totals)
        ans.headline = f"Found {len(txns)} matching {what}, totalling {phrase}."
        ans.metrics = [{"label": "Matches", "value": len(txns), "currency": None}] + metrics
        ans.table = _txn_table(txns)
        ans.transaction_ids = [str(t.id) for t in txns]
    elif spec.intent == "top":
        limit = spec.limit or 5
        with_amount = [t for t in txns if t.amount is not None]
        cur_groups = {t.currency for t in with_amount}
        if len(cur_groups) > 1 and not spec.currency:
            primary = ctx.statement_currency
            with_amount = [t for t in with_amount if t.currency == primary]
            ans.notes.append(f"Ranked by amount in {primary}; transactions billed in other currencies were not compared.")
        top = sorted(with_amount, key=lambda t: t.amount, reverse=(spec.sort != "amount_asc"))[:limit]  # type: ignore[arg-type,return-value]
        label = "smallest" if spec.sort == "amount_asc" else "largest"
        if len(top) == 1:
            t = top[0]
            noun = what[:-1] if what.endswith("s") else what
            when = t.txn_date.isoformat() if t.txn_date else "an unreadable date"
            ans.headline = (f"Your {label} {noun} was {money(t.amount, t.currency)} "  # type: ignore[arg-type]
                            f"at {t.merchant or t.description[:60]} on {when}.")
        else:
            ans.headline = f"Your {len(top)} {label} {what}, ranked by amount."
        ans.metrics = [{"label": f"#{i + 1} {t.merchant or ''}".strip(), "value": t.amount, "currency": t.currency}
                       for i, t in enumerate(top[:3])]
        ans.table = _txn_table(top)
        ans.transaction_ids = [str(t.id) for t in top]
    elif spec.intent == "count":
        noun = (what[:-1] if what.endswith("s") else what) if len(txns) == 1 else what
        ans.headline = f"There {'is' if len(txns) == 1 else 'are'} {len(txns)} matching {noun}."
        ans.metrics = [{"label": "Count", "value": len(txns), "currency": None}]
        if spec.currency:
            totals = _money_totals(txns, spec.currency)
            phrase, metrics, _ = _totals_phrase(totals)
            ans.headline += f" Together they amount to {phrase}."
            ans.metrics += metrics
        ans.table = _txn_table(txns)
        ans.transaction_ids = [str(t.id) for t in txns]
    elif spec.intent == "average":
        per: dict[str, list[Decimal]] = defaultdict(list)
        for t in txns:
            if t.amount is not None:
                per[t.currency or "UNKNOWN"].append(t.amount)
        parts = []
        for cur, vals in sorted(per.items(), key=lambda kv: -len(kv[1])):
            avg = (sum(vals, Decimal("0")) / len(vals)).quantize(Decimal("0.01"))
            parts.append(f"{money(avg, cur)} over {len(vals)} {what}")
            ans.metrics.append({"label": f"Average ({cur})", "value": avg, "currency": cur})
        ans.headline = "The average is " + "; ".join(parts) + "."
        ans.table = _txn_table(txns)
        ans.transaction_ids = [str(t.id) for t in txns]
    elif spec.intent == "group":
        key = spec.group_by or "category"
        gcur: str | None = spec.currency if key != "currency" else None
        if key != "currency" and not gcur:
            gcur = ctx.statement_currency
        groups = q.group_sum(txns, key, gcur, spec.txn_type or "debit") if key != "currency" else _currency_groups(txns)
        if spec.limit:
            groups = groups[: spec.limit]
        if not groups:
            ans = _not_found(spec, f, ctx)
            ans.scope = scope
            return ans
        leader = groups[0]
        leader_total = money(leader["total"], leader.get("currency", gcur))
        if spec.limit == 1:
            ans.headline = (f"The {key} with the highest {what} is {leader['label']}: "
                            f"{leader_total} across {leader['count']} transaction(s).")
        elif key in ("day", "month"):
            ans.headline = f"Your {what} by {key}: {len(groups)} {key}(s) with activity."
        elif key == "currency":
            ans.headline = f"Your {what} were made in {len(groups)} currenc{'y' if len(groups) == 1 else 'ies'}."
        else:
            ans.headline = (f"Your {what} grouped by {key}. The largest is {leader['label']} at "
                            f"{leader_total} ({leader['count']} transaction(s)).")
        if gcur and key != "currency":
            other = [t for t in txns if t.currency and t.currency != gcur]
            if other:
                ans.notes.append(
                    f"{len(other)} transaction(s) billed in other currencies are not included in these {gcur} totals.")
        ans.metrics = [{"label": g["label"], "value": g["total"], "currency": g.get("currency", gcur)} for g in groups[:3]]
        ans.table = {
            "columns": [{"key": "label", "label": key.capitalize(), "type": "text"},
                        {"key": "total", "label": "Total", "type": "money"},
                        {"key": "count", "label": "Transactions", "type": "number"}],
            "rows": [{"label": g["label"], "total": g["total"], "currency": g.get("currency", gcur), "count": g["count"],
                      "ids": g["ids"]} for g in groups[:MAX_TABLE_ROWS]],
            "total_rows": len(groups), "truncated": len(groups) > MAX_TABLE_ROWS,
        }
        used = [i for g in groups for i in g["ids"]]
        ans.transaction_ids = used
        used_set = set(used)
        ans.warnings = _warnings([t for t in txns if str(t.id) in used_set])
    ans.facts = _facts(ans)
    return ans


def _currency_groups(txns: list[Transaction]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for t in txns:
        made_in = t.original_currency or t.currency or "UNKNOWN"
        amt = t.original_amount if t.original_currency else t.amount
        g = groups.setdefault(made_in, {"label": made_in, "currency": made_in, "total": Decimal("0"), "count": 0, "ids": []})
        g["count"] += 1
        g["ids"].append(str(t.id))
        if amt is not None:
            g["total"] += amt
    return sorted(groups.values(), key=lambda g: -g["count"])


def _statement_info(spec: QuerySpec, stmts: list[Statement], scope: dict[str, Any]) -> Answer:
    fact = spec.fact or "closing_balance"
    lines, metrics = [], []
    for s in stmts:
        if fact == "period":
            if s.period_start or s.period_end:
                lines.append(f"{s.display_name}: {s.period_start or '?'} to {s.period_end or '?'}")
        elif fact == "currency":
            if s.statement_currency:
                lines.append(f"{s.display_name}: {s.statement_currency}")
        else:
            value = (s.detected_totals or {}).get(fact)
            if value is not None:
                lines.append(f"{s.display_name}: {money(Decimal(value), s.statement_currency)}")
                metrics.append({"label": FACT_LABELS.get(fact, fact), "value": Decimal(value), "currency": s.statement_currency})
    label = FACT_LABELS.get(fact, fact.replace("_", " "))
    if not lines:
        return Answer(kind="statement_info", scope=scope, not_found=True, headline=(
            f"The {label.lower()} was not found in the extracted statement summary, so I can't report it."))
    headline = (f"{label} as printed on the statement: " + "; ".join(lines) + ".") if fact not in ("period", "currency") \
        else f"Statement {fact}: " + "; ".join(lines) + "."
    ans = Answer(kind="statement_info", headline=headline, metrics=metrics, scope=scope,
                 notes=["Read from the statement's summary section, not calculated from transactions."])
    ans.facts = _facts(ans)
    return ans


def _facts(ans: Answer) -> list[str]:
    facts = [ans.headline, *ans.notes, *ans.warnings]
    if ans.table:
        for r in ans.table["rows"][:15]:
            facts.append(", ".join(f"{k}={v}" for k, v in r.items() if k not in ("id", "ids") and v is not None))
    return facts
