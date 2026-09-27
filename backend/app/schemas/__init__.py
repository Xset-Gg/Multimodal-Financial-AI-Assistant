"""API schemas. Decimal amounts serialize as strings to preserve precision."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FieldSerializationInfo, field_serializer, field_validator

from app.processing.amounts import quantize
from app.processing.categorize import CATEGORIES


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SessionOut(BaseModel):
    retention_days: int
    allowed_retention_days: list[int]
    statement_count: int
    vision_enabled: bool
    llm_enabled: bool
    vision_model: str | None
    text_model: str | None
    max_upload_mb: int
    max_pdf_pages: int
    created_at: datetime


class RetentionIn(BaseModel):
    days: int


class DocumentOut(ORM):
    original_filename: str
    mime_type: str
    kind: str
    size_bytes: int
    page_count: int | None


class StatementOut(ORM):
    id: uuid.UUID
    display_name: str
    status: str
    is_demo: bool
    issuer: str | None
    statement_currency: str | None
    period_start: date | None
    period_end: date | None
    error_message: str | None
    extraction_methods: list[str]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    document: DocumentOut | None = None
    transaction_count: int = 0
    needs_review_count: int = 0
    totals: dict[str, Any] = Field(default_factory=dict)


class StatementDetail(StatementOut):
    detected_totals: dict[str, Any]
    verification: dict[str, Any]


class PageOut(ORM):
    page_number: int
    method: str
    text: str
    tables: list[Any]
    char_count: int
    is_scanned: bool
    notes: list[str]


class EventOut(ORM):
    id: int
    stage: str
    level: str
    message: str
    data: dict[str, Any]
    created_at: datetime


class TransactionOut(ORM):
    id: uuid.UUID
    statement_id: uuid.UUID
    seq: int
    txn_date: date | None
    posting_date: date | None
    merchant: str | None
    description: str
    amount: Decimal | None
    currency: str | None
    original_amount: Decimal | None
    original_currency: str | None
    txn_type: str
    category: str
    category_source: str
    page_number: int | None
    source_method: str
    raw_text: str
    confidence: float
    uncertain_fields: list[str]
    flags: list[str]
    review_status: str
    excluded: bool
    duplicate_of_id: uuid.UUID | None

    @field_serializer("amount", "original_amount")
    def _money(self, value: Decimal | None, info: FieldSerializationInfo) -> str | None:
        if value is None:
            return None
        cur = self.currency if info.field_name == "amount" else self.original_currency
        return str(quantize(value, cur))


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    # Debit/credit sums per billing currency over the *whole* filtered set (exact decimal strings).
    totals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    limit: int
    offset: int


class TransactionPatch(BaseModel):
    """User corrections. Any edit marks the transaction as user-verified."""

    txn_date: date | None = None
    merchant: str | None = Field(default=None, max_length=200)
    amount: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    txn_type: Literal["debit", "credit"] | None = None
    category: str | None = None
    excluded: bool | None = None
    verified: bool | None = None

    @field_validator("category")
    @classmethod
    def _valid_category(cls, v: str | None) -> str | None:
        if v is not None and v not in CATEGORIES:
            raise ValueError("unknown category")
        return v


class LookupIn(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=1000)


class ConversationIn(BaseModel):
    statement_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(ORM):
    id: uuid.UUID
    statement_id: uuid.UUID | None
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(ORM):
    id: uuid.UUID
    role: str
    content: str
    query_spec: dict[str, Any] | None
    result: dict[str, Any] | None
    transaction_ids: list[str]
    status: str
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[ChatMessageOut]


class MessageIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=1000)
