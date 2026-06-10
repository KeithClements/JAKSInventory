"""
app/routers/credit_memos.py
===========================
R8 — Customer credit memos (CM-YYYY-NNNN).

A credit memo is an INDEPENDENT financial document (NOT a negative invoice).
They are *created* elsewhere — from the invoice workspace
(POST /invoices/{id}/issue-credit-memo), accepted RAs, and approved warranties.
This router surfaces and consumes them:

  GET  /credit-memos/             → list (newest first, optional ?status= filter)
  GET  /credit-memos/{id}         → detail (lines + allocations + apply form)
  POST /credit-memos/{id}/apply   → allocate to an open invoice (same customer)
  POST /credit-memos/{id}/close   → push remainder to customer.credit_balance

Reverse remains service-only (see CreditMemoService).
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import CreditMemoStatus, InvoiceStatus
from app.deps import get_current_user_id, get_db
from app.models.credit_memo import CreditMemo, CreditMemoAllocation
from app.models.invoice import Invoice

log = logging.getLogger(__name__)

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


# ── Detail ───────────────────────────────────────────────────────────────────

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

    # Apply targets: this customer's open invoices (an OPEN/PARTIAL CM still
    # has credit to give). balance_due is a computed property, so filter the
    # zero-balance edge cases in Python rather than SQL.
    open_invoices: list[Invoice] = []
    if cm.status in (CreditMemoStatus.OPEN, CreditMemoStatus.PARTIAL):
        open_invoices = [
            inv for inv in (
                db.query(Invoice)
                .filter(
                    Invoice.customer_id == cm.customer_id,
                    Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
                )
                .order_by(Invoice.id.desc())
                .all()
            )
            if inv.balance_due > 0.001
        ]

    return templates.TemplateResponse(
        request,
        "credit_memos/detail.html",
        {
            "cm": cm,
            "active_allocations": active_allocations,
            "open_invoices": open_invoices,
            "CreditMemoStatus": CreditMemoStatus,
        },
    )


# ── Apply to invoice ──────────────────────────────────────────────────────────

@router.post("/{cm_id}/apply", response_class=RedirectResponse)
async def credit_memo_apply(
    cm_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Allocate part (or all) of an OPEN/PARTIAL credit memo to an open invoice.

    Form fields:
      - invoice_id (required) — must belong to the CM's customer.
      - amount (optional) — blank → min(CM unapplied, invoice balance_due).
    All validation (status, bounds, customer match) lives in CreditMemoService.
    """
    from app.services.credit_memo_service import CreditMemoService

    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        return RedirectResponse("/credit-memos/", status_code=303)

    form = await request.form()
    try:
        invoice_id = int(form.get("invoice_id", 0))
    except (TypeError, ValueError):
        invoice_id = 0
    if not invoice_id:
        return RedirectResponse(
            f"/credit-memos/{cm_id}?error="
            f"{url_quote('Select an invoice to apply the credit to.')}",
            status_code=303,
        )

    amount_raw = str(form.get("amount", "")).strip()
    try:
        if amount_raw:
            try:
                amount = float(amount_raw)
            except (TypeError, ValueError):
                raise ValueError("Enter a valid dollar amount.")
            # float('nan') sails through every <=/> service guard (all NaN
            # comparisons are False) — reject non-finite before it reaches money.
            if not math.isfinite(amount):
                raise ValueError("Enter a valid dollar amount.")
        else:
            # Default: as much as fits — the smaller of what the CM has left
            # and what the invoice still owes.
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if invoice is None:
                raise ValueError(f"Invoice {invoice_id} not found")
            amount = min(cm.unapplied_amount, invoice.balance_due)
        CreditMemoService(db, user_id).apply_credit_memo(cm_id, invoice_id, amount)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/credit-memos/{cm_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except PermissionError:
        db.rollback()
        return RedirectResponse(
            f"/credit-memos/{cm_id}?error="
            f"{url_quote('You do not have permission to apply credit memos.')}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error applying credit memo %s", cm_id)
        return RedirectResponse(
            f"/credit-memos/{cm_id}?error="
            f"{url_quote('Unexpected error — credit was not applied.')}",
            status_code=303,
        )
    return RedirectResponse(f"/credit-memos/{cm_id}?saved=1", status_code=303)


# ── Close to account credit ───────────────────────────────────────────────────

@router.post("/{cm_id}/close", response_class=RedirectResponse)
async def credit_memo_close(
    cm_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Close the CM: push remaining unapplied amount to customer.credit_balance
    and mark it APPLIED (idempotent per CreditMemoService.close_credit_memo)."""
    from app.services.credit_memo_service import CreditMemoService

    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        return RedirectResponse("/credit-memos/", status_code=303)

    try:
        CreditMemoService(db, user_id).close_credit_memo(cm_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/credit-memos/{cm_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except PermissionError:
        db.rollback()
        return RedirectResponse(
            f"/credit-memos/{cm_id}?error="
            f"{url_quote('You do not have permission to close credit memos.')}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error closing credit memo %s", cm_id)
        return RedirectResponse(
            f"/credit-memos/{cm_id}?error="
            f"{url_quote('Unexpected error — credit memo was not closed.')}",
            status_code=303,
        )
    return RedirectResponse(f"/credit-memos/{cm_id}?saved=1", status_code=303)
