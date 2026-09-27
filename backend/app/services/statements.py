"""Statement lifecycle: creation from an upload, retry, deletion and retention purge."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AnalysisJob,
    ChatConversation,
    Document,
    JobStatus,
    Statement,
    StatementStatus,
    UserSession,
    utcnow,
)
from app.services import storage
from app.services.events import audit, emit


def create_statement(db: Session, sess: UserSession, stream: BinaryIO, filename: str | None,
                     *, is_demo: bool = False) -> Statement:
    stored = storage.store_upload(stream)  # raises UploadValidationError
    display = storage.sanitize_filename(filename)
    try:
        stmt = Statement(
            session_id=sess.id,
            display_name=("[DEMO] " if is_demo else "") + display,
            status=StatementStatus.uploaded.value,
            is_demo=is_demo,
            expires_at=utcnow() + timedelta(days=sess.retention_days) if sess.retention_days > 0 else None,
        )
        db.add(stmt)
        db.flush()
        db.add(Document(
            statement_id=stmt.id,
            storage_key=stored.storage_key,
            original_filename=display,
            mime_type=stored.detected.mime,
            kind=stored.detected.kind,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            page_count=stored.page_count,
        ))
        job = AnalysisJob(statement_id=stmt.id, status=JobStatus.queued.value)
        db.add(job)
        db.flush()
        emit(db, stmt.id, "uploaded",
             f"Upload validated ({stored.detected.kind.upper()}, {stored.page_count or 1} page(s), "
             f"{round(stored.size_bytes / 1024)} KB); queued for analysis",
             job_id=job.id, data={"kind": stored.detected.kind, "pages": stored.page_count}, commit=False)
        audit(db, "statement.upload", session_id=sess.id, statement_id=stmt.id,
              detail={"kind": stored.detected.kind, "size_bytes": stored.size_bytes, "demo": is_demo})
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_file(stored.storage_key)
        raise
    return stmt


def retry_statement(db: Session, stmt: Statement) -> AnalysisJob:
    active = db.scalar(select(AnalysisJob).where(
        AnalysisJob.statement_id == stmt.id,
        AnalysisJob.status.in_([JobStatus.queued.value, JobStatus.running.value]),
    ))
    if active:
        return active
    job = AnalysisJob(statement_id=stmt.id, status=JobStatus.queued.value)
    db.add(job)
    stmt.status = StatementStatus.uploaded.value
    stmt.error_message = None
    db.flush()
    emit(db, stmt.id, "uploaded", "Re-queued for analysis", job_id=job.id, commit=False)
    db.commit()
    return job


def delete_statement(db: Session, stmt: Statement, *, reason: str = "user") -> None:
    """Delete a statement, its stored file and all derived data (pages, transactions, chats, events)."""
    doc = db.scalar(select(Document).where(Document.statement_id == stmt.id))
    storage_key = doc.storage_key if doc else None
    session_id, statement_id = stmt.session_id, stmt.id
    # Conversations scoped to this statement contain answers derived from it.
    for conv in db.scalars(select(ChatConversation).where(ChatConversation.statement_id == statement_id)):
        db.delete(conv)
    db.delete(stmt)
    audit(db, "statement.delete", session_id=session_id, statement_id=statement_id,
          detail={"reason": reason, "file_deleted": bool(storage_key)})
    db.commit()
    if storage_key:
        storage.delete_file(storage_key)


def delete_all_session_data(db: Session, sess: UserSession) -> int:
    stmts = list(db.scalars(select(Statement).where(Statement.session_id == sess.id)))
    for stmt in stmts:
        delete_statement(db, stmt, reason="delete_all")
    for conv in db.scalars(select(ChatConversation).where(ChatConversation.session_id == sess.id)):
        db.delete(conv)
    audit(db, "session.delete_all", session_id=sess.id, detail={"statements": len(stmts)})
    db.commit()
    return len(stmts)


def purge_expired(db: Session) -> int:
    expired = list(db.scalars(select(Statement).where(
        Statement.expires_at.is_not(None), Statement.expires_at < utcnow()
    )))
    for stmt in expired:
        delete_statement(db, stmt, reason="retention_expired")
    return len(expired)


def apply_retention(db: Session, sess: UserSession, days: int) -> None:
    settings = get_settings()
    if days not in settings.allowed_retention_days:
        raise ValueError("unsupported retention period")
    sess.retention_days = days
    for stmt in db.scalars(select(Statement).where(Statement.session_id == sess.id)):
        stmt.expires_at = stmt.created_at + timedelta(days=days)
    audit(db, "session.retention", session_id=sess.id, detail={"days": days})
    db.commit()


def get_owned_statement(db: Session, sess: UserSession, statement_id: uuid.UUID) -> Statement | None:
    return db.scalar(select(Statement).where(Statement.id == statement_id, Statement.session_id == sess.id))
