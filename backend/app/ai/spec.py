"""The structured query an assistant question is translated into.

The LLM (or the rule-based parser) only ever produces a QuerySpec; it never
produces numbers. All figures come from executing the spec against the database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.processing.categorize import CATEGORIES

Intent = Literal[
    "sum", "list", "top", "count", "group", "average", "statement_info", "help", "unsupported"
]
StatementFact = Literal[
    "closing_balance", "minimum_due", "total_debits", "total_credits", "previous_balance", "period", "currency"
]


class QuerySpec(BaseModel):
    intent: Intent
    group_by: Literal["merchant", "category", "day", "month", "currency"] | None = None
    date_from: date | None = None
    date_to: date | None = None
    merchant: str | None = Field(default=None, max_length=100)
    categories: list[str] = Field(default_factory=list)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    min_amount: Decimal | None = Field(default=None, ge=0)
    max_amount: Decimal | None = Field(default=None, ge=0)
    min_exclusive: bool = False
    max_exclusive: bool = False
    fact: StatementFact | None = None
    txn_type: Literal["debit", "credit"] | None = "debit"
    search: str | None = Field(default=None, max_length=100)
    sort: Literal["amount_desc", "amount_asc", "date_asc", "date_desc"] | None = None
    limit: int | None = Field(default=None, ge=1, le=200)
    notes: list[str] = Field(default_factory=list)
    unsupported_reason: str | None = None
    source: Literal["rules", "llm"] = "rules"

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, v: list[str]) -> list[str]:
        return [c for c in v if c in CATEGORIES]

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v


class QuestionContext(BaseModel):
    """Facts about the selected data the interpreter may use (no transaction rows)."""

    statement_currency: str | None
    currencies: list[str]
    date_min: date | None
    date_max: date | None
    merchants: list[str]
    categories_present: list[str]
    previous_question: str | None = None
    previous_spec: QuerySpec | None = None
