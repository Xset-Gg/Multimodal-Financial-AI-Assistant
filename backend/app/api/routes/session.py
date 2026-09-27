from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_session, current_session, optional_session, set_session_cookie
from app.db.session import get_db
from app.models import AuditEvent, Statement, UserSession
from app.schemas import RetentionIn, SessionOut
from app.services.statements import apply_retention, delete_all_session_data

router = APIRouter(prefix="/api/session", tags=["session"])


def _out(db: Session, sess: UserSession) -> SessionOut:
    s = get_settings()
    n = db.scalar(select(func.count()).select_from(Statement).where(Statement.session_id == sess.id)) or 0
    return SessionOut(
        retention_days=sess.retention_days,
        allowed_retention_days=list(s.allowed_retention_days),
        statement_count=n,
        vision_enabled=s.groq_enabled,
        llm_enabled=s.groq_enabled,
        vision_model=s.groq_vision_model if s.groq_enabled else None,
        text_model=s.groq_text_model if s.groq_enabled else None,
        max_upload_mb=s.max_upload_mb,
        max_pdf_pages=s.max_pdf_pages,
        created_at=sess.created_at,
    )


@router.post("", response_model=SessionOut)
def ensure_session(request: Request, response: Response, db: Session = Depends(get_db)) -> SessionOut:
    """Return the current anonymous session, creating one (and its cookie) if needed."""
    sess = optional_session(request, db)
    if sess is None:
        sess, token = create_session(db)
        set_session_cookie(response, token)
    return _out(db, sess)


@router.get("", response_model=SessionOut)
def get_session_info(db: Session = Depends(get_db), sess: UserSession = Depends(current_session)) -> SessionOut:
    return _out(db, sess)


@router.put("/retention", response_model=SessionOut)
def set_retention(body: RetentionIn, db: Session = Depends(get_db),
                  sess: UserSession = Depends(current_session)) -> SessionOut:
    try:
        apply_retention(db, sess, body.days)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _out(db, sess)


@router.delete("/data")
def delete_all_data(db: Session = Depends(get_db), sess: UserSession = Depends(current_session)) -> dict[str, int]:
    return {"deleted_statements": delete_all_session_data(db, sess)}


@router.get("/audit")
def audit_log(db: Session = Depends(get_db), sess: UserSession = Depends(current_session)) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AuditEvent).where(AuditEvent.session_id == sess.id).order_by(AuditEvent.id.desc()).limit(100)
    )
    return [
        {"id": r.id, "action": r.action, "statement_id": str(r.statement_id) if r.statement_id else None,
         "detail": r.detail, "created_at": r.created_at.isoformat()}
        for r in rows
    ]
