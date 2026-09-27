"""Turn extracted page content (tables + text lines) into raw transaction candidates.

No bank-specific templates: tables are mapped by header keywords (or, failing
that, by column content), and free text is parsed line by line looking for a
leading date followed by a description and one or more amounts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.processing.amounts import CURRENCY_CODE_RE, find_amounts, parse_amount
from app.processing.dates import DateContext, find_dates
from app.processing.types import PageContent, RawTransaction

SUMMARY_RE = re.compile(
    r"\b(?:total|sub-?total|balance|previous|opening|closing|minimum\s+(?:payment|amount|due)|payment\s+due|"
    r"due\s+date|credit\s+limit|available\s+(?:credit|limit)|statement\s+date|brought\s+forward|"
    r"carried\s+forward|cash\s+limit|reward|points\s+earned)\b",
    re.I,
)
CREDIT_WORDS_RE = re.compile(
    r"\b(?:payment\s+(?:received|thank\s*you)|thank\s+you|refund|reversal|cash\s*back|credit\s+adjustment|"
    r"returned|chargeback)\b",
    re.I,
)

HEADER_WORDS = {
    "trans", "transaction", "txn", "date", "dates", "post", "posting", "posted", "value", "description", "details",
    "merchant", "narrative", "particulars", "reference", "ref", "no", "#", "amount", "amounts", "debit", "credit",
    "currency", "foreign", "original", "billing", "billed", "in", "usd", "qar", "eur", "gbp", "aed", "sar",
}

_HEADER_ROLES: list[tuple[str, re.Pattern[str]]] = [
    ("posting_date", re.compile(r"\b(?:post(?:ing|ed)?|value|settlement)\s*date\b", re.I)),
    ("date", re.compile(r"\b(?:trans(?:action)?\.?|txn|purchase|trx)?\s*date\b|^\s*date\s*$", re.I)),
    ("original_amount", re.compile(r"\b(?:original|foreign|orig\.?|fx|transaction)\s*(?:currency\s*)?amount\b", re.I)),
    ("currency", re.compile(r"^\s*(?:currency|ccy|curr\.?|orig(?:inal)?\s*currency)\s*$", re.I)),
    ("debit", re.compile(r"\b(?:debits?|withdrawals?|charges?|money\s+out|dr)\b", re.I)),
    ("credit", re.compile(r"\b(?:credits?|deposits?|payments?\s*/?\s*credits?|money\s+in|cr)\b", re.I)),
    ("amount", re.compile(r"\b(?:amount|billing|billed|value|sum)\b", re.I)),
    ("description", re.compile(
        r"\b(?:description|details?|merchant|narrative|particulars|payee|transaction(?:s)?|reference|remarks?)\b",
        re.I)),
]


@dataclass
class ColumnMap:
    date: int | None = None
    posting_date: int | None = None
    description: int | None = None
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    currency: int | None = None
    original_amount: int | None = None
    amount_currency: str | None = None  # e.g. "Amount (QAR)"


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _map_header(row: list[str]) -> ColumnMap | None:
    cmap = ColumnMap()
    hits = 0
    for i, cell in enumerate(row):
        text = (cell or "").strip()
        if not text or len(text) > 40:
            continue
        for role, pat in _HEADER_ROLES:
            if pat.search(text) and getattr(cmap, role) is None:
                # A second "amount"-like column is usually the original/foreign amount.
                setattr(cmap, role, i)
                hits += 1
                if role in ("amount", "debit", "credit"):
                    codes = CURRENCY_CODE_RE.findall(text.upper())
                    if codes:
                        cmap.amount_currency = codes[0]
                break
    if cmap.amount is None and cmap.debit is None and cmap.credit is None and cmap.original_amount is not None:
        # A lone "Transaction Amount" column is the billed amount, not a foreign one.
        cmap.amount, cmap.original_amount = cmap.original_amount, None
    has_amount = cmap.amount is not None or cmap.debit is not None or cmap.credit is not None
    if hits >= 2 and (cmap.date is not None or has_amount):
        return cmap
    return None


def _infer_columns(rows: list[list[str]], ctx: DateContext) -> ColumnMap | None:
    """Map columns by content when a table has no recognizable header."""
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    date_ratio, amt_ratio, text_len = [0.0] * ncols, [0.0] * ncols, [0.0] * ncols
    for r in rows:
        for i in range(ncols):
            c = _cell(r, i)
            if not c:
                continue
            if find_dates(c, ctx) and len(c) <= 20:
                date_ratio[i] += 1
            if find_amounts(c) and len(c) <= 25:
                amt_ratio[i] += 1
            text_len[i] += len(re.sub(r"[\d\W]", "", c))
    n = len(rows)
    date_cols = [i for i in range(ncols) if date_ratio[i] / n >= 0.5]
    amt_cols = [i for i in range(ncols) if amt_ratio[i] / n >= 0.5]
    if not date_cols or not amt_cols:
        return None
    cmap = ColumnMap(date=date_cols[0])
    if len(date_cols) > 1:
        cmap.posting_date = date_cols[1]
    cmap.amount = amt_cols[-1]
    if len(amt_cols) > 1:
        cmap.original_amount = amt_cols[-2]
    others = [i for i in range(ncols) if i not in date_cols and i not in amt_cols]
    if others:
        cmap.description = max(others, key=lambda i: text_len[i])
    return cmap


def parse_table(table: list[list[str]], page_number: int, ctx: DateContext, method: str) -> list[RawTransaction]:
    rows = [[str(c) if c is not None else "" for c in r] for r in table if any((c or "").strip() for c in r)]
    if not rows:
        return []
    cmap: ColumnMap | None = None
    start = 0
    for i, row in enumerate(rows[:6]):
        cmap = _map_header(row)
        if cmap:
            start = i + 1
            break
    if cmap is None:
        cmap = _infer_columns(rows, ctx)
        start = 0
    if cmap is None:
        return []

    out: list[RawTransaction] = []
    for row in rows[start:]:
        row_text = " | ".join(c.strip() for c in row if c and c.strip())
        date_text = _cell(row, cmap.date)
        desc = _cell(row, cmap.description)
        if cmap.description is None:
            used = {cmap.date, cmap.posting_date, cmap.amount, cmap.debit, cmap.credit, cmap.currency, cmap.original_amount}
            desc = " ".join(_cell(row, i) for i in range(len(row)) if i not in used).strip()
        debit_text, credit_text = _cell(row, cmap.debit), _cell(row, cmap.credit)
        amount_text = _cell(row, cmap.amount)
        type_hint: str | None = None
        if not amount_text:
            if debit_text and parse_amount(debit_text).value:
                amount_text, type_hint = debit_text, "debit"
            elif credit_text and parse_amount(credit_text).value:
                amount_text, type_hint = credit_text, "credit"
        orig_text = _cell(row, cmap.original_amount)
        currency_cell = _cell(row, cmap.currency).upper()
        orig_currency = None
        if currency_cell and CURRENCY_CODE_RE.fullmatch(currency_cell):
            orig_currency = currency_cell
        elif orig_text:
            codes = CURRENCY_CODE_RE.findall(orig_text.upper())
            orig_currency = codes[0] if codes else None

        has_date = bool(date_text and find_dates(date_text, ctx))
        has_amount = bool(amount_text and parse_amount(amount_text).value is not None)
        if not has_date:
            # Continuation row carrying foreign-currency details for the previous transaction.
            if out and not out[-1].original_amount_text:
                fx = [a for a in find_amounts(row_text) if a[2].currency and a[2].currency != cmap.amount_currency]
                if fx:
                    out[-1].original_amount_text = fx[0][2].text
                    out[-1].original_currency = fx[0][2].currency
                    out[-1].raw_text += "\n" + row_text
            continue
        if SUMMARY_RE.search(desc) and not CREDIT_WORDS_RE.search(desc):
            continue
        if not has_amount and not desc:
            continue
        if type_hint is None and CREDIT_WORDS_RE.search(desc):
            type_hint = "credit"
        out.append(
            RawTransaction(
                page_number=page_number,
                source_method=f"{method}_table" if method == "docling" else method,
                raw_text=row_text,
                date_text=date_text,
                posting_date_text=_cell(row, cmap.posting_date) or None,
                description=desc,
                amount_text=amount_text or None,
                currency_hint=cmap.amount_currency,
                original_amount_text=orig_text or None,
                original_currency=orig_currency,
                type_hint=type_hint,
            )
        )
    return out


def parse_line(line: str, page_number: int, ctx: DateContext, statement_currency: str | None,
               method: str) -> RawTransaction | None:
    text = re.sub(r"\s+", " ", line).strip()
    if len(text) < 8:
        return None
    dates = find_dates(text, ctx)
    if not dates:
        return None
    if dates[0][0] > 3:
        # A column header merged into the first row ("Trans Post Description Amount 08/28 ...").
        # Accept it only when every word before the date is column-header vocabulary.
        words = re.findall(r"[A-Za-z#.]+", text[: dates[0][0]].lower())
        if not words or any(w.strip(".") not in HEADER_WORDS for w in words):
            return None
        text = text[dates[0][0]:]
        dates = find_dates(text, ctx)
    # Consecutive leading dates: transaction date, then optional posting date.
    lead = [dates[0]]
    if len(dates) > 1 and dates[1][0] - dates[0][1] <= 3:
        lead.append(dates[1])
    after_dates = lead[-1][1]
    amounts = [a for a in find_amounts(text, statement_currency) if a[0] >= after_dates]
    if not amounts:
        return None
    description = text[after_dates : amounts[0][0]].strip(" |-:")
    if not re.search(r"[A-Za-z]{2}", description):
        return None
    if SUMMARY_RE.search(description) and not CREDIT_WORDS_RE.search(description):
        return None

    hints: list[str] = []
    foreign = [a for a in amounts if a[2].currency and statement_currency and a[2].currency != statement_currency]
    local = [a for a in amounts if a not in foreign]
    billed = local[-1] if local else amounts[-1]
    original = foreign[0] if foreign and foreign[0] is not billed else None
    if len(local) > 1 and not original:
        # Two plain amounts: could be (original, billed) or (amount, running balance).
        hints.append("amount")
    type_hint = "credit" if (billed[2].is_credit or CREDIT_WORDS_RE.search(description)) else None
    return RawTransaction(
        page_number=page_number,
        source_method=f"{method}_text" if method == "docling" else method,
        raw_text=text,
        date_text=lead[0][2].text,
        posting_date_text=lead[1][2].text if len(lead) > 1 else None,
        description=description,
        amount_text=billed[2].text,
        currency_hint=statement_currency,
        original_amount_text=original[2].text if original else None,
        original_currency=original[2].currency if original else None,
        type_hint=type_hint,
        uncertain_hints=hints,
    )


def parse_lines(lines: list[str], page_number: int, ctx: DateContext, statement_currency: str | None,
                method: str) -> list[RawTransaction]:
    out: list[RawTransaction] = []
    for line in lines:
        txn = parse_line(line, page_number, ctx, statement_currency, method)
        if txn:
            out.append(txn)
            continue
        # Foreign-currency detail printed on the line below the transaction.
        if out and out[-1].original_amount_text is None and not find_dates(line, ctx):
            fx = [a for a in find_amounts(line) if a[2].currency and a[2].currency != statement_currency]
            if fx and len(line) < 60:
                out[-1].original_amount_text = fx[0][2].text
                out[-1].original_currency = fx[0][2].currency
                out[-1].raw_text += "\n" + line.strip()
    return out


def parse_page(page: PageContent, ctx: DateContext, statement_currency: str | None) -> list[RawTransaction]:
    method = "docling" if page.method.startswith("docling") else page.method
    txns: list[RawTransaction] = []
    for table in page.tables:
        txns.extend(parse_table(table, page.page_number, ctx, method))
    txns.extend(parse_lines(page.lines, page.page_number, ctx, statement_currency, method))
    return txns


def transaction_signal_count(page: PageContent, ctx: DateContext) -> int:
    """How many lines look like they contain a transaction (date + amount). Used to decide on vision fallback."""
    count = 0
    candidates = page.lines + [" ".join(r) for t in page.tables for r in t]
    for line in candidates:
        if find_dates(line, ctx) and find_amounts(line):
            count += 1
    return count
