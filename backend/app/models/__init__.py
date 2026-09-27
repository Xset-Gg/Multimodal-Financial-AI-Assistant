"""SQLAlchemy ORM models."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class StatementStatus(str, enum.Enum):
    uploaded = "uploaded"
    extracting = "extracting"
    structuring = "structuring"
    verifying = "verifying"
    ready = "ready"
    failed = "failed"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class UserSession(Base):
    """An anonymous browser session. All user data is scoped to a session."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    statements: Mapped[list[Statement]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class Statement(Base):
    __tablename__ = "statements"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default=StatementStatus.uploaded.value, index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    statement_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Summary figures printed on the statement itself (e.g. "Total purchases"), used for cross-checks.
    detected_totals: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    extraction_methods: Mapped[list[str]] = mapped_column(JSONType, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    session: Mapped[UserSession] = relationship(back_populates="statements")
    document: Mapped[Document | None] = relationship(
        back_populates="statement", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", passive_deletes=True
    )
    jobs: Mapped[list[AnalysisJob]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", passive_deletes=True
    )


class Document(Base):
    """The uploaded file. ``storage_key`` is an internal identifier, never a user-supplied path."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), unique=True
    )
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    original_filename: Mapped[str] = mapped_column(String(200))
    mime_type: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(10))  # pdf | image
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    statement: Mapped[Statement] = relationship(back_populates="document")
    pages: Mapped[list[ExtractedPage]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExtractedPage.page_number",
    )


class ExtractedPage(Base):
    """Raw extraction output per page, preserved for traceability."""

    __tablename__ = "extracted_pages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(20))  # docling | vision | docling+vision
    text: Mapped[str] = mapped_column(Text, default="")
    tables: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    is_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="pages")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_statement_date", "statement_id", "txn_date"),
        Index("ix_transactions_statement_merchant", "statement_id", "merchant_normalized"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)  # stable order within the statement
    txn_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    posting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    merchant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    merchant_normalized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    txn_type: Mapped[str] = mapped_column(String(10), default="debit")  # debit | credit
    category: Mapped[str] = mapped_column(String(40), default="Uncategorized")
    category_source: Mapped[str] = mapped_column(String(20), default="rules")
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_method: Mapped[str] = mapped_column(String(20))  # docling_table | docling_text | vision
    raw_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    uncertain_fields: Mapped[list[str]] = mapped_column(JSONType, default=list)
    flags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    review_status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | needs_review | user_verified
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    statement: Mapped[Statement] = relationship(back_populates="transactions")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value, index=True)
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    statement: Mapped[Statement] = relationship(back_populates="jobs")


class ProcessingEvent(Base):
    """Progress log for a statement. Messages must never contain statement contents."""

    __tablename__ = "processing_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    stage: Mapped[str] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(10), default="info")  # info | warning | error
    message: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Privacy-relevant actions (upload, delete, purge). Survives statement deletion; holds no contents."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    statement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True
    )
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    query_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    transaction_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="complete")  # complete | error
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
