"""
app/routers/public_docs.py
==========================
§22.7 — PUBLIC (no-login) document viewer. A signed, expiring token resolves to
exactly one quote / sales-order / invoice, rendered to PDF and served inline so a
customer can tap a link in a text/email and see their document.

This router is exempt from the login middleware (app/main.py `_AUTH_EXEMPT`/prefix)
— the token IS the credential. CSRF doesn't apply (GET only). An invalid or expired
token returns a friendly 404, never a stack trace or a login redirect.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services.document_messaging import render_pdf_or_none
from app.services.document_render import get_prepared_by
from app.services.public_links import read_doc_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/p", tags=["public"])

_GONE_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>Link unavailable</title></head>"
    "<body style='font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;"
    "padding:0 1.25rem;color:#1f2937;text-align:center'>"
    "<h1 style='font-size:1.25rem;font-weight:600'>This link has expired or is invalid</h1>"
    "<p style='color:#6b7280'>Please contact us for an up-to-date copy of your document.</p>"
    "</body></html>"
)


def _is_shareable(doc) -> bool:
    """Never expose an internal DRAFT through a public link — a link must only ever
    surface a customer-appropriate state. Everything else the customer is meant to
    see (sent/accepted/open/paid; void & cancelled already render a watermark) is OK.
    String compare is StrEnum-safe across the quote/SO/invoice status enums."""
    return str(getattr(doc, "status", "") or "").strip().lower() != "draft"


def _render_doc_pdf(db: Session, request: Request, doc_type: str, doc_id: int) -> bytes | None:
    """Render the document's PDF, reusing each router's existing print context so the
    public PDF is identical to the in-app one. Lazy imports avoid a router import cycle.
    A missing OR non-shareable (DRAFT) doc returns None → the friendly 404."""
    base = str(request.base_url)
    if doc_type == "quote":
        from app.models.quote import Quote
        from app.routers.quotes import _quote_print_context, templates
        q = db.get(Quote, doc_id)
        if q is None or not _is_shareable(q):
            return None
        return render_pdf_or_none(
            templates.env, "quotes/print.html", base,
            **_quote_print_context(db, q, request, None),
        )
    if doc_type == "invoice":
        from app.models.invoice import Invoice
        from app.routers.invoices import _invoice_print_context, templates
        inv = db.get(Invoice, doc_id)
        if inv is None or not _is_shareable(inv):
            return None
        return render_pdf_or_none(
            templates.env, "invoices/print.html", base,
            request=request, **_invoice_print_context(db, inv, None),
        )
    if doc_type == "sales_order":
        from app.models.sales_order import SalesOrder
        from app.routers.sales_orders import _so_print_context, templates
        so = db.get(SalesOrder, doc_id)
        if so is None or not _is_shareable(so):
            return None
        ctx = _so_print_context(so, db)
        ctx.setdefault("prepared_by", get_prepared_by(db, None))
        return render_pdf_or_none(
            templates.env, "sales_orders/print.html", base, request=request, **ctx,
        )
    return None


@router.get("/d/{token}")
async def public_doc(token: str, request: Request, db: Session = Depends(get_db)):
    decoded = read_doc_token(token)
    if decoded is None:
        return HTMLResponse(_GONE_HTML, status_code=404)
    doc_type, doc_id = decoded
    try:
        pdf = _render_doc_pdf(db, request, doc_type, doc_id)
    except Exception:  # noqa: BLE001 — never leak an error to an anonymous visitor
        log.exception("public_doc render failed for %s/%s", doc_type, doc_id)
        pdf = None
    if pdf is None:
        return HTMLResponse(_GONE_HTML, status_code=404)
    fname = f"{doc_type.replace('_', '-')}-{doc_id}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )
