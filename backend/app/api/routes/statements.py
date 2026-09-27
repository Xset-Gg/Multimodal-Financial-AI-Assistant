from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import owned_statement
from app.core.security import current_session
from app.db.session import db_session, get_db
from app.models import Document, ExtractedPage, ProcessingEvent, Statement, Transaction, UserSession
from app.schemas import EventOut, PageOut, StatementDetail, StatementOut
from app.services import storage
from app.services.query import totals_by_currency
from app.services.statements import create_statement, delete_statement, retry_statement
from app.services.storage import UploadValidationError

router = APIRouter(prefix="/api/statements", tags=["statements"])

DEMO_PDF = Path(__file__).resolve().parents[2] / "demo" / "demo_statement.pdf"
TERMINAL = {"ready", "failed"}


def _summaries(db: Session, stmts: list[Statement]) -> dict[uuid.UUID, dict[str, Any]]:
    ids = [s.id for s in stmts]
    if not ids:
        return {}
    txns = list(db.scalars(select(Transaction).where(Transaction.statement_id.in_(ids), Transaction.excluded.is_(False))))
    out: dict[uuid.UUID, dict[str, Any]] = {i: {"count": 0, "review": 0, "txns": []} for i in ids}
    for t in txns:
        o = out[t.statement_id]
        o["count"] += 1
        o["review"] += t.review_status == "needs_review"
        o["txns"].append(t)
    return out


def _to_out(stmt: Statement, summary: dict[str, Any] | None, detail: bool = False) -> StatementOut:
    model = StatementDetail if detail else StatementOut
    data = model.model_validate(stmt, from_attributes=True)
    if summary:
        data.transaction_count = summary["count"]
        data.needs_review_count = summary["review"]
        data.totals = {k: {kk: str(vv) if not isinstance(vv, int) else vv for kk, vv in v.items()}
                       for k, v in totals_by_currency(summary["txns"]).items()}
    return data


@router.get("", response_model=list[StatementOut])
def list_statements(db: Session = Depends(get_db), sess: UserSession = Depends(current_session)) -> list[StatementOut]:
    stmts = list(db.scalars(
        select(Statement).where(Statement.session_id == sess.id).order_by(Statement.created_at.desc())
    ))
    sums = _summaries(db, stmts)
    return [_to_out(s, sums.get(s.id)) for s in stmts]


def _upload(db: Session, sess: UserSession, file: UploadFile, is_demo: bool = False) -> StatementOut:
    try:
        stmt = create_statement(db, sess, file.file, file.filename, is_demo=is_demo)
    except UploadValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.refresh(stmt)
    return _to_out(stmt, None)


@router.post("", response_model=StatementOut, status_code=201)
def upload_statement(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    sess: UserSession = Depends(current_session),
) -> StatementOut:
    return _upload(db, sess, file)


@router.post("/demo", response_model=StatementOut, status_code=201)
def load_demo(db: Session = Depends(get_db), sess: UserSession = Depends(current_session)) -> StatementOut:
    """Load the bundled fictional sample statement. It runs through the real pipeline."""
    if not DEMO_PDF.exists():
        raise HTTPException(500, "Demo statement is not bundled with this build.")
    with DEMO_PDF.open("rb") as fh:
        try:
            stmt = create_statement(db, sess, fh, "SpendLens Demo Bank - September 2025.pdf", is_demo=True)
        except UploadValidationError as exc:
            raise HTTPException(500, str(exc)) from exc
    db.refresh(stmt)
    return _to_out(stmt, None)


@router.get("/{statement_id}", response_model=StatementDetail)
def get_statement(stmt: Statement = Depends(owned_statement), db: Session = Depends(get_db)) -> StatementOut:
    return _to_out(stmt, _summaries(db, [stmt]).get(stmt.id), detail=True)


@router.delete("/{statement_id}", status_code=204)
def remove_statement(stmt: Statement = Depends(owned_statement), db: Session = Depends(get_db)) -> None:
    delete_statement(db, stmt)


@router.post("/{statement_id}/retry", response_model=StatementOut)
def retry(stmt: Statement = Depends(owned_statement), db: Session = Depends(get_db)) -> StatementOut:
    if stmt.status not in TERMINAL:
        raise HTTPException(409, "Statement is still being processed.")
    retry_statement(db, stmt)
    db.refresh(stmt)
    return _to_out(stmt, None)


@router.get("/{statement_id}/file")
def get_file(stmt: Statement = Depends(owned_statement), db: Session = Depends(get_db)) -> FileResponse:
    doc = db.scalar(select(Document).where(Document.statement_id == stmt.id))
    if doc is None:
        raise HTTPException(404, "File not found")
    try:
        path = storage.path_for_key(doc.storage_key)
    except ValueError as exc:
        raise HTTPException(404, "File not found") from exc
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path,
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="statement.{doc.kind if doc.kind == "pdf" else "img"}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                       "frame-ancestors 'self'; sandbox allow-same-origin",
        },
    )


@router.get("/{statement_id}/pages", response_model=list[PageOut])
def get_pages(stmt: Statement = Depends(owned_statement), db: Session = Depends(get_db)) -> list[ExtractedPage]:
    doc = db.scalar(select(Document).where(Document.statement_id == stmt.id))
    if doc is None:
        return []
    return list(db.scalars(select(ExtractedPage).where(ExtractedPage.document_id == doc.id)
                           .order_by(ExtractedPage.page_number)))


@router.get("/{statement_id}/events", response_model=list[EventOut])
def get_events(stmt: Statement = Depends(owned_statement), db: Session = Depends(get_db)) -> list[ProcessingEvent]:
    return list(db.scalars(select(ProcessingEvent).where(ProcessingEvent.statement_id == stmt.id)
                           .order_by(ProcessingEvent.id)))


def _poll(statement_id: uuid.UUID, after: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    with db_session() as db:
        stmt = db.get(Statement, statement_id)
        if stmt is None:
            return [], None
        events = db.scalars(select(ProcessingEvent).where(
            ProcessingEvent.statement_id == statement_id, ProcessingEvent.id > after
        ).order_by(ProcessingEvent.id).limit(200))
        payload = [EventOut.model_validate(e).model_dump(mode="json") for e in events]
        count = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.statement_id == statement_id))
        state = {"status": stmt.status, "error_message": stmt.error_message, "transaction_count": count or 0}
        return payload, state


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/{statement_id}/stream")
async def stream_events(request: Request, stmt: Statement = Depends(owned_statement)) -> StreamingResponse:
    """Server-Sent Events: processing events and status, straight from the database."""
    statement_id = stmt.id
    try:
        last_id = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        last_id = 0

    async def gen() -> AsyncIterator[str]:
        nonlocal last_id
        idle = 0.0
        last_state: dict[str, Any] | None = None
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            events, state = await run_in_threadpool(_poll, statement_id, last_id)
            if state is None:
                yield _sse("deleted", {})
                return
            for e in events:
                last_id = e["id"]
                yield _sse("progress", e, e["id"])
            if state != last_state:
                yield _sse("status", state)
                last_state = state
            if state["status"] in TERMINAL and not events:
                yield _sse("end", state)
                return
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle >= 15:
                idle = 0
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })
