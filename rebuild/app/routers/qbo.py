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
    result = QBOSyncService(db).push_invoice(invoice_id)
    if result.get("ok"):
        return RedirectResponse(f"/invoices/{invoice_id}?qbo_ok=1", status_code=303)
    return RedirectResponse(
        f"/invoices/{invoice_id}?error={url_quote('QBO push failed: ' + result.get('error', ''))}",
        status_code=303,
    )


@router.get("/status")
def qbo_status(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """JSON connection + sync-count summary (debug / settings panel)."""
    return JSONResponse(QBOSyncService(db).connection_summary())
