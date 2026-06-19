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

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.settings_utils import get_setting_value_db


# ── WeasyPrint local-asset fetcher ────────────────────────────────────────────
# Absolute path to the mounted /static directory (mirrors the StaticFiles mount
# in app/main.py: BASE_DIR / "static", where BASE_DIR is the app package dir).
_STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"

# WeasyPrint's guesser doesn't know modern web fonts; pin the ones we self-host.
_MIME_OVERRIDES = {".woff2": "font/woff2", ".woff": "font/woff", ".css": "text/css"}


def _local_static_path(url: str) -> Path | None:
    """Map a ``/static/…``-pointing URL to its file under ``app/static``.

    Returns ``None`` for anything that is not our own static mount (external CDN
    images, ``data:`` URIs, fonts.googleapis.com, …). Resolves the candidate and
    confirms it stays inside the static root so a crafted ``../`` ``src`` cannot
    escape onto the wider filesystem.
    """
    try:
        path = unquote(urlsplit(url).path or "")
    except ValueError:
        return None
    # base_url makes a relative src like "/static/uploads/logo.png" absolute
    # ("http://host/static/uploads/logo.png"); we match on the path either way.
    if not path.startswith("/static/"):
        return None
    candidate = (_STATIC_ROOT / path[len("/static/"):]).resolve()
    try:
        candidate.relative_to(_STATIC_ROOT.resolve())
    except ValueError:
        return None                                   # path traversal → refuse
    return candidate if candidate.is_file() else None


def static_url_fetcher(url, *args, **kwargs):
    """WeasyPrint ``url_fetcher`` that serves the app's own ``/static`` assets
    straight from disk.

    Without this, WeasyPrint resolves ``/static/…`` srcs to an HTTP request back
    to the same single-process uvicorn server — which is blocked synchronously
    inside ``write_pdf()`` on the event loop — so those requests hang and the
    logo plus any locally-uploaded product thumbnail silently drop from the PDF.
    External URLs (e.g. the PAI image CDN) fall through to WeasyPrint's default
    fetcher and are retrieved normally over the network.
    """
    local = _local_static_path(url)
    if local is not None:
        mime = _MIME_OVERRIDES.get(local.suffix.lower()) or \
            mimetypes.guess_type(str(local))[0] or "application/octet-stream"
        return {"string": local.read_bytes(), "mime_type": mime,
                "redirected_url": url}
    from weasyprint import default_url_fetcher  # type: ignore
    return default_url_fetcher(url, *args, **kwargs)


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


def get_prepared_by(db: Session, user_id: int | None) -> str:
    """Resolve the current user's display name for print headers ('Prepared by').
    Returns a non-empty string — falls back to 'JAKS' when user is unknown."""
    if user_id is None:
        return "JAKS"
    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:                       # id with no matching row → never crash
        return "JAKS"
    return (user.name or "").strip() or "JAKS"


def customer_has_active_price_rules(db: Session, customer: Any) -> bool:
    """True when ``customer`` has at least one ACTIVE ``CustomerPriceRule``.

    Cheap existence check (single ``SELECT 1 … LIMIT 1``) used to gate the
    optional "Your account price" print label. Date windows and qty breaks are
    intentionally NOT evaluated here — the label only asserts that an account
    pricing agreement exists, not that a given line matched a rule today. Any
    active rule (even a future-dated one) is a real, honoured agreement.

    None-safe: a missing customer or a customer with no id → False.
    """
    cust_id = getattr(customer, "id", None)
    if cust_id is None:
        return False
    from app.models.pricing import CustomerPriceRule
    return (
        db.query(CustomerPriceRule.id)
        .filter(
            CustomerPriceRule.customer_id == cust_id,
            CustomerPriceRule.is_active == True,  # noqa: E712
        )
        .first()
        is not None
    )


def get_company_dict(db: Session) -> dict[str, Any]:
    """Standard company block used across every document template.

    §5.12 branding fields are EMPTY-SAFE: when no logo is set, ``logo_url`` is
    ``None`` so templates fall back to the text header (today's behavior — no
    regression). ``company_logo_path`` is stored relative to ``static/`` (e.g.
    ``uploads/logo_xyz.png``) by the upload route; it resolves to a ``/static``
    URL here. ``show_logo`` lets the owner suppress the logo without deleting it.

    CUSTOMER_PRICING_DESIGN.md §5 — the optional "Your account price" print
    label is surfaced two ways, both default-silent:
      • ``show_account_price_label`` — the raw setting
        (``pricing_show_account_price_label``, default "false").
      • ``account_price_label(customer)`` — a bound callable templates use to
        gate the label per-document. Returns True only when the setting is ON
        **and** that document's customer has ≥1 active CustomerPriceRule. When
        the setting is OFF it short-circuits to False without any query, so the
        printed output is byte-for-byte unchanged (default silent).
    """
    show_account_price_label = (
        get_setting_value_db(db, "pricing_show_account_price_label", "false")
        or "false"
    ).strip().lower() == "true"

    def _account_price_label(customer: Any) -> bool:
        if not show_account_price_label:
            return False
        return customer_has_active_price_rules(db, customer)

    logo_path = (get_setting_value_db(db, "company_logo_path", "") or "").strip()
    show_logo = (get_setting_value_db(db, "document_show_logo", "true") or "true").strip().lower() == "true"
    logo_url = f"/static/{logo_path.lstrip('/')}" if logo_path else None
    # Owner-adjustable logo height (px), uniform across every document header.
    # Parse defensively: a blank/non-numeric/out-of-range value falls back to the
    # 56px default so a document never renders broken CSS.
    raw_logo_h = (get_setting_value_db(db, "document_logo_height", "56") or "56").strip()
    try:
        logo_height = max(24, min(160, int(float(raw_logo_h))))
    except (TypeError, ValueError):
        logo_height = 56
    return {
        "name":        get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address":     get_setting_value_db(db, "company_address", ""),
        "phone":       format_phone(get_setting_value_db(db, "company_phone", "")),
        "email":       get_setting_value_db(db, "company_email",   ""),
        # §5.12 branding
        "logo_url":    logo_url,
        "show_logo":   show_logo,
        "logo_height": logo_height,
        "footer_text": get_setting_value_db(db, "document_footer_text", ""),
        "terms_text":  get_setting_value_db(db, "document_terms_text", ""),
        # CUSTOMER_PRICING_DESIGN.md §5 — optional "Your account price" label
        "show_account_price_label": show_account_price_label,
        "account_price_label":      _account_price_label,
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
        pdf_bytes = HTML(
            string=html_str, base_url=str(request.base_url),
            url_fetcher=static_url_fetcher,
        ).write_pdf()
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
