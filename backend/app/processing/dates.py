"""Date parsing that is explicit about ambiguity.

Statements use many formats. Numeric dates like 03/04/2025 are ambiguous
(3 April vs March 4); we detect the document's day/month order from dates that
are unambiguous (a component > 12) and fall back to a currency-region default,
flagging the result as uncertain when no evidence exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

# Ordered from most to least specific.
DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("iso", re.compile(r"(?<!\d)(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})(?!\d)")),
    ("num3", re.compile(r"(?<![\d.,])(?P<a>\d{1,2})(?P<sep>[-/.])(?P<b>\d{1,2})(?P=sep)(?P<y>\d{4}|\d{2})(?!\d|[.,]\d)")),
    ("d_mon_y", re.compile(rf"(?<!\d)(?P<d>\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]?(?P<mon>{_MON})\.?,?[\s\-/.]?(?P<y>\d{{4}}|\d{{2}})(?![\d])", re.I)),
    ("mon_d_y", re.compile(rf"(?<![A-Za-z])(?P<mon>{_MON})\.?[\s\-]?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s(?P<y>\d{{4}})(?!\d)", re.I)),
    ("d_mon", re.compile(rf"(?<!\d)(?P<d>\d{{1,2}})[\s\-/.]?(?P<mon>{_MON})(?![A-Za-z])", re.I)),
    ("mon_d", re.compile(rf"(?<![A-Za-z])(?P<mon>{_MON})\.?[\s\-]?(?P<d>\d{{1,2}})(?!\d)", re.I)),
    # Year-less numeric dates: "/" or "-" only, since "9.99" is an amount, not a date.
    ("num2", re.compile(r"(?<![\d/.,\-])(?P<a>\d{1,2})[/\-](?P<b>\d{1,2})(?![\d/.\-])")),
]

MDY_CURRENCIES = {"USD", "PHP"}  # regions that commonly print month-first


@dataclass
class DateContext:
    order: str = "DMY"  # DMY | MDY
    order_confident: bool = False
    period_start: date | None = None
    period_end: date | None = None
    default_year: int | None = None


@dataclass
class ParsedDate:
    value: date | None
    uncertain: bool
    reasons: list[str]
    text: str = ""


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def detect_order(texts: list[str], currency: str | None = None) -> tuple[str, bool]:
    """Infer DMY vs MDY from unambiguous numeric dates in the document."""
    dmy = mdy = 0
    # Only tokens that are clearly dates: with a year, or "/"/"-" separated (never "9.99"-style amounts).
    with_year = re.compile(r"(?<![\d.,])(\d{1,2})([-/.])(\d{1,2})\2(?:\d{4}|\d{2})(?!\d|[.,]\d)")
    no_year = re.compile(r"(?<![\d.,/\-])(\d{1,2})[/\-](\d{1,2})(?![\d/.,\-])")
    for text in texts:
        pairs = [(m.group(1), m.group(3)) for m in with_year.finditer(text)]
        pairs += no_year.findall(with_year.sub(" ", text))
        for a, b in pairs:
            ia, ib = int(a), int(b)
            if ia > 12 and 1 <= ib <= 12:
                dmy += 1
            elif ib > 12 and 1 <= ia <= 12:
                mdy += 1
    if dmy and not mdy:
        return "DMY", True
    if mdy and not dmy:
        return "MDY", True
    if dmy and mdy:
        return ("DMY" if dmy >= mdy else "MDY"), False
    return ("MDY" if currency in MDY_CURRENCIES else "DMY"), False


def _infer_year(month: int, day: int, ctx: DateContext) -> tuple[int | None, bool]:
    """Pick a year for a date printed without one, using the statement period."""
    end = ctx.period_end or (date(ctx.default_year, 12, 31) if ctx.default_year else None)
    if end is None:
        return None, True
    year = end.year
    # A December transaction on a January statement belongs to the previous year.
    if month > end.month + 1:
        year -= 1
    return year, False


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date_match(kind: str, m: re.Match[str], ctx: DateContext) -> ParsedDate:
    reasons: list[str] = []
    uncertain = False
    gd = m.groupdict()
    text = m.group(0)
    if kind == "iso":
        y, mo, d = int(gd["y"]), int(gd["m"]), int(gd["d"])
    elif kind in ("num3", "num2"):
        a, b = int(gd["a"]), int(gd["b"])
        if a > 12 and b <= 12:
            d, mo = a, b
        elif b > 12 and a <= 12:
            mo, d = a, b
        else:
            if ctx.order == "DMY":
                d, mo = a, b
            else:
                mo, d = a, b
            if a != b and not ctx.order_confident:
                uncertain = True
                reasons.append("ambiguous_day_month_order")
        if kind == "num3":
            y = _year(gd["y"])
        else:
            yi, unc = _infer_year(mo, d, ctx)
            if yi is None:
                return ParsedDate(None, True, ["missing_year"], text)
            y = yi
            if unc:
                uncertain = True
                reasons.append("inferred_year")
    else:
        key = gd["mon"].lower().rstrip(".")
        mo = MONTHS.get(key) or MONTHS[key[:3]]
        d = int(gd["d"])
        if gd.get("y"):
            y = _year(gd["y"])
        else:
            yi, unc = _infer_year(mo, d, ctx)
            if yi is None:
                return ParsedDate(None, True, ["missing_year"], text)
            y = yi
            if unc:
                uncertain = True
                reasons.append("inferred_year")
    value = _make(y, mo, d)
    if value is None:
        return ParsedDate(None, True, ["impossible_date"], text)
    return ParsedDate(value, uncertain, reasons, text)


def find_dates(text: str, ctx: DateContext) -> list[tuple[int, int, ParsedDate]]:
    """Find non-overlapping dates in text, most specific patterns first."""
    taken: list[tuple[int, int]] = []
    out: list[tuple[int, int, ParsedDate]] = []
    for kind, pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            parsed = parse_date_match(kind, m, ctx)
            if parsed.value is None and "impossible_date" not in parsed.reasons:
                continue
            taken.append((s, e))
            out.append((s, e, parsed))
    out.sort(key=lambda x: x[0])
    return out


def parse_date(text: str | None, ctx: DateContext) -> ParsedDate:
    if not text or not str(text).strip():
        return ParsedDate(None, True, ["missing_date"], "")
    found = find_dates(str(text), ctx)
    if not found:
        return ParsedDate(None, True, ["unparseable_date"], str(text))
    return found[0][2]
