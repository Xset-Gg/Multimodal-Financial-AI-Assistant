"""Statement-level metadata: currency, period, issuer, and totals printed on the statement."""

from __future__ import annotations

import re
from collections import Counter

from app.processing.amounts import CURRENCY_CODE_RE, find_amounts
from app.processing.dates import DateContext, detect_order, find_dates
from app.processing.types import PageContent, StatementMeta

_CURRENCY_DECL = re.compile(
    r"(?:billing\s+currency|statement\s+currency|card\s+currency|currency|amounts?\s+(?:are\s+)?in)\s*[:\-]?\s*\(?([A-Z]{3})\b",
    re.I,
)
_HEADER_CCY = re.compile(r"amount\s*\(\s*([A-Z]{3})\s*\)", re.I)
_PERIOD = re.compile(r"(?:statement|billing)?\s*(?:period|cycle)|\bfrom\b.+\bto\b", re.I)
_STATEMENT_DATE = re.compile(r"statement\s+date|closing\s+date|date\s+of\s+statement", re.I)
_ISSUER = re.compile(
    r"\b([A-Z][A-Za-z&.'\-]*(?:\s+[A-Z][A-Za-z&.'\-]*){0,4}\s+(?:Bank|Banking|Card\s+Services|Financial|Credit\s+Union))\b"
)

TOTAL_LABELS: list[tuple[str, re.Pattern[str]]] = [
    ("total_debits", re.compile(r"total\s+(?:purchases|debits|spend(?:ing)?|charges|purchases\s*(?:&|and)\s*charges)|purchases\s*(?:&|and)\s*(?:other\s+)?charges", re.I)),
    ("total_credits", re.compile(r"total\s+(?:credits|payments(?:\s*(?:&|and)\s*credits)?)|payments\s*(?:&|and)\s*credits", re.I)),
    ("previous_balance", re.compile(r"previous\s+(?:statement\s+)?balance|opening\s+balance|balance\s+brought\s+forward", re.I)),
    ("closing_balance", re.compile(r"new\s+balance|closing\s+balance|statement\s+balance|total\s+(?:amount\s+)?due|outstanding\s+balance", re.I)),
    ("minimum_due", re.compile(r"minimum\s+(?:payment|amount)(?:\s+due)?", re.I)),
]


def _all_lines(pages: list[PageContent]) -> list[str]:
    lines: list[str] = []
    for p in pages:
        lines.extend(p.lines)
        for table in p.tables:
            for row in table:
                lines.append("  ".join(c for c in row if c))
    return lines


def detect_metadata(pages: list[PageContent], hint: StatementMeta | None = None) -> tuple[StatementMeta, DateContext]:
    """Detect statement metadata. ``hint`` carries values the vision model read (strings already parsed)."""
    lines = _all_lines(pages)
    meta = StatementMeta()

    # Currency: explicit declaration > column header > vision hint > most frequent code.
    for line in lines:
        m = _CURRENCY_DECL.search(line) or _HEADER_CCY.search(line)
        if m and CURRENCY_CODE_RE.fullmatch(m.group(1).upper()):
            meta.currency, meta.currency_confident = m.group(1).upper(), True
            break
    if meta.currency is None and hint and hint.currency:
        meta.currency, meta.currency_confident = hint.currency, hint.currency_confident
    if meta.currency is None:
        counts = Counter(c for line in lines for c in CURRENCY_CODE_RE.findall(line))
        if counts:
            meta.currency = counts.most_common(1)[0][0]

    order, confident = detect_order(lines, meta.currency)
    ctx = DateContext(order=order, order_confident=confident)

    # Period: look for a line with period keywords and two dates (full dates only on this pass).
    for line in lines:
        if _PERIOD.search(line):
            found = [d for _, _, d in find_dates(line, ctx) if d.value and "inferred_year" not in d.reasons]
            if len(found) >= 2:
                meta.period_start, meta.period_end = sorted([found[0].value, found[1].value])  # type: ignore[type-var]
                break
    if meta.period_end is None:
        for line in lines:
            if _STATEMENT_DATE.search(line):
                found = [d for _, _, d in find_dates(line, ctx) if d.value]
                if found:
                    meta.period_end = found[0].value
                    break
    if meta.period_end is None and hint:
        meta.period_start, meta.period_end = hint.period_start, hint.period_end
    ctx.period_start, ctx.period_end = meta.period_start, meta.period_end
    if meta.period_end is None:
        years = Counter(d.value.year for line in lines for _, _, d in find_dates(line, ctx) if d.value)
        if years:
            ctx.default_year = years.most_common(1)[0][0]

    for line in lines[:80]:
        m = _ISSUER.search(line)
        if m:
            meta.issuer = m.group(1).strip()[:120]
            break
    if meta.issuer is None and hint:
        meta.issuer = hint.issuer

    # A summary line may hold several labels ("Total Purchases $718.95  Total Payments $1,200.00"):
    # each label takes the first amount that follows it, before the next label starts.
    for line in lines:
        labels = sorted(((m.start(), m.end(), key) for key, pat in TOTAL_LABELS for m in pat.finditer(line)))
        for i, (_start, end, key) in enumerate(labels):
            if key in meta.printed_totals:
                continue
            stop = labels[i + 1][0] if i + 1 < len(labels) else len(line)
            amounts = find_amounts(line[end:stop], meta.currency)
            if amounts and amounts[0][2].value is not None:
                meta.printed_totals[key] = str(abs(amounts[0][2].value))
    if hint:
        for key, value in hint.printed_totals.items():
            meta.printed_totals.setdefault(key, value)
    return meta, ctx
