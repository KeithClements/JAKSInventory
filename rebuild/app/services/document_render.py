"""
app/services/document_render.py
================================
Read-only helpers for print/PDF document rendering.

Every print route does the same three things:
  1. Pull company info from settings (name, address, phone, email).
  2. Format an address into display lines for a customer or vendor.
  3. Optionally render an HTML string for WeasyPrint and fall back to the
     browser /print view if GTK libs are missing.

Keeping these in one place stops every router from re-implementing them.

TODO (Phase 2): when MessagingService gains real providers, expose
`email_document(doc_type, doc_id, recipient)` here so each /print route gets a
matching "Email" affordance. Document Engine Series only renders; sending is
intentionally out of scope until the messaging stack is wired (per
BACKEND_IMPLEMENTATION_PLAN.md Phase N).

TODO (Phase 2): digital signature embedding (DocuSign / Adobe Sign) for RA,
Warranty, and Core Return Slip would slot in around `render_pdf_or_fallback`.
Out of scope here — we only generate the document.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.settings_utils import get_setting_value_db


# ── Company settings dict ─────────────────────────────────────────────────────

def format_phone(raw: str) -> str:
    """Format a US 10-digit phone as ``(XXX) XXX-XXXX`` (handles a leading 1).
    Passes anything that isn't 10/11 digits through unchanged, so international
    or already-formatted numbers are never mangled."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    return (raw or "").strip()


def get_company_dict(db: Session) -> dict[str, Any]:
    """Standard company block used across every document template.

    §5.12 branding fields are EMPTY-SAFE: when no logo is set, ``logo_url`` is
    ``None`` so templates fall back to the text header (today's behavior — no
    regression). ``company_logo_path`` is stored relative to ``static/`` (e.g.
    ``uploads/logo_xyz.png``) by the upload route; it resolves to a ``/static``
    URL here. ``show_logo`` lets the owner suppress the logo without deleting it.
    """
    logo_path = (get_setting_value_db(db, "company_logo_path", "") or "").strip()
    show_logo = (get_setting_value_db(db, "document_show_logo", "true") or "true").strip().lower() == "true"
    logo_url = f"/static/{logo_path.lstrip('/')}" if logo_path else None
    return {
        "name":        get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address":     get_setting_value_db(db, "company_address", ""),
        "phone":       format_phone(get_setting_value_db(db, "company_phone", "")),
        "email":       get_setting_value_db(db, "company_email",   ""),
        # §5.12 branding
        "logo_url":    logo_url,
        "show_logo":   show_logo,
        "footer_text": get_setting_value_db(db, "document_footer_text", ""),
        "terms_text":  get_setting_value_db(db, "document_terms_text", ""),
    }


# ── Address formatting ───────────────────────────────────────────────────────

def customer_address_lines(customer: Any) -> list[str]:
    """
    Returns a list of address lines for the customer's primary billing address.
    Empty lines are excluded. Phone appears as the trailing line if present.
    Safe for None customer (returns []).
    """
    if customer is None:
        return []

    lines: list[str] = []

    for ln in (
        getattr(customer, "address_line1", None),
        getattr(customer, "address_line2", None),
    ):
        if ln and ln.strip():
            lines.append(ln.strip())

    city = (getattr(customer, "city", "") or "").strip()
    state = (getattr(customer, "state", "") or "").strip()
    zip_code = (getattr(customer, "zip_code", "") or "").strip()
    city_state = ", ".join(p for p in (city, state) if p)
    if city_state and zip_code:
        lines.append(f"{city_state} {zip_code}")
    elif city_state:
        lines.append(city_state)
    elif zip_code:
        lines.append(zip_code)

    phone = (getattr(customer, "phone", "") or "").strip()
    if phone:
        lines.append(phone)

    return lines


def vendor_address_lines(vendor: Any) -> list[str]:
    """
    Vendor records have no postal address fields — only contact name + phone
    + email. Returns whatever contact info is populated so vendor docs still
    have a usable "To" block.
    """
    if vendor is None:
        return []

    lines: list[str] = []
    contact = (getattr(vendor, "contact_name", "") or "").strip()
    phone = (getattr(vendor, "phone", "") or "").strip()
    email = (getattr(vendor, "email", "") or "").strip()
    if contact:
        lines.append(f"Attn: {contact}")
    if phone:
        lines.append(phone)
    if email:
        lines.append(email)
    return lines


# ── WeasyPrint render with browser-print fallback ─────────────────────────────

def render_pdf_or_fallback(
    *,
    request: Request,
    templates,
    template_name: str,
    context: dict[str, Any],
    fallback_print_url: str,
    download_filename: str,
):
    """
    Renders ``template_name`` to HTML, attempts WeasyPrint, returns the PDF
    bytes on success. On any failure (missing GTK on Windows, import error,
    rendering error) redirects to ``fallback_print_url`` so the browser
    print dialog can take over.

    Mirrors the pattern used by /invoices/{id}/pdf and /quotes/{id}/pdf.
    """
    html_str = templates.env.get_template(template_name).render(
        request=request, **context
    )
    try:
        from weasyprint import HTML  # type: ignore
        pdf_bytes = HTML(string=html_str, base_url=str(request.base_url)).write_pdf()
    except Exception:
        return RedirectResponse(fallback_print_url, status_code=302)

    safe_name = (download_filename or "document").replace("/", "-").replace("\\", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
