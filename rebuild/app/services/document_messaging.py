"""
app/services/document_messaging.py
==================================
§22 Function A — shared helpers for the "Send" dialog on quotes / sales orders /
invoices. ONE definition so all three documents send identically:

  • build_send_context(...)  → the dict the documents/_send_dialog.html partial reads
  • render_pdf_or_none(...)  → best-effort WeasyPrint PDF bytes (None if GTK/Pango
                               unavailable, e.g. on Windows — email then sends w/o
                               attachment, exactly like the existing /pdf routes)
  • perform_document_send(...) → run the per-channel sends + return a summary; one
                               temp-file lifecycle for the PDF attachment

Pure-ish: only reads settings. Routers own the doc-specific print context.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.constants import CommunicationChannel, CommunicationStatus
from app.services.messaging_service import (
    CommunicationBlockedError,
    MessagingService,
)
from app.settings_utils import get_setting_value_db

log = logging.getLogger(__name__)

_OK_STATUSES = {CommunicationStatus.SENT, CommunicationStatus.LOGGED_ONLY,
                CommunicationStatus.QUEUED}


# ── Defaults the rep can edit in the dialog ──────────────────────────────────

def _company(db: Session) -> tuple[str, str]:
    name = (get_setting_value_db(db, "company_name", "JAKS") or "JAKS").strip()
    phone = (get_setting_value_db(db, "company_phone", "") or "").strip()
    return name, phone


def default_email_subject(db: Session, doc_label: str, number: str) -> str:
    name, _ = _company(db)
    return f"{doc_label} {number} from {name}".strip()


def _fmt_qty(q) -> str:
    """2.0 → '2', 1.5 → '1.5' (no trailing .0 noise in the itemized list)."""
    try:
        f = float(q)
    except (TypeError, ValueError):
        return str(q)
    return str(int(f)) if f == int(f) else (f"{f:g}")


# Normalized line shape used for itemization: {"qty": float, "desc": str, "amount": float}

def itemize_lines(orm_lines, *, qty_attr="qty", desc_attr="description",
                  unit_attr="unit_price", include=None) -> list:
    """Normalize a document's ORM lines into the itemization shape. ``include`` is
    an optional predicate (e.g. quote: only included lines). Lines with a blank
    description are skipped so the message stays clean."""
    out = []
    for ln in orm_lines or []:
        if include is not None and not include(ln):
            continue
        desc = (getattr(ln, desc_attr, "") or "").strip()
        if not desc:
            continue
        qty = getattr(ln, qty_attr, 0) or 0
        unit = getattr(ln, unit_attr, 0) or 0
        try:
            amount = round(float(qty) * float(unit), 2)
        except (TypeError, ValueError):
            amount = 0.0
        out.append({"qty": qty, "desc": desc, "amount": amount})
    return out

def default_email_body(db: Session, *, doc_label: str, number: str,
                       customer_name: str, total: float, lines: list | None = None,
                       view_url: str | None = None) -> str:
    name, phone = _company(db)
    out = [
        f"Hi {customer_name or 'there'},",
        "",
        f"Please find your {doc_label.lower()} {number} attached"
        f" (total ${total:,.2f}).",
    ]
    if lines:
        out += ["", "Items:"]
        for ln in lines:
            out.append(f"  {_fmt_qty(ln['qty'])} x {ln['desc']} — ${ln['amount']:,.2f}")
        out.append(f"  {'Total':<6} ${total:,.2f}")
    if view_url:
        out += ["", f"View it online: {view_url}"]
    out += [
        "",
        "Let us know if you have any questions.",
        "",
        "Thank you,",
        name + (f"  |  {phone}" if phone else ""),
    ]
    return "\n".join(out)


def default_sms_body(db: Session, *, doc_label: str, number: str, total: float,
                     lines: list | None = None, max_items: int = 6,
                     view_url: str | None = None) -> str:
    name, phone = _company(db)
    out = [f"{name}: {doc_label} {number}"]
    if lines:
        for ln in lines[:max_items]:
            desc = (ln["desc"] or "")[:40]
            out.append(f"{_fmt_qty(ln['qty'])}x {desc} ${ln['amount']:,.2f}")
        if len(lines) > max_items:
            out.append(f"...(+{len(lines) - max_items} more)")
    tail = f"Total ${total:,.2f}." + (f" Reply or call {phone}." if phone else "")
    out.append(tail)
    if view_url:
        out.append(f"View: {view_url}")
    return "\n".join(out)


def is_log_only(db: Session) -> bool:
    return (get_setting_value_db(db, "messaging_log_only_mode", "true")
            or "true").strip().lower() == "true"


# ── Dialog context ───────────────────────────────────────────────────────────

def build_send_context(db: Session, *, doc_label: str, doc_number: str,
                       customer, total: float, action_url: str,
                       lines: list | None = None, view_url: str | None = None) -> dict:
    """Everything documents/_send_dialog.html needs, pre-filled + consent-aware.

    ``lines`` (optional) is a normalized list of {"qty","desc","amount"} dicts; when
    given, the default email + SMS bodies are itemized. ``view_url`` (optional) is a
    public signed link appended to both bodies (§22.7). The rep can still edit them.
    """
    cust_email = (getattr(customer, "email", "") or "").strip() if customer else ""
    cust_phone = (getattr(customer, "phone", "") or "").strip() if customer else ""
    allow_sms = bool(getattr(customer, "allow_sms", False)) if customer else False
    allow_email = bool(getattr(customer, "allow_email", True)) if customer else True
    do_not_contact = bool(getattr(customer, "do_not_contact", False)) if customer else False
    return {
        "action_url": action_url,
        "doc_label": doc_label,
        "doc_number": doc_number,
        "customer_id": getattr(customer, "id", None) if customer else None,
        "customer_name": (getattr(customer, "company_name", "") or
                          getattr(customer, "contact_name", "") or "") if customer else "",
        "to_email": cust_email,
        "to_phone": cust_phone,
        "allow_sms": allow_sms,
        "allow_email": allow_email,
        "do_not_contact": do_not_contact,
        "email_subject": default_email_subject(db, doc_label, doc_number),
        "email_body": default_email_body(
            db, doc_label=doc_label, number=doc_number,
            customer_name=(getattr(customer, "company_name", "") or
                           getattr(customer, "contact_name", "") if customer else ""),
            total=total, lines=lines, view_url=view_url),
        "sms_body": default_sms_body(db, doc_label=doc_label, number=doc_number,
                                     total=total, lines=lines, view_url=view_url),
        "log_only": is_log_only(db),
    }


# ── PDF (best-effort) ────────────────────────────────────────────────────────

def render_pdf_or_none(env, template_name: str, base_url: str, **ctx) -> bytes | None:
    """Render a print template to PDF bytes via WeasyPrint. Returns None when the
    WeasyPrint system libs are missing (Windows/GTK) — caller emails without the
    attachment, mirroring the existing /pdf route fallback."""
    try:
        html_str = env.get_template(template_name).render(**ctx)
        from weasyprint import HTML
        from app.services.document_render import static_url_fetcher
        return HTML(string=html_str, base_url=base_url,
                    url_fetcher=static_url_fetcher).write_pdf()
    except Exception as exc:  # ImportError / OSError (GTK) / template / render
        log.info("PDF render unavailable for %s: %s", template_name, exc)
        return None


# ── The send itself ──────────────────────────────────────────────────────────

def perform_document_send(
    db: Session,
    user_id: int,
    *,
    customer_id: int,
    channels: list[str],
    to_email: str,
    to_phone: str,
    email_subject: str,
    email_body: str,
    sms_body: str,
    pdf_bytes: bytes | None = None,
    pdf_filename: str = "document.pdf",
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
) -> dict:
    """Run the selected per-channel sends. Returns
    ``{"sent": [...], "failed": [...], "blocked": [...]}`` (channel-level; one
    blocked/failed channel never raises into the route). Manages the PDF temp file.
    """
    svc = MessagingService(db, current_user_id=user_id)
    out: dict[str, list] = {"sent": [], "failed": [], "blocked": []}
    tmp_path: Path | None = None
    try:
        want_email = "email" in channels and (to_email or "").strip()
        want_sms = "sms" in channels and (to_phone or "").strip()

        if want_email and pdf_bytes:
            tmp = tempfile.NamedTemporaryFile(
                prefix="axle_", suffix="_" + pdf_filename, delete=False)
            tmp.write(pdf_bytes)
            tmp.close()
            tmp_path = Path(tmp.name)

        if want_email:
            try:
                comm = svc.send_message(
                    customer_id=customer_id, channel=CommunicationChannel.EMAIL,
                    subject=email_subject, body=email_body,
                    attachments=[tmp_path] if tmp_path else None,
                    override_address=(to_email or "").strip(),
                    related_entity_type=related_entity_type,
                    related_entity_id=related_entity_id,
                )
                bucket = "sent" if comm.status in _OK_STATUSES else "failed"
                out[bucket].append({"channel": "email", "status": comm.status,
                                    "error": comm.failed_reason})
            except CommunicationBlockedError as exc:
                out["blocked"].append({"channel": "email", "error": str(exc)})

        if want_sms:
            try:
                comm = svc.send_message(
                    customer_id=customer_id, channel=CommunicationChannel.SMS,
                    body=sms_body, override_address=(to_phone or "").strip(),
                    related_entity_type=related_entity_type,
                    related_entity_id=related_entity_id,
                )
                bucket = "sent" if comm.status in _OK_STATUSES else "failed"
                out[bucket].append({"channel": "sms", "status": comm.status,
                                    "error": comm.failed_reason})
            except CommunicationBlockedError as exc:
                out["blocked"].append({"channel": "sms", "error": str(exc)})
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return out
