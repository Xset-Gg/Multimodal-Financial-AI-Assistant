from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import current_session
from app.db.session import get_db
from app.models import Statement, UserSession
from app.services.statements import get_owned_statement


def owned_statement(
    statement_id: uuid.UUID,
    db: Session = Depends(get_db),
    sess: UserSession = Depends(current_session),
) -> Statement:
    stmt = get_owned_statement(db, sess, statement_id)
    if stmt is None:
        # Same response for "missing" and "not yours" to avoid leaking existence.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found")
    return stmt


def resolve_statement_ids(db: Session, sess: UserSession, requested: list[uuid.UUID] | None,
                          ready_only: bool = True) -> list[uuid.UUID]:
    q = select(Statement.id).where(Statement.session_id == sess.id)
    if ready_only:
        q = q.where(Statement.status == "ready")
    owned = set(db.scalars(q))
    if requested:
        return [s for s in requested if s in owned]
    return list(owned)
