"""Anonymous session handling and request-level security helpers."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import UserSession, utcnow

CSRF_HEADER = "x-spendlens-client"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _lookup(db: Session, token: str | None) -> UserSession | None:
    if not token or len(token) > 200:
        return None
    return db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=int(timedelta(days=settings.session_ttl_days).total_seconds()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def create_session(db: Session) -> tuple[UserSession, str]:
    token = new_token()
    sess = UserSession(token_hash=hash_token(token), retention_days=get_settings().default_retention_days)
    db.add(sess)
    db.commit()
    return sess, token


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    token = request.cookies.get(get_settings().session_cookie_name)
    sess = _lookup(db, token)
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active session. Call /api/session first.")
    now = utcnow()
    if (now - sess.last_seen_at).total_seconds() > 300:
        sess.last_seen_at = now
        db.commit()
    return sess


def optional_session(request: Request, db: Session) -> UserSession | None:
    return _lookup(db, request.cookies.get(get_settings().session_cookie_name))
