"""Deterministic natural-language → QuerySpec parser.

Used when no LLM is configured, and as the fallback when the LLM's output fails
validation. It covers the common question shapes (totals, top-N, filters by
merchant/category/date/amount/currency, counts, group-bys, statement summary facts).
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal

from app.ai.spec import QuerySpec, QuestionContext
from app.processing.amounts import ISO_CURRENCIES
from app.processing.dates import DateContext, find_dates

CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "Dining": ["restaurant", "restaurants", "dining", "dine", "food", "eating out", "eat out", "cafe", "cafes",
               "café", "coffee", "takeaway", "take-away", "food delivery", "meals", "lunch", "dinner", "breakfast"],
    "Groceries": ["grocery", "groceries", "supermarket", "supermarkets", "hypermarket"],
    "Transport": ["transport", "transportation", "taxi", "taxis", "rides", "ride hailing", "fuel", "petrol",
                  "gas station", "parking", "commute", "commuting"],
    "Travel": ["travel", "travelling", "traveling", "flight", "flights", "hotel", "hotels", "airline", "airlines",
               "trip", "trips", "vacation", "holiday"],
    "Shopping": ["shopping", "clothes", "clothing", "fashion", "electronics", "online shopping", "retail"],
    "Entertainment": ["entertainment", "streaming", "movies", "movie", "cinema", "games", "gaming", "subscriptions",
                      "subscription"],
    "Utilities & Telecom": ["utilities", "utility", "bills", "phone bill", "mobile", "internet", "telecom"],
    "Health": ["health", "medical", "pharmacy", "doctor", "fitness", "gym", "healthcare"],
    "Education": ["education", "course", "courses", "school", "tuition", "books"],
    "Fees & Interest": ["fee", "fees", "interest", "bank charges", "finance charges", "charges and fees"],
    "Transfers": ["transfer", "transfers", "atm", "cash withdrawal", "withdrawals"],
}
CURRENCY_WORDS = {
    "dollar": "USD", "dollars": "USD", "usd": "USD", "euro": "EUR", "euros": "EUR", "pound": "GBP",
    "pounds": "GBP", "sterling": "GBP", "riyal": "QAR", "riyals": "QAR", "qr": "QAR", "dirham": "AED",
    "dirhams": "AED", "rupee": "INR", "rupees": "INR", "yen": "JPY",
}
OUT_OF_SCOPE = re.compile(
    r"\b(credit\s+score|invest|investment|stocks?|crypto|bitcoin|loan\s+approval|mortgage|tax\s+return|"
    r"predict|forecast|next\s+month|will\s+i|should\s+i|financial\s+advice|net\s+worth|salary)\b",
    re.I,
)
HELP = re.compile(r"^(hi|hello|hey|help|what can you do|what can i ask|thanks|thank you|how does this work)\W*$", re.I)
STOP_MERCHANT = {"the", "a", "an", "my", "all", "restaurants", "restaurant", "shops", "stores", "home", "night", "least",
                 "most", "once", "total", "each", "every"}

_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_CUR = r"(?:\s*(?:" + "|".join(sorted(ISO_CURRENCIES)) + r"|qr|\$|€|£|dollars?|euros?|riyals?|pounds?|dirhams?))?"
AMOUNT_BOUNDS = [
    (re.compile(rf"\bbetween\s*(?:\$|€|£)?{_NUM}{_CUR}\s*(?:and|-|to)\s*(?:\$|€|£)?{_NUM}", re.I), "between", False),
    (re.compile(rf"\b(?:above|over|more\s+than|greater\s+than|exceeding|larger\s+than|bigger\s+than|>)\s*(?:\$|€|£)?{_NUM}", re.I), "min", True),
    (re.compile(rf"\b(?:at\s+least|minimum\s+of|>=|no\s+less\s+than)\s*(?:\$|€|£)?{_NUM}", re.I), "min", False),
    (re.compile(rf"\b(?:below|under|less\s+than|cheaper\s+than|smaller\s+than|<)\s*(?:\$|€|£)?{_NUM}", re.I), "max", True),
    (re.compile(rf"\b(?:at\s+most|up\s+to|maximum\s+of|<=|no\s+more\s+than)\s*(?:\$|€|£)?{_NUM}", re.I), "max", False),
]
FACTS = [
    ("minimum_due", re.compile(r"\bminimum\s+(?:payment|amount|due)\b", re.I)),
    ("closing_balance", re.compile(r"\b(?:new|closing|statement|outstanding|current)\s+balance\b|\btotal\s+(?:amount\s+)?due\b|\bhow\s+much\s+do\s+i\s+owe\b", re.I)),
    ("previous_balance", re.compile(r"\b(?:previous|opening|last)\s+(?:statement\s+)?balance\b", re.I)),
    ("period", re.compile(r"\b(?:statement|billing)\s+(?:period|cycle|dates?)\b|\bwhat\s+period\b", re.I)),
]


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _year_for_month(month: int, ctx: QuestionContext) -> int:
    if ctx.date_min and ctx.date_max:
        for y in range(ctx.date_max.year, ctx.date_min.year - 1, -1):
            start, end = _month_range(y, month)
            if start <= ctx.date_max and end >= ctx.date_min:
                return y
    anchor = ctx.date_max or date.today()
    return anchor.year if month <= anchor.month else anchor.year - 1


MONTH_NAMES = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTH_NAMES.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTH_NAMES["sept"] = 9


def _parse_dates(text: str, ctx: QuestionContext, spec: QuerySpec) -> str:
    """Fill date filters. Returns text with the consumed date phrases removed."""
    anchor = ctx.date_max or date.today()
    t = text

    rel = re.search(r"\b(this|last|previous|past)\s+(month|week)\b", t)
    if rel:
        which, unit = rel.group(1), rel.group(2)
        if unit == "month":
            y, m = anchor.year, anchor.month
            if which in ("last", "previous", "past"):
                y, m = (y - 1, 12) if m == 1 else (y, m - 1)
            spec.date_from, spec.date_to = _month_range(y, m)
            spec.notes.append(
                f'"{rel.group(0)}" was interpreted relative to the end of your statement data '
                f"({anchor.isoformat()}), i.e. {calendar.month_name[m]} {y}."
            )
        else:
            end = anchor if which == "this" else anchor - timedelta(days=anchor.weekday() + 1)
            start = end - timedelta(days=end.weekday()) if which == "this" else end - timedelta(days=6)
            spec.date_from, spec.date_to = start, end
            spec.notes.append(f'"{rel.group(0)}" was interpreted relative to the end of your statement data ({anchor.isoformat()}).')
        return t.replace(rel.group(0), " ")

    last_n = re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", t)
    if last_n:
        n = int(last_n.group(1))
        spec.date_from, spec.date_to = anchor - timedelta(days=n - 1), anchor
        spec.notes.append(f"The last {n} days were counted back from the end of your statement data ({anchor.isoformat()}).")
        return t.replace(last_n.group(0), " ")

    dctx = DateContext(order="MDY" if ctx.statement_currency == "USD" else "DMY", period_end=ctx.date_max,
                       default_year=anchor.year)
    found = [(s, e, d) for s, e, d in find_dates(t, dctx) if d.value]
    if len(found) >= 2 and re.search(r"\b(between|from)\b", t):
        a, b = sorted([found[0][2].value, found[1][2].value])  # type: ignore[type-var]
        spec.date_from, spec.date_to = a, b
        for s, e, _ in reversed(found[:2]):
            t = t[:s] + " " + t[e:]
        return t
    if found:
        s, e, d = found[0]
        spec.date_from = spec.date_to = d.value
        if "inferred_year" in d.reasons or not re.search(r"\d{4}", d.text):
            spec.notes.append(f'"{d.text.strip()}" was read as {d.value.isoformat()} (year taken from your statement).')  # type: ignore[union-attr]
        if "ambiguous_day_month_order" in d.reasons:
            spec.notes.append(f'"{d.text.strip()}" is ambiguous; it was read as {d.value.isoformat()}.')  # type: ignore[union-attr]
        return t[:s] + " " + t[e:]

    nth = re.search(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b", t)
    if nth and ctx.date_max:
        day = int(nth.group(1))
        try:
            spec.date_from = spec.date_to = date(anchor.year, anchor.month, day)
            spec.notes.append(f'"the {nth.group(1)}th" was read as {spec.date_from.isoformat()}.')
        except ValueError:
            pass
        return t.replace(nth.group(0), " ")

    month = re.search(r"\b(?:in|for|during|of)?\s*(" + "|".join(sorted(MONTH_NAMES, key=len, reverse=True)) + r")\b(?:\s+(\d{4}))?", t)
    if month and month.group(1) in MONTH_NAMES and (month.group(0).strip() != month.group(1) or len(month.group(1)) > 3):
        m = MONTH_NAMES[month.group(1)]
        y = int(month.group(2)) if month.group(2) else _year_for_month(m, ctx)
        spec.date_from, spec.date_to = _month_range(y, m)
        return t.replace(month.group(0), " ")

    year = re.search(r"\b(?:in|during)\s+(20\d{2})\b", t)
    if year:
        y = int(year.group(1))
        spec.date_from, spec.date_to = date(y, 1, 1), date(y, 12, 31)
        return t.replace(year.group(0), " ")

    if re.search(r"\btoday\b", t):
        spec.date_from = spec.date_to = date.today()
    elif re.search(r"\byesterday\b", t):
        spec.date_from = spec.date_to = date.today() - timedelta(days=1)
    return t


def _parse_currency(text: str, original: str) -> str | None:
    for code in re.findall(r"\b([A-Z]{3})\b", original.upper()):
        if code in ISO_CURRENCIES and re.search(rf"\b{code}\b", original, re.I):
            # avoid matching ordinary words that happen to be codes (e.g. "all", "try")
            if code in {"ALL", "TRY", "CUP", "TOP", "MOP", "BAM"} and not re.search(rf"\b{code}\b", original):
                continue
            return code
    for word, code in CURRENCY_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return code
    if "$" in original:
        return "USD"
    if "€" in original:
        return "EUR"
    if "£" in original:
        return "GBP"
    return None


def _parse_category(text: str) -> tuple[list[str], str]:
    cats: list[str] = []
    for cat, words in CATEGORY_SYNONYMS.items():
        for w in sorted(words, key=len, reverse=True):
            if re.search(rf"\b{re.escape(w)}\b", text):
                cats.append(cat)
                text = re.sub(rf"\b{re.escape(w)}\b", " ", text)
                break
    return cats, text


def _parse_merchant(text: str, ctx: QuestionContext) -> str | None:
    best: str | None = None
    for merchant in ctx.merchants:
        key = re.sub(r"[^a-z0-9 ]", " ", merchant.lower()).split()
        tokens = [k for k in key if len(k) >= 3 and k not in STOP_MERCHANT]
        if not tokens:
            continue
        full = " ".join(key)
        if re.search(rf"\b{re.escape(full)}\b", text):
            cand = merchant
        elif re.search(rf"\b{re.escape(tokens[0])}\b", text):
            cand = tokens[0]
        else:
            continue
        if best is None or len(cand) > len(best):
            best = cand
    if best:
        return best
    m = re.search(r"\b(?:at|with|from|to|on)\s+([a-z0-9][a-z0-9'&.\- ]{2,40}?)(?:\s+(?:in|on|during|this|last|since|for|above|over|below|under)\b|[?.!,]|$)", text)
    if m:
        cand = m.group(1).strip()
        words = cand.split()
        if words and words[0] not in STOP_MERCHANT and not re.search(r"\b(spend|spent|transactions?|purchases?|average|each|day|days|month|week)\b", cand):
            return cand
    return None


def parse_question(question: str, ctx: QuestionContext) -> QuerySpec:
    original = question.strip()
    text = " " + re.sub(r"\s+", " ", original.lower()) + " "

    if HELP.match(original.strip()):
        return QuerySpec(intent="help", txn_type=None)
    if OUT_OF_SCOPE.search(text):
        return QuerySpec(intent="unsupported", txn_type=None, unsupported_reason=(
            "That isn't something your statement can answer. I only report facts computed from the "
            "transactions and summary figures in your uploaded statements."))
    for fact, pat in FACTS:
        if pat.search(text):
            return QuerySpec(intent="statement_info", fact=fact, txn_type=None)  # type: ignore[arg-type]

    followup = bool(re.match(r"^\s*(what about|how about|and|only|same for|now for|what if)\b", text)) and ctx.previous_spec
    spec = ctx.previous_spec.model_copy(deep=True) if followup and ctx.previous_spec else QuerySpec(intent="sum")
    spec.notes = []
    spec.source = "rules"

    # Amount bounds (consume them so their numbers aren't read as dates/merchants).
    working = text
    for pat, kind, exclusive in AMOUNT_BOUNDS:
        m = pat.search(working)
        if not m:
            continue
        if kind == "between":
            lo, hi = sorted([_dec(m.group(1)), _dec(m.group(2))])
            spec.min_amount, spec.max_amount = lo, hi
            spec.min_exclusive = spec.max_exclusive = False
        elif kind == "min" and spec.min_amount is None:
            spec.min_amount, spec.min_exclusive = _dec(m.group(1)), exclusive
        elif kind == "max" and spec.max_amount is None:
            spec.max_amount, spec.max_exclusive = _dec(m.group(1)), exclusive
        working = working.replace(m.group(0), " ")

    currency = _parse_currency(working, original)
    if currency:
        spec.currency = currency
        working = re.sub(rf"\b{currency.lower()}\b", " ", working)
        for word, code in CURRENCY_WORDS.items():
            if code == currency:
                working = re.sub(rf"\b{word}\b", " ", working)

    date_before = (spec.date_from, spec.date_to)
    if not followup:
        spec.date_from = spec.date_to = None
    working = _parse_dates(working, ctx, spec)
    if followup and (spec.date_from, spec.date_to) == (None, None):
        spec.date_from, spec.date_to = date_before

    cats, working = _parse_category(working)
    if cats:
        spec.categories = cats
    merchant = _parse_merchant(working, ctx)
    if merchant and not cats:
        spec.merchant = merchant
    elif not followup:
        spec.merchant = None

    # Transaction direction.
    credit_words = re.search(r"\b(refunds?|credits?|payments?\s+(?:made|received)|repayments?|cash\s*back|reimburse\w*|returns?)\b", text)
    spend_words = re.search(r"\b(spend|spent|spending|purchases?|bought|buy|expenses?|cost|paid\s+(?:at|to|for))\b", text)
    if credit_words and not spend_words:
        spec.txn_type = "credit"
    elif spend_words:
        spec.txn_type = "debit"
    elif not followup:
        spec.txn_type = None  # decided per intent below

    # Intent.
    n_match = re.search(r"\btop\s+(\d{1,3})\b|\b(\d{1,3})\s+(?:largest|biggest|highest|most\s+expensive|smallest)\b", text)
    n = int(n_match.group(1) or n_match.group(2)) if n_match else None
    if followup and not re.search(r"\b(how many|largest|biggest|top|which|show|list|average|by)\b", text):
        pass  # keep previous intent
    elif re.search(r"\b(which|what|top|biggest|largest)\s+(merchants?|stores?|shops?|places?|vendors?)\b|\bmerchant\b.*\bmost\b|\bby\s+merchant\b|\bper\s+merchant\b|\bwhere\s+did\s+i\s+spend\s+(?:the\s+)?most\b", text):
        spec.intent, spec.group_by = "group", "merchant"
        spec.limit = n or (1 if re.search(r"\b(the\s+most|most\??$|which\s+merchant\b(?!s))", text) else 10)
    elif re.search(r"\b(which|what|top)\s+categor(y|ies)\b|\bby\s+categor(y|ies)\b|\bcategor(y|ies)\s+breakdown\b|\bper\s+category\b|\bspending\s+categories\b", text):
        spec.intent, spec.group_by = "group", "category"
        spec.limit = n or (1 if re.search(r"\bthe\s+most\b|\bwhich\s+category\b", text) else None)
    elif re.search(r"\b(daily|per\s+day|by\s+day|each\s+day)\b", text):
        spec.intent, spec.group_by = "group", "day"
    elif re.search(r"\b(monthly|per\s+month|by\s+month|each\s+month)\b", text):
        spec.intent, spec.group_by = "group", "month"
    elif re.search(r"\b(by|per)\s+currenc(y|ies)\b|\bwhich\s+currencies\b", text):
        spec.intent, spec.group_by = "group", "currency"
    elif re.search(r"\b(how\s+many|number\s+of|count)\b", text):
        spec.intent = "count"
    elif re.search(r"\b(average|avg|mean|typical)\b", text):
        spec.intent = "average"
    elif re.search(r"\b(largest|biggest|highest|most\s+expensive|top|smallest|cheapest|lowest)\b", text):
        spec.intent = "top"
        asc = re.search(r"\b(smallest|cheapest|lowest)\b", text)
        spec.sort = "amount_asc" if asc else "amount_desc"
        singular = re.search(r"\b(largest|biggest|highest|most\s+expensive|smallest|cheapest|lowest)\s+(single\s+)?(transaction|purchase|expense|payment|charge|one)\b(?!s)", text)
        spec.limit = n or (1 if singular else 5)
    elif re.search(r"\b(show|list|display|find|give\s+me|which\s+transactions|what\s+transactions|all\s+transactions|transactions\s+(above|over|below|under|at|from|on|in|with))\b", text) \
            or (re.search(r"\btransactions?\b", text) and not re.search(r"\bhow\s+much\b", text)):
        spec.intent = "list"
        spec.sort = spec.sort or "date_asc"
    elif re.search(r"\b(how\s+much|total|spend|spent|spending|cost|paid|pay)\b", text):
        spec.intent = "sum"
    elif not followup:
        has_filter = any([spec.merchant, spec.categories, spec.date_from, spec.currency, spec.min_amount, spec.max_amount])
        if has_filter:
            spec.intent = "sum"
        else:
            return QuerySpec(intent="unsupported", txn_type=None, unsupported_reason=(
                "I couldn't map that question to your transaction data. Try asking about totals, merchants, "
                "categories, dates, amounts or currencies."))

    if spec.txn_type is None and spec.intent in ("sum", "top", "average", "group") and not credit_words:
        spec.txn_type = "debit"
    if spec.intent in ("sum", "top", "average", "group") and spec.txn_type == "debit" and not spend_words:
        spec.notes.append("Only purchases/debits are included; payments and refunds are excluded.")
    return spec
