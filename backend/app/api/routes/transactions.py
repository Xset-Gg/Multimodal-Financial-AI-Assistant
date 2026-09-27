from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import resolve_statement_ids
from app.core.security import current_session
from app.db.session import get_db
from app.models import Statement, Transaction, UserSession
from app.processing.categorize import CATEGORIES, normalize_merchant_key
from app.schemas import LookupIn, TransactionOut, TransactionPage, TransactionPatch
from app.services import query as q
from app.services.events import audit

router = APIRouter(prefix="/api", tags=["transactions"])


def _filter(
    db: Session,
    sess: UserSession,
    statement_id: list[uuid.UUID] | None,
    date_from: date | None = None,
    date_to: date | None = None,
    merchant: str | None = None,
    category: list[str] | None = None,
    currency: str | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    txn_type: str | None = None,
    search: str | None = None,
    review: str | None = None,
    include_excluded: bool = False,
) -> q.TxnFilter:
    return q.TxnFilter(
        statement_ids=resolve_statement_ids(db, sess, statement_id),
        date_from=date_from,
        date_to=date_to,
        merchant=merchant or None,
        categories=category or [],
        currency=currency.upper() if currency else None,
        min_amount=min_amount,
        max_amount=max_amount,
        txn_type=txn_type,
        search=search or None,
        review=review,
        include_excluded=include_excluded,
    )


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    statement_id: list[uuid.UUID] | None = Query(default=None),
    date_from: date | None = None,
    date_to: date | None = None,
    merchant: str | None = Query(default=None, max_length=100),
    category: list[str] | None = Query(default=None),
    currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
    min_amount: Decimal | None = Query(default=None, ge=0),
    max_amount: Decimal | None = Query(default=None, ge=0),
    txn_type: Literal["debit", "credit"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    review: Literal["ok", "needs_review", "user_verified"] | None = None,
    include_excluded: bool = False,
    sort: Literal["date", "amount", "merchant", "category", "confidence"] = "date",
    direction: Literal["asc", "desc"] = "asc",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    sess: UserSession = Depends(current_session),
) -> TransactionPage:
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(422, "min_amount cannot be greater than max_amount")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")
    f = _filter(db, sess, statement_id, date_from, date_to, merchant, category, currency, min_amount, max_amount,
                txn_type, search, review, include_excluded)
    items = q.fetch(db, f, sort, direction, limit, offset)
    totals = q.jsonify(q.totals_by_currency(q.fetch(db, f)))
    return TransactionPage(
        items=[TransactionOut.model_validate(t) for t in items], total=q.count(db, f), limit=limit, offset=offset,
        totals=totals,
    )


@router.post("/transactions/lookup", response_model=list[TransactionOut])
def lookup(body: LookupIn, db: Session = Depends(get_db), sess: UserSession = Depends(current_session)) -> list[Transaction]:
    """Fetch specific transactions (e.g. the ones an AI answer was computed from)."""
    owned = resolve_statement_ids(db, sess, None, ready_only=False)
    if not body.ids or not owned:
        return []
    return list(db.scalars(
        select(Transaction)
        .where(Transaction.id.in_(body.ids), Transaction.statement_id.in_(owned))
        .order_by(Transaction.txn_date.asc().nulls_last(), Transaction.seq)
    ))


def _owned_txn(db: Session, sess: UserSession, txn_id: uuid.UUID) -> Transaction:
    txn = db.scalar(
        select(Transaction).join(Statement).where(Transaction.id == txn_id, Statement.session_id == sess.id)
    )
    if txn is None:
        raise HTTPException(404, "Transaction not found")
    return txn


@router.get("/transactions/{txn_id}", response_model=TransactionOut)
def get_transaction(txn_id: uuid.UUID, db: Session = Depends(get_db),
                    sess: UserSession = Depends(current_session)) -> Transaction:
    return _owned_txn(db, sess, txn_id)


@router.patch("/transactions/{txn_id}", response_model=TransactionOut)
def patch_transaction(txn_id: uuid.UUID, body: TransactionPatch, db: Session = Depends(get_db),
                      sess: UserSession = Depends(current_session)) -> Transaction:
    txn = _owned_txn(db, sess, txn_id)
    changes = body.model_dump(exclude_unset=True)
    corrected: list[str] = []
    for field_name in ("txn_date", "merchant", "amount", "currency", "txn_type", "category"):
        if field_name in changes and changes[field_name] is not None:
            setattr(txn, field_name, changes[field_name])
            corrected.append(field_name)
    if "merchant" in corrected:
        txn.merchant_normalized = normalize_merchant_key(txn.merchant)
    if "category" in corrected:
        txn.category_source = "user"
    if "excluded" in changes and changes["excluded"] is not None:
        txn.excluded = bool(changes["excluded"])
    if corrected or changes.get("verified"):
        # User has checked this row against the document.
        txn.uncertain_fields = [f for f in txn.uncertain_fields if f not in corrected] if not changes.get("verified") else []
        txn.review_status = "user_verified"
        txn.confidence = 1.0
    audit(db, "transaction.edit", session_id=sess.id, statement_id=txn.statement_id,
          detail={"fields": corrected, "excluded": changes.get("excluded"), "verified": changes.get("verified")})
    db.commit()
    db.refresh(txn)
    return txn


@router.get("/analytics")
def get_analytics(
    statement_id: list[uuid.UUID] | None = Query(default=None),
    currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    sess: UserSession = Depends(current_session),
) -> dict[str, Any]:
    f = _filter(db, sess, statement_id, date_from, date_to)
    result = q.analytics(db, f, currency.upper() if currency else None)
    result["statement_ids"] = [str(s) for s in f.statement_ids]
    return q.jsonify(result)


@router.get("/categories")
def categories() -> list[str]:
    return CATEGORIES
