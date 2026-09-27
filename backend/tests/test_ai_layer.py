"""AI layer: question parsing, LLM validation/fallback, deterministic execution, grounded explanations."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.ai.compose import numbers_grounded, stream_explanation
from app.ai.execute import build_context, execute
from app.ai.interpret import interpret
from app.ai.llm import LLMError
from app.ai.rules import parse_question
from app.ai.spec import QuerySpec, QuestionContext
from app.db.session import db_session
from app.models import Statement, Transaction, UserSession

CTX = QuestionContext(
    statement_currency="QAR", currencies=["EUR", "QAR", "USD"], date_min=date(2025, 9, 1), date_max=date(2025, 9, 30),
    merchants=["Starbucks City Center", "Carrefour Hypermarket", "Amazon.com", "Qatar Airways", "Talabat Food Delivery"],
    categories_present=["Dining", "Groceries", "Shopping", "Travel"],
)


def test_spend_this_month_resolves_against_statement_not_today() -> None:
    s = parse_question("How much did I spend this month?", CTX)
    assert s.intent == "sum" and s.txn_type == "debit"
    assert (s.date_from, s.date_to) == (date(2025, 9, 1), date(2025, 9, 30))
    assert any("end of your statement data" in n for n in s.notes)


def test_largest_transactions() -> None:
    s = parse_question("What were my largest transactions?", CTX)
    assert s.intent == "top" and s.sort == "amount_desc" and s.limit == 5 and s.txn_type == "debit"
    one = parse_question("What was my biggest single purchase?", CTX)
    assert one.limit == 1


def test_restaurants_maps_to_dining_category() -> None:
    s = parse_question("How much did I spend at restaurants?", CTX)
    assert s.intent == "sum" and s.categories == ["Dining"] and s.merchant is None


def test_transactions_above_amount_with_currency() -> None:
    s = parse_question("Show transactions above 500 QAR", CTX)
    assert s.intent == "list" and s.min_amount == Decimal("500") and s.min_exclusive and s.currency == "QAR"


def test_spend_on_specific_day() -> None:
    s = parse_question("What did I spend on September 15?", CTX)
    assert s.intent == "sum" and s.date_from == s.date_to == date(2025, 9, 15)


def test_top_merchant() -> None:
    s = parse_question("Which merchant did I spend the most with?", CTX)
    assert s.intent == "group" and s.group_by == "merchant" and s.limit == 1


def test_count_in_currency() -> None:
    s = parse_question("How many transactions were made in USD?", CTX)
    assert s.intent == "count" and s.currency == "USD" and s.txn_type is None


def test_known_merchant_and_between_amounts() -> None:
    # The matched token is used as a substring filter so every "Carrefour ..." branch is included.
    s = parse_question("How much did I spend at Carrefour?", CTX)
    assert s.merchant == "carrefour"
    assert parse_question("How much at Qatar Airways?", CTX).merchant == "Qatar Airways"
    s2 = parse_question("list purchases between 100 and 300", CTX)
    assert (s2.min_amount, s2.max_amount) == (Decimal("100"), Decimal("300"))


def test_follow_up_reuses_previous_query() -> None:
    first = parse_question("How much did I spend at restaurants?", CTX)
    ctx = CTX.model_copy(update={"previous_question": "x", "previous_spec": first})
    s = parse_question("what about groceries?", ctx)
    assert s.intent == "sum" and s.categories == ["Groceries"]


def test_out_of_scope_and_statement_facts() -> None:
    assert parse_question("What's my credit score?", CTX).intent == "unsupported"
    assert parse_question("Should I invest in stocks?", CTX).intent == "unsupported"
    s = parse_question("What is my minimum payment due?", CTX)
    assert s.intent == "statement_info" and s.fact == "minimum_due"
    assert parse_question("hello", CTX).intent == "help"


class FakeLLM:
    def __init__(self, payload: dict[str, Any] | Exception, stream: list[str] | None = None) -> None:
        self.payload, self.stream = payload, stream or []

    async def complete_json(self, messages: list[dict[str, Any]], max_tokens: int = 600) -> dict[str, Any]:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    async def stream_text(self, messages: list[dict[str, Any]], max_tokens: int = 400):  # type: ignore[no-untyped-def]
        for s in self.stream:
            yield s


def test_llm_interpretation_is_validated_and_falls_back() -> None:
    good = FakeLLM({"intent": "sum", "categories": ["Dining", "NotACategory"], "txn_type": "debit"})
    spec = asyncio.run(interpret("food spend?", CTX, good))  # type: ignore[arg-type]
    assert spec.source == "llm" and spec.categories == ["Dining"]
    invalid = FakeLLM({"intent": "delete_everything"})
    assert asyncio.run(interpret("How much did I spend at restaurants?", CTX, invalid)).source == "rules"  # type: ignore[arg-type]
    down = FakeLLM(LLMError("HTTP 500"))
    assert asyncio.run(interpret("How much did I spend at restaurants?", CTX, down)).categories == ["Dining"]  # type: ignore[arg-type]


def test_numbers_grounded() -> None:
    facts = ["You spent 1,234.50 QAR across 3 purchases.", "date=2025-09-15, amount=845.00"]
    assert numbers_grounded("You spent 1,234.50 QAR on 3 purchases, including 845.00 on September 15.", facts)
    assert not numbers_grounded("You spent 1,300.00 QAR.", facts)


# --- execution against the database -------------------------------------------------

def _seed() -> uuid.UUID:
    rows = [
        ("2025-09-01", "Carrefour Hypermarket", "Groceries", "412.75", "QAR", None, None, "debit"),
        ("2025-09-04", "Amazon.com", "Shopping", "331.16", "QAR", "89.99", "USD", "debit"),
        ("2025-09-10", "Payment Received", "Payments & Credits", "5000.00", "QAR", None, None, "credit"),
        ("2025-09-15", "Nobu Restaurant", "Dining", "845.00", "QAR", None, None, "debit"),
        ("2025-09-15", "Starbucks City Center", "Dining", "31.00", "QAR", None, None, "debit"),
        ("2025-09-17", "Qatar Airways", "Travel", "2450.00", "QAR", None, None, "debit"),
        ("2025-09-19", "Netflix.com", "Entertainment", "56.00", "QAR", "15.49", "USD", "debit"),
    ]
    with db_session() as db:
        sess = UserSession(token_hash=uuid.uuid4().hex, retention_days=7)
        db.add(sess)
        db.flush()
        stmt = Statement(session_id=sess.id, display_name="t.pdf", status="ready", statement_currency="QAR",
                         detected_totals={"minimum_due": "559.38"}, verification={}, extraction_methods=["docling"])
        db.add(stmt)
        db.flush()
        for i, (d, m, c, a, cur, oa, oc, tt) in enumerate(rows):
            db.add(Transaction(statement_id=stmt.id, seq=i, txn_date=date.fromisoformat(d), merchant=m,
                               merchant_normalized=m.upper(), description=m, amount=Decimal(a), currency=cur,
                               original_amount=Decimal(oa) if oa else None, original_currency=oc, txn_type=tt,
                               category=c, source_method="docling_table", raw_text=f"{d} {m} {a}", page_number=1,
                               uncertain_fields=[], flags=[]))
        return stmt.id


def _ask(question: str) -> dict[str, Any]:
    sid = _seed()
    with db_session() as db:
        ctx = build_context(db, [sid])
        spec = parse_question(question, ctx)
        return execute(db, spec, [sid], ctx).to_dict()


def test_execute_sum_this_month_excludes_credits() -> None:
    r = _ask("How much did I spend this month?")
    total = Decimal("412.75") + Decimal("331.16") + Decimal("845.00") + Decimal("31.00") + Decimal("2450.00") + Decimal("56.00")
    assert r["metrics"][0]["value"] == str(total) and r["metrics"][0]["currency"] == "QAR"
    assert r["transaction_count"] == 6 and r["computed_by"] == "deterministic"


def test_execute_restaurants_and_day() -> None:
    assert _ask("How much did I spend at restaurants?")["metrics"][0]["value"] == "876.00"


def test_execute_day_and_ids_are_traceable() -> None:
    r = _ask("What did I spend on September 15?")
    assert r["metrics"][0]["value"] == "876.00" and len(r["transaction_ids"]) == 2
    assert {row["merchant"] for row in r["table"]["rows"]} == {"Nobu Restaurant", "Starbucks City Center"}


def test_execute_top_merchant_and_largest() -> None:
    r = _ask("Which merchant did I spend the most with?")
    assert r["table"]["rows"][0]["label"] == "Qatar Airways" and r["table"]["rows"][0]["total"] == "2450.00"
    top = _ask("What were my largest transactions?")
    assert [row["amount"] for row in top["table"]["rows"]][:2] == ["2450.00", "845.00"]


def test_execute_usd_count_and_above_filter() -> None:
    r = _ask("How many transactions were made in USD?")
    assert r["metrics"][0]["value"] == 2
    assert any(m["currency"] == "USD" and m["value"] == "105.48" for m in r["metrics"])
    above = _ask("Show transactions above 500 QAR")
    assert sorted(row["amount"] for row in above["table"]["rows"]) == ["2450.00", "5000.00", "845.00"]


def test_execute_not_found_says_so() -> None:
    r = _ask("How much did I spend in March?")
    assert r["not_found"] is True and "couldn't find" in r["headline"]
    assert r["transaction_ids"] == []


def test_execute_statement_fact_and_missing_fact() -> None:
    assert "559.38" in _ask("What is my minimum payment?")["headline"]
    missing = _ask("What was my previous balance?")
    assert missing["not_found"] and "not found" in missing["headline"]


def test_explanation_rejects_hallucinated_numbers() -> None:
    sid = _seed()
    with db_session() as db:
        ctx = build_context(db, [sid])
        ans = execute(db, parse_question("How much did I spend at restaurants?", ctx), [sid], ctx)

    async def run(llm: Any) -> str:
        final = ""
        async for kind, text in stream_explanation("q", ans, llm):
            if kind == "final":
                final = text
        return final

    honest = asyncio.run(run(FakeLLM({}, ["You spent 876.00 QAR ", "on dining across 2 purchases."])))
    assert honest.startswith("You spent 876.00 QAR on dining")
    lying = asyncio.run(run(FakeLLM({}, ["You spent 900.00 QAR on dining."])))
    assert "900" not in lying and lying.startswith(ans.headline)


@pytest.mark.parametrize("q", ["", "   "])
def test_empty_question_rejected_by_schema(q: str) -> None:
    from pydantic import ValidationError

    from app.schemas import MessageIn

    with pytest.raises(ValidationError):
        MessageIn(content=q if q else "")
    assert QuerySpec(intent="sum").txn_type == "debit"
