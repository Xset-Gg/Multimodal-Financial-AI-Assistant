"""Deterministic amount and currency parsing.

Handles thousands separators in both conventions (1,234.56 / 1.234,56), negative
notations ((12.00), -12.00, 12.00-, 12.00 CR), inline currency codes/symbols and
three-decimal currencies (KWD, BHD, OMR, JOD).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

ISO_CURRENCIES = {
    "QAR", "USD", "EUR", "GBP", "AED", "SAR", "KWD", "BHD", "OMR", "JOD", "EGP", "INR", "PKR",
    "LKR", "PHP", "JPY", "CNY", "HKD", "SGD", "MYR", "THB", "IDR", "KRW", "AUD", "NZD", "CAD",
    "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "TRY", "ZAR", "NGN", "KES", "MAD", "TND",
    "BRL", "MXN", "ARS", "CLP", "COP", "RUB", "UAH", "ILS", "LBP", "BDT", "NPR", "VND",
}
THREE_DECIMAL = {"KWD", "BHD", "OMR", "JOD", "TND"}
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "IDR"}

# Symbols map to a currency but some are ambiguous ($ is used by many currencies).
SYMBOLS: dict[str, tuple[str, bool]] = {
    "$": ("USD", True),
    "US$": ("USD", False),
    "€": ("EUR", False),
    "£": ("GBP", False),
    "¥": ("JPY", True),
    "₹": ("INR", False),
    "₩": ("KRW", False),
    "₺": ("TRY", False),
    "﷼": ("QAR", True),
}

_CODES = "|".join(sorted(ISO_CURRENCIES))
_SYMS = "|".join(re.escape(s) for s in sorted(SYMBOLS, key=len, reverse=True))

# An amount token: optional sign/paren, optional currency, digits with separators,
# and a mandatory 2-3 digit decimal part (credit-card amounts are always printed with decimals;
# requiring them avoids matching dates, card suffixes and reference numbers).
AMOUNT_RE = re.compile(
    rf"""
    (?<![\w.,/])
    (?P<open>\()?
    (?P<pre_sign>[-−+])?\s?
    (?:(?P<pre_code>{_CODES})\s?)?
    (?P<sym>{_SYMS})?\s?
    (?P<sign2>[-−])?
    (?P<num>\d{{1,3}}(?:[,.\s']\d{{3}})*[.,]\d{{2,3}}|\d+[.,]\d{{2,3}})
    (?!\d)
    (?:\s?(?P<post_code>{_CODES})\b)?
    (?P<close>\))?
    (?:\s?(?P<suffix>CR|Cr|cr|DR|Dr|dr|-|−)(?![\w]))?
    """,
    re.VERBOSE,
)


@dataclass
class ParsedAmount:
    value: Decimal | None
    currency: str | None = None
    currency_ambiguous: bool = False
    is_credit: bool | None = None  # True when the notation marks a credit/negative
    uncertain: bool = False
    reasons: list[str] = field(default_factory=list)
    text: str = ""


def _to_decimal(num: str, currency: str | None) -> tuple[Decimal | None, bool, list[str]]:
    """Convert a numeric string with separators to Decimal. Returns (value, uncertain, reasons)."""
    s = num.replace(" ", "").replace("'", "").replace(" ", "")
    reasons: list[str] = []
    uncertain = False
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    dec_sep: str | None
    if last_dot >= 0 and last_comma >= 0:
        dec_sep = "." if last_dot > last_comma else ","
    elif last_dot >= 0:
        dec_sep = "." if s.count(".") == 1 else None
    elif last_comma >= 0:
        # "1,234" is thousands; "12,34" / "12,345" (single group) is treated as decimal comma
        # only when followed by 2 digits (3-digit groups are ambiguous).
        tail = s[last_comma + 1 :]
        if s.count(",") == 1 and len(tail) == 2 or s.count(",") == 1 and len(tail) == 3 and currency in THREE_DECIMAL:
            dec_sep = ","
        else:
            dec_sep = None
    else:
        dec_sep = None

    if dec_sep is None:
        cleaned = s.replace(",", "").replace(".", "")
    else:
        thousands = "," if dec_sep == "." else "."
        int_part, _, frac = s.rpartition(dec_sep)
        int_part = int_part.replace(thousands, "")
        if not re.fullmatch(r"\d*", int_part) or not re.fullmatch(r"\d+", frac):
            return None, True, ["malformed_amount"]
        cleaned = f"{int_part or '0'}.{frac}"
        if len(frac) == 3 and currency not in THREE_DECIMAL:
            uncertain = True
            reasons.append("unusual_decimal_places")
    try:
        return Decimal(cleaned), uncertain, reasons
    except InvalidOperation:
        return None, True, ["malformed_amount"]


def parse_amount(text: str | None, currency_hint: str | None = None) -> ParsedAmount:
    """Parse a single amount string (e.g. a table cell or a vision-extracted value)."""
    if text is None:
        return ParsedAmount(None, uncertain=True, reasons=["missing_amount"])
    raw = str(text).strip()
    if not raw:
        return ParsedAmount(None, uncertain=True, reasons=["missing_amount"], text=raw)
    m = AMOUNT_RE.search(raw)
    if not m:
        # Accept bare integers in a dedicated amount cell (e.g. "1500" or "1,500").
        bare = re.fullmatch(r"\s*(\()?([-−])?\s?(\d{1,3}(?:,\d{3})+|\d+)(\))?\s*(CR|DR)?\s*", raw, re.I)
        if not bare:
            return ParsedAmount(None, uncertain=True, reasons=["malformed_amount"], text=raw)
        value = Decimal(bare.group(3).replace(",", ""))
        credit = bool(bare.group(1) or bare.group(2) or (bare.group(5) or "").upper() == "CR")
        return ParsedAmount(value, currency_hint, False, credit if credit else None, False, [], raw)
    return _from_match(m, currency_hint)


def _from_match(m: re.Match[str], currency_hint: str | None) -> ParsedAmount:
    currency: str | None = m.group("pre_code") or m.group("post_code")
    ambiguous = False
    if not currency and m.group("sym"):
        currency, ambiguous = SYMBOLS[m.group("sym")]
    if not currency:
        currency = currency_hint
    value, uncertain, reasons = _to_decimal(m.group("num"), currency)
    suffix = (m.group("suffix") or "").upper()
    negative = bool(
        (m.group("open") and m.group("close"))
        or m.group("pre_sign") in ("-", "−")
        or m.group("sign2")
        or suffix in ("-", "−")
    )
    credit: bool | None = None
    if negative or suffix == "CR":
        credit = True
    elif suffix == "DR":
        credit = False
    return ParsedAmount(value, currency, ambiguous, credit, uncertain, reasons, m.group(0).strip())


def find_amounts(line: str, currency_hint: str | None = None) -> list[tuple[int, int, ParsedAmount]]:
    """Find all amount tokens in a line. Returns (start, end, parsed)."""
    matches = list(AMOUNT_RE.finditer(line))
    parsed = [_from_match(m, currency_hint) for m in matches]
    for i in range(len(matches) - 1):
        m, nxt = matches[i], matches[i + 1]
        code = m.group("post_code")
        # "412.66 EUR 378.50": a code sitting between two amounts prefixes the *following* one.
        if code and not nxt.group("pre_code") and not nxt.group("sym") and nxt.start() - m.end() <= 2:
            parsed[i].currency = currency_hint
            parsed[i].text = parsed[i].text[: parsed[i].text.rfind(code)].strip()
            parsed[i + 1] = _from_match(nxt, code)
            parsed[i + 1].currency = code
            parsed[i + 1].text = f"{code} {parsed[i + 1].text}"
    return [(m.start(), m.end(), p) for m, p in zip(matches, parsed, strict=True) if p.value is not None]


CURRENCY_CODE_RE = re.compile(rf"\b({_CODES})\b")


def find_currency_codes(text: str) -> list[str]:
    return CURRENCY_CODE_RE.findall(text)


def quantize(value: Decimal, currency: str | None) -> Decimal:
    places = 3 if currency in THREE_DECIMAL else 0 if currency in ZERO_DECIMAL else 2
    return value.quantize(Decimal(1).scaleb(-places))
