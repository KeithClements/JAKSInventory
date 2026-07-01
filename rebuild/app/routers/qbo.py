"""
app/routers/qbo.py
==================
QuickBooks Online routes (Phase 1B): OAuth connect/callback/disconnect, the
one-time generic-item setup, and the one-click invoice push.

Admin-gated for connection management; the push itself is allowed for any signed-in
user (the bookkeeper pushes invoices). Everything here is additive — none of it is
reachable from the money paths, and a QBO failure only ever flashes an error.
"""
from __future__ import annotations

import logging
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_current_user_id, get_db, require_admin
from app.services import qbo_client
from app.services.qbo_client import QBOError
from app.services.qbo_service import QBOSyncService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/qbo", tags=["qbo"])


@router.get("/connect")
def qbo_connect(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Kick off the OAuth dance — redirect the admin to Intuit's consent screen."""
    try:
        url = qbo_client.authorize_url(db)
    except QBOError as exc:
        return RedirectResponse(f"/settings/?error={url_quote(str(exc))}", status_code=303)
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
def qbo_callback(request: Request, db: Session = Depends(get_db)):
    """Intuit redirects here with ?code&realmId&state — swap for tokens."""
    qp = request.query_params
    if qp.get("error"):
        return RedirectResponse(
            f"/settings/?error={url_quote('QBO connect cancelled: ' + qp.get('error', ''))}",
            status_code=303,
        )
    code, realm, state = qp.get("code", ""), qp.get("realmId", ""), qp.get("state", "")
    if not code or not realm:
        return RedirectResponse(
            f"/settings/?error={url_quote('QBO callback missing code/realm.')}", status_code=303
        )
    try:
        qbo_client.exchange_code(db, code, realm, state)
    except QBOError as exc:
        return RedirectResponse(f"/settings/?error={url_quote(str(exc))}", status_code=303)
    return RedirectResponse("/settings/?qbo_connected=1", status_code=303)


@router.post("/disconnect")
def qbo_disconnect(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    qbo_client.disconnect(db)
    return RedirectResponse("/settings/?qbo_disconnected=1", status_code=303)


@router.post("/setup-items")
def qbo_setup_items(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """One-time: create the generic income items the push maps to."""
    result = QBOSyncService(db).ensure_default_items()
    if result.get("ok"):
        msg = (
            f"QBO items ready — created {len(result.get('created', []))}, "
            f"income account '{result.get('income_account', '')}'."
        )
        return RedirectResponse(f"/settings/?qbo_msg={url_quote(msg)}", status_code=303)
    return RedirectResponse(
        f"/settings/?error={url_quote('QBO item setup failed: ' + result.get('error', ''))}",
        status_code=303,
    )


@router.post("/invoices/{invoice_id}/push")
def qbo_push_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    result = QBOSyncService(db, current_user_id=user_id).push_invoice(invoice_id)
    if result.get("ok"):
        if result.get("skipped"):
            msg = "Invoice is already synced to QuickBooks."
        else:
            msg = f"Invoice pushed to QuickBooks (id {result.get('qbo_id', '')})."
        return RedirectResponse(f"/invoices/{invoice_id}?ok={url_quote(msg)}", status_code=303)
    return RedirectResponse(
        f"/invoices/{invoice_id}?error={url_quote('QBO push failed: ' + result.get('error', ''))}",
        status_code=303,
    )


@router.post("/push-payment/{payment_id}")
def qbo_push_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """One-click push of a customer payment to QuickBooks (links to its already-
    synced invoice(s)). Fail-soft like the invoice push — flashes the error, never
    500s, and never touches the money path."""
    result = QBOSyncService(db, current_user_id=user_id).push_payment(payment_id)
    if result.get("ok"):
        if result.get("skipped"):
            msg = "Payment is already synced to QuickBooks."
        else:
            msg = f"Payment pushed to QuickBooks (id {result.get('qbo_id', '')})."
        return RedirectResponse(f"/payments/{payment_id}?ok={url_quote(msg)}", status_code=303)
    return RedirectResponse(
        f"/payments/{payment_id}?error={url_quote('QBO push failed: ' + result.get('error', ''))}",
        status_code=303,
    )


@router.post("/push-bill/{bill_id}")
def qbo_push_bill(
    bill_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """R3 — one-click push of an APPROVED/PAID vendor bill to QuickBooks as a
    Bill (AP/COGS side). Fail-soft like every other push: success → synced stamp,
    failure → error stamp + flash, never 500s, never touches the money path.
    Redirects back to the bill's PO workspace (bills live there)."""
    from app.models.purchase_order import VendorBill

    bill = db.query(VendorBill).filter(VendorBill.id == bill_id).first()
    back = f"/purchase-orders/{bill.po_id}" if (bill and bill.po_id) else "/purchase-orders/"

    result = QBOSyncService(db, current_user_id=user_id).push_vendor_bill(bill_id)
    if result.get("ok"):
        if result.get("skipped"):
            msg = "Vendor bill is already synced to QuickBooks."
        else:
            msg = f"Vendor bill pushed to QuickBooks (id {result.get('qbo_id', '')})."
        return RedirectResponse(f"{back}?ok={url_quote(msg)}", status_code=303)
    return RedirectResponse(
        f"{back}?error={url_quote('QBO push failed: ' + result.get('error', ''))}",
        status_code=303,
    )


@router.post("/push-credit-memo/{cm_id}")
def qbo_push_credit_memo(
    cm_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """R3 — one-click push of a credit memo to QuickBooks as a CreditMemo against
    the synced customer. Fail-soft, same contract as the invoice/payment/bill
    pushes; flashes the outcome on the credit-memo detail page."""
    result = QBOSyncService(db, current_user_id=user_id).push_credit_memo(cm_id)
    if result.get("ok"):
        if result.get("skipped"):
            msg = "Credit memo is already synced to QuickBooks."
        else:
            msg = f"Credit memo pushed to QuickBooks (id {result.get('qbo_id', '')})."
        return RedirectResponse(f"/credit-memos/{cm_id}?ok={url_quote(msg)}", status_code=303)
    return RedirectResponse(
        f"/credit-memos/{cm_id}?error={url_quote('QBO push failed: ' + result.get('error', ''))}",
        status_code=303,
    )


_ALL_UNSYNCED_MODES = {"all_unsynced", "all", "unsynced"}


async def _parse_batch_request(request: Request) -> tuple[list[int], str | None]:
    """Read invoice_ids + mode from a JSON body or a form post (tolerant of both)."""
    mode = None
    raw_ids: list = []
    if "application/json" in (request.headers.get("content-type", "") or ""):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            mode = body.get("mode")
            raw_ids = body.get("invoice_ids") or body.get("ids") or []
        if not isinstance(raw_ids, list):
            raw_ids = []
    else:
        form = await request.form()
        mode = form.get("mode")
        raw_ids = form.getlist("invoice_ids") or form.getlist("ids")
        if not raw_ids and form.get("invoice_ids"):
            raw_ids = str(form.get("invoice_ids")).split(",")
    ids = [int(str(x).strip()) for x in raw_ids if str(x).strip().isdigit()]
    return ids, (str(mode).strip().lower() if mode else None)


def _run_push_batch(
    svc: QBOSyncService, ids: list[int], mode: str | None
) -> list[dict]:
    """Blocking QBO push loop, run OFF the event loop (see the route below).

    Each ``push_invoice`` is a SYNCHRONOUS ``httpx`` round-trip to Intuit
    (``qbo_client``). Looping N of them inline inside the ``async`` route would
    block the event loop for the whole batch — freezing every other request on
    this (LAN-served) server for the duration (§23.3 Phase 1). This runs in a
    worker thread instead. Each invoice is pushed INDEPENDENTLY (push_invoice
    commits / marks per invoice), so one failure never rolls back the others."""
    if mode in _ALL_UNSYNCED_MODES:
        # §21 — pending_only: skip ERROR invoices so a bulk "Sync All" doesn't
        # re-hammer Intuit with known failures (the background retry worker
        # re-attempts those on its own schedule, under a retry ceiling).
        ids = svc.unsynced_invoice_ids(pending_only=True)

    results: list[dict] = []
    for iid in ids:
        r = svc.push_invoice(iid)
        ok = bool(r.get("ok"))
        entry = {"id": iid, "ok": ok}
        if ok:
            entry["qbo_id"] = r.get("qbo_id", "")
            if r.get("skipped"):
                entry["skipped"] = r["skipped"]
        else:
            entry["error"] = r.get("error", "")
        results.append(entry)
    return results


@router.post("/invoices/push-batch")
async def qbo_push_batch(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Bulk-push invoices to QBO. Accepts a list of invoice IDs (JSON
    ``{"invoice_ids":[...]}`` or form ``invoice_ids``) OR ``mode=all_unsynced``
    (every finalized invoice with qbo_sync_status != 'synced').

    The blocking push loop is offloaded to a worker thread via
    ``run_in_threadpool`` so a bulk push never freezes the event loop
    (§23.3 Phase 1). Returns per-invoice results: {id, ok, qbo_id|error}."""
    svc = QBOSyncService(db, current_user_id=user_id)
    ids, mode = await _parse_batch_request(request)
    results = await run_in_threadpool(_run_push_batch, svc, ids, mode)

    return JSONResponse({
        "count": len(results),
        "pushed": sum(1 for e in results if e["ok"] and not e.get("skipped")),
        "failed": sum(1 for e in results if not e["ok"]),
        "results": results,
    })


@router.get("/status")
def qbo_status(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """JSON connection + sync-count summary (debug / settings panel)."""
    return JSONResponse(QBOSyncService(db).connection_summary())
