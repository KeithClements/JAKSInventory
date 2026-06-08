"""
app/routers/credit_memos.py
===========================
R8 — Customer credit memos (CM-YYYY-NNNN), read-only views.

A credit memo is an INDEPENDENT financial document (NOT a negative invoice).
They are *created* elsewhere — from the invoice workspace
(POST /invoices/{id}/issue-credit-memo), accepted RAs, and approved warranties.
This router only surfaces them:

  GET /credit-memos/        → list (newest first, optional ?status= filter)
  GET /credit-memos/{id}    → read-only detail (lines + allocations)

Phase 1 is read-only; apply / close / reverse remain service-only
(see CreditMemoService).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import CreditMemoStatus
from app.deps import get_db
from app.models.credit_memo import CreditMemo, CreditMemoAllocation

router = APIRouter(prefix="/credit-memos", tags=["credit-memos"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

# status slug → display label (slug "all" means no filter)
_CM_STATUS_TABS: list[tuple[str, str]] = [
    ("all", "All"),
    (CreditMemoStatus.OPEN, "Open"),
    (CreditMemoStatus.PARTIAL, "Partial"),
    (CreditMemoStatus.APPLIED, "Applied"),
    (CreditMemoStatus.REVERSED, "Reversed"),
]


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def credit_memo_list(
    request: Request,
    status: str = "all",
    db: Session = Depends(get_db),
):
    # Counts always from the full (unfiltered) dataset so the tab counts are
    # stable regardless of the active filter (mirrors the other L2 lists).
    raw_counts = dict(
        db.query(CreditMemo.status, func.count(CreditMemo.id))
          .group_by(CreditMemo.status)
          .all()
    )
    counts = {"all": sum(raw_counts.values())}
    for s in (CreditMemoStatus.OPEN, CreditMemoStatus.PARTIAL,
              CreditMemoStatus.APPLIED, CreditMemoStatus.REVERSED):
        counts[s] = raw_counts.get(s, 0)

    query = (
        db.query(CreditMemo)
        .options(joinedload(CreditMemo.customer))
        .order_by(CreditMemo.id.desc())
    )
    if status and status != "all":
        query = query.filter(CreditMemo.status == status)
    credit_memos = query.limit(200).all()

    return templates.TemplateResponse(
        request,
        "credit_memos/index.html",
        {
            "credit_memos": credit_memos,
            "tabs": _CM_STATUS_TABS,
            "status": status,
            "counts": counts,
            "CreditMemoStatus": CreditMemoStatus,
        },
    )


# ── Detail (read-only) ──────────────────────────────────────────────────────

@router.get("/{cm_id}", response_class=HTMLResponse)
def credit_memo_detail(cm_id: int, request: Request, db: Session = Depends(get_db)):
    cm = (
        db.query(CreditMemo)
        .options(
            joinedload(CreditMemo.customer),
            joinedload(CreditMemo.original_invoice),
            joinedload(CreditMemo.lines),
            joinedload(CreditMemo.allocations).joinedload(CreditMemoAllocation.invoice),
        )
        .filter(CreditMemo.id == cm_id)
        .first()
    )
    if not cm:
        return RedirectResponse("/credit-memos/", status_code=303)
    active_allocations = [a for a in cm.allocations if not a.is_reversed]
    return templates.TemplateResponse(
        request,
        "credit_memos/detail.html",
        {
            "cm": cm,
            "active_allocations": active_allocations,
            "CreditMemoStatus": CreditMemoStatus,
        },
    )
