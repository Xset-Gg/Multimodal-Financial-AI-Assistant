"""Groq vision model integration.

The model is asked to *transcribe* what is printed — values are returned as the
exact strings on the page and are then parsed by the same deterministic code used
for Docling output. The model is instructed never to compute or infer values and
to list fields it cannot read in ``uncertain_fields``.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.processing.categorize import CATEGORIES
from app.processing.types import PageContent, RawTransaction

log = logging.getLogger(__name__)


class VisionUnavailable(RuntimeError):
    """Raised when vision extraction is needed but no Groq API key is configured."""


class VisionError(RuntimeError):
    """Raised when the vision API call fails. Messages never include document contents."""


SYSTEM_PROMPT = f"""You are a meticulous transcription engine for credit-card and bank statements.
You read ONE page image and return JSON only. Rules:
- Transcribe values EXACTLY as printed. Never calculate, total, convert currencies, or invent values.
- Only include rows that are individual transactions (purchases, payments, refunds, fees, interest).
  Do NOT include summary rows such as totals, previous/new balance, minimum payment or credit limit.
- "date" and "posting_date": copy the printed text exactly (e.g. "15/09/2025", "Sep 15", "15 SEP").
- "amount": the billed/posted amount string exactly as printed, including any minus sign, parentheses or CR/DR marker.
- "currency": ISO code of the billed amount if printed or declared on the page, else null.
- "original_amount"/"original_currency": only if a foreign-currency amount is printed for that row.
- "type": "credit" for payments/refunds/credits (often marked CR, minus or parentheses), else "debit".
- "category_guess": one of {", ".join(CATEGORIES)}.
- "uncertain_fields": list any field names you could not read with confidence (blurry, cut off, ambiguous).
- If a value is unreadable, set it to null and list it in uncertain_fields. Never guess.
- "raw_text": the full printed row text.
- "statement": header information if visible on this page, values copied as printed, else null.
- "page_text": a plain transcription of other text on the page (headings, summary labels and values), max 1500 chars.
Output schema:
{{"is_statement": bool,
 "statement": {{"issuer": str|null, "currency": str|null, "period_start": str|null, "period_end": str|null,
   "total_purchases": str|null, "total_credits": str|null, "previous_balance": str|null, "new_balance": str|null}},
 "transactions": [{{"date": str|null, "posting_date": str|null, "description": str|null, "merchant": str|null,
   "amount": str|null, "currency": str|null, "original_amount": str|null, "original_currency": str|null,
   "type": "debit"|"credit", "category_guess": str|null, "uncertain_fields": [str], "raw_text": str}}],
 "page_text": str}}"""


@dataclass
class VisionPageResult:
    page_number: int
    is_statement: bool
    transactions: list[RawTransaction]
    statement: dict[str, Any] = field(default_factory=dict)
    page_text: str = ""

    def to_page_content(self) -> PageContent:
        lines = [ln for ln in self.page_text.splitlines() if ln.strip()]
        return PageContent(page_number=self.page_number, method="vision", lines=lines)


def _s(value: Any, limit: int = 300) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text and text.lower() not in ("null", "none", "n/a") else None


def parse_vision_payload(payload: dict[str, Any], page_number: int) -> VisionPageResult:
    txns: list[RawTransaction] = []
    for row in payload.get("transactions") or []:
        if not isinstance(row, dict):
            continue
        uncertain = [str(f) for f in (row.get("uncertain_fields") or []) if isinstance(f, str)]
        field_map = {"date": "txn_date", "amount": "amount", "merchant": "merchant", "currency": "currency",
                     "description": "merchant", "original_amount": "original_amount"}
        hints = sorted({field_map.get(f, f) for f in uncertain if f in field_map})
        description = _s(row.get("description")) or _s(row.get("merchant")) or ""
        currency = (_s(row.get("currency"), 3) or "").upper() or None
        orig_currency = (_s(row.get("original_currency"), 3) or "").upper() or None
        type_hint = row.get("type") if row.get("type") in ("debit", "credit") else None
        txns.append(
            RawTransaction(
                page_number=page_number,
                source_method="vision",
                raw_text=_s(row.get("raw_text"), 1000) or description,
                date_text=_s(row.get("date"), 40),
                posting_date_text=_s(row.get("posting_date"), 40),
                description=description,
                amount_text=_s(row.get("amount"), 40),
                currency_hint=currency if currency and len(currency) == 3 else None,
                original_amount_text=_s(row.get("original_amount"), 40),
                original_currency=orig_currency if orig_currency and len(orig_currency) == 3 else None,
                type_hint=type_hint,
                merchant_hint=_s(row.get("merchant"), 120),
                category_hint=_s(row.get("category_guess"), 40),
                uncertain_hints=hints,
            )
        )
    statement = payload.get("statement") if isinstance(payload.get("statement"), dict) else {}
    return VisionPageResult(
        page_number=page_number,
        is_statement=bool(payload.get("is_statement", True)),
        transactions=txns,
        statement={k: _s(v, 60) for k, v in (statement or {}).items()},
        page_text=_s(payload.get("page_text"), 4000) or "",
    )


class GroqVisionClient:
    def __init__(self, http: httpx.Client | None = None) -> None:
        settings = get_settings()
        if not settings.groq_api_key:
            raise VisionUnavailable(
                "Vision extraction requires GROQ_API_KEY. Set it in the backend environment to process "
                "images and scanned PDFs."
            )
        self._key = settings.groq_api_key.get_secret_value()
        self._model = settings.groq_vision_model
        self._url = settings.groq_base_url.rstrip("/") + "/chat/completions"
        self._http = http or httpx.Client(timeout=settings.groq_timeout_seconds)

    def extract_page(self, image: bytes, mime: str, page_number: int) -> VisionPageResult:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        body = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Page {page_number}. Return the JSON object."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        try:
            resp = self._http.post(self._url, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as exc:
            raise VisionError(f"Vision API request failed ({type(exc).__name__}).") from exc
        if resp.status_code != 200:
            # Error bodies can echo input; log only the status.
            log.warning("groq vision returned HTTP %s", resp.status_code)
            raise VisionError(f"Vision API returned HTTP {resp.status_code}.")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            payload = json.loads(content)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise VisionError("Vision model returned an unreadable response.") from exc
        if not isinstance(payload, dict):
            raise VisionError("Vision model returned an unexpected structure.")
        return parse_vision_payload(payload, page_number)
