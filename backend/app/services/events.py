"""Processing and audit event helpers. Event messages must never contain document contents."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, ProcessingEvent


def emit(
    db: Session,
    statement_id: uuid.UUID,
    stage: str,
    message: str,
    *,
    level: str = "info",
    job_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    db.add(ProcessingEvent(
        statement_id=statement_id, job_id=job_id, stage=stage, level=level, message=message[:500], data=data or {}
    ))
    if commit:
        db.commit()


def audit(db: Session, action: str, *, session_id: uuid.UUID | None, statement_id: uuid.UUID | None = None,
          detail: dict[str, Any] | None = None) -> None:
    db.add(AuditEvent(session_id=session_id, statement_id=statement_id, action=action, detail=detail or {}))
