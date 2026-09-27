from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.compose import stream_explanation
from app.ai.execute import build_context, execute
from app.ai.interpret import interpret
from app.ai.llm import GroqLLM, LLMError
from app.ai.spec import QuerySpec
from app.api.deps import resolve_statement_ids
from app.core.security import current_session
from app.db.session import db_session, get_db
from app.models import ChatConversation, ChatMessage, Statement, UserSession, utcnow
from app.schemas import ConversationDetail, ConversationIn, ConversationOut, MessageIn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["chat"])


def _owned_conversation(db: Session, sess: UserSession, conversation_id: uuid.UUID) -> ChatConversation:
    conv = db.scalar(select(ChatConversation).where(
        ChatConversation.id == conversation_id, ChatConversation.session_id == sess.id))
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.get("", response_model=list[ConversationOut])
def list_conversations(statement_id: uuid.UUID | None = Query(default=None), db: Session = Depends(get_db),
                       sess: UserSession = Depends(current_session)) -> list[ChatConversation]:
    stmt = select(ChatConversation).where(ChatConversation.session_id == sess.id)
    if statement_id:
        stmt = stmt.where(ChatConversation.statement_id == statement_id)
    return list(db.scalars(stmt.order_by(ChatConversation.updated_at.desc()).limit(50)))


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationIn, db: Session = Depends(get_db),
                        sess: UserSession = Depends(current_session)) -> ChatConversation:
    if body.statement_id:
        owned = db.scalar(select(Statement.id).where(Statement.id == body.statement_id, Statement.session_id == sess.id))
        if owned is None:
            raise HTTPException(404, "Statement not found")
    conv = ChatConversation(session_id=sess.id, statement_id=body.statement_id, title=body.title or "New conversation")
    db.add(conv)
    db.commit()
    return conv


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db),
                     sess: UserSession = Depends(current_session)) -> ChatConversation:
    return _owned_conversation(db, sess, conversation_id)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db),
                        sess: UserSession = Depends(current_session)) -> None:
    db.delete(_owned_conversation(db, sess, conversation_id))
    db.commit()


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _prepare(sess_id: uuid.UUID, conversation_id: uuid.UUID, content: str) -> dict[str, Any]:
    """Save the user message and gather everything needed to answer (runs in a worker thread)."""
    with db_session() as db:
        conv = db.scalar(select(ChatConversation).where(
            ChatConversation.id == conversation_id, ChatConversation.session_id == sess_id))
        if conv is None:
            raise HTTPException(404, "Conversation not found")
        sess = db.get(UserSession, sess_id)
        assert sess is not None
        requested = [conv.statement_id] if conv.statement_id else None
        statement_ids = resolve_statement_ids(db, sess, requested)
        previous = db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
                              .order_by(ChatMessage.created_at.desc()).limit(4)).all()
        prev_q = next((m.content for m in previous if m.role == "user"), None)
        prev_spec_raw = next((m.query_spec for m in previous if m.role == "assistant" and m.query_spec), None)
        prev_spec = None
        if prev_spec_raw:
            try:
                prev_spec = QuerySpec.model_validate(prev_spec_raw)
            except ValueError:
                prev_spec = None
        user_msg = ChatMessage(conversation_id=conv.id, role="user", content=content)
        db.add(user_msg)
        if conv.title == "New conversation":
            conv.title = content[:80]
        conv.updated_at = utcnow()
        db.flush()
        ctx = build_context(db, statement_ids, prev_q, prev_spec)
        return {"statement_ids": statement_ids, "ctx": ctx, "user_message_id": str(user_msg.id)}


def _execute(statement_ids: list[uuid.UUID], spec: QuerySpec, ctx: Any) -> Any:
    with db_session() as db:
        return execute(db, spec, statement_ids, ctx)


def _save_assistant(conversation_id: uuid.UUID, content: str, spec: QuerySpec | None, result: dict[str, Any] | None,
                    status: str) -> dict[str, Any]:
    with db_session() as db:
        msg = ChatMessage(
            conversation_id=conversation_id, role="assistant", content=content,
            query_spec=spec.model_dump(mode="json") if spec else None, result=result,
            transaction_ids=(result or {}).get("transaction_ids", []), status=status,
        )
        db.add(msg)
        conv = db.get(ChatConversation, conversation_id)
        if conv:
            conv.updated_at = utcnow()
        db.flush()
        return {"id": str(msg.id), "created_at": msg.created_at.isoformat()}


@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: uuid.UUID, body: MessageIn,
                       sess: UserSession = Depends(current_session)) -> StreamingResponse:
    """Answer a question. Streams SSE events: status, interpretation, result, token, final | error."""
    content = body.content.strip()
    if not content:
        raise HTTPException(422, "Message is empty")
    prepared = await run_in_threadpool(_prepare, sess.id, conversation_id, content)

    async def gen() -> AsyncIterator[str]:
        spec: QuerySpec | None = None
        result: dict[str, Any] | None = None
        try:
            yield _sse("status", {"stage": "interpreting", "message": "Interpreting your question"})
            try:
                llm: GroqLLM | None = GroqLLM()
            except LLMError:
                llm = None
            spec = await interpret(content, prepared["ctx"], llm)
            yield _sse("interpretation", spec.model_dump(mode="json"))
            yield _sse("status", {"stage": "calculating", "message": "Calculating from your transactions"})
            answer = await run_in_threadpool(_execute, prepared["statement_ids"], spec, prepared["ctx"])
            result = answer.to_dict()
            yield _sse("result", result)
            yield _sse("status", {"stage": "explaining", "message": "Writing the explanation"})
            final_text = ""
            async for kind, text in stream_explanation(content, answer, llm):
                if kind == "token":
                    yield _sse("token", {"text": text})
                else:
                    final_text = text
            saved = await run_in_threadpool(_save_assistant, conversation_id, final_text, spec, result, "complete")
            yield _sse("final", {"message_id": saved["id"], "content": final_text, "result": result,
                                 "query_spec": spec.model_dump(mode="json"), "created_at": saved["created_at"]})
        except Exception:  # noqa: BLE001
            log.exception("chat answer failed")
            msg = "Something went wrong while answering. Your question was saved; please retry."
            await run_in_threadpool(_save_assistant, conversation_id, msg, spec, result, "error")
            yield _sse("error", {"message": msg})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
