"""Question interpretation: LLM → validated QuerySpec, with the rule parser as fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.ai.llm import GroqLLM, LLMError
from app.ai.rules import parse_question
from app.ai.spec import QuerySpec, QuestionContext
from app.processing.categorize import CATEGORIES

log = logging.getLogger(__name__)

INTERPRETER_PROMPT = """You translate a user's question about their credit-card statement into a JSON query.
You NEVER answer the question and NEVER output amounts that are not in the question. Output JSON only with keys:
intent: one of "sum" (total spent/received), "list" (show matching transactions), "top" (largest/smallest N),
  "count" (number of transactions), "group" (totals grouped by group_by), "average",
  "statement_info" (a figure printed on the statement summary), "help", "unsupported".
group_by: "merchant" | "category" | "day" | "month" | "currency" | null
date_from, date_to: ISO dates (YYYY-MM-DD) or null. Relative phrases ("this month", "last week") are relative to
  the LATEST DATE in the statement data given in the context, not to today. A single day uses the same date for both.
merchant: merchant name substring or null. Prefer a name from the known merchants list when one matches.
categories: list drawn ONLY from the allowed categories. Map words like "restaurants" to "Dining".
currency: 3-letter ISO code or null (use it for "in USD", "made in dollars", or amount thresholds with a currency).
min_amount, max_amount: numbers from the question or null. min_exclusive/max_exclusive: true for "above/over"/"below/under".
txn_type: "debit" for spending/purchases, "credit" for payments/refunds, null for all transactions.
sort: "amount_desc" | "amount_asc" | "date_asc" | "date_desc" | null.  limit: integer or null.
fact (statement_info only): "closing_balance" | "minimum_due" | "total_debits" | "total_credits" |
  "previous_balance" | "period" | "currency".
notes: short strings explaining any interpretation you made (e.g. how a relative date was resolved).
unsupported_reason: for "unsupported", a one-sentence reason (e.g. the question needs data a statement doesn't contain,
  or asks for advice/predictions).
For follow-up questions ("what about in USD?"), reuse the previous query and change only what the user changed."""


def _context_blob(ctx: QuestionContext) -> dict[str, Any]:
    return {
        "allowed_categories": CATEGORIES,
        "statement_currency": ctx.statement_currency,
        "currencies_in_data": ctx.currencies,
        "earliest_date_in_statement_data": ctx.date_min.isoformat() if ctx.date_min else None,
        "latest_date_in_statement_data": ctx.date_max.isoformat() if ctx.date_max else None,
        "known_merchants": ctx.merchants[:150],
        "categories_in_data": ctx.categories_present,
        "previous_question": ctx.previous_question,
        "previous_query": ctx.previous_spec.model_dump(mode="json", exclude={"notes", "source"}) if ctx.previous_spec else None,
    }


async def interpret(question: str, ctx: QuestionContext, llm: GroqLLM | None) -> QuerySpec:
    if llm is not None:
        messages = [
            {"role": "system", "content": INTERPRETER_PROMPT},
            {"role": "user", "content": json.dumps({"context": _context_blob(ctx), "question": question})},
        ]
        try:
            raw = await llm.complete_json(messages)
            raw = {k: v for k, v in raw.items() if v is not None or k in ("txn_type",)}
            raw["source"] = "llm"
            spec = QuerySpec.model_validate(raw)
            if spec.date_from and spec.date_to and spec.date_from > spec.date_to:
                spec.date_from, spec.date_to = spec.date_to, spec.date_from
            if spec.min_amount is not None and spec.max_amount is not None and spec.min_amount > spec.max_amount:
                raise ValueError("inverted amount bounds")
            if spec.intent == "group" and spec.group_by is None:
                raise ValueError("group intent without group_by")
            return spec
        except (LLMError, ValidationError, ValueError) as exc:
            log.info("LLM interpretation unavailable (%s); using rule parser", type(exc).__name__)
    return parse_question(question, ctx)
