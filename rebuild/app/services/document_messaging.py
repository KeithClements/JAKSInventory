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


def default_email_body(db: Session, *, doc_label: str, number: str,
                       customer_name: str, total: float) -> str:
    name, phone = _company(db)
    lines = [
        f"Hi {customer_name or 'there'},",
        "",
        f"Please find your {doc_label.lower()} {number} attached"
        f" (total ${total:,.2f}).",
        "",
        "Let us know if you have any questions.",
        "",
        f"Thank you,",
        name + (f"  |  {phone}" if phone else ""),
    ]
    return "\n".join(lines)


def default_sms_body(db: Session, *, doc_label: str, number: str, total: float) -> str:
    name, phone = _company(db)
    tail = f" Reply here or call {phone}." if phone else " Reply here with any questions."
    return f"{name}: your {doc_label.lower()} {number} is ready — ${total:,.2f}.{tail}"


def is_log_only(db: Session) -> bool:
    return (get_setting_value_db(db, "messaging_log_only_mode", "true")
            or "true").strip().lower() == "true"


# ── Dialog context ───────────────────────────────────────────────────────────

def build_send_context(db: Session, *, doc_label: str, doc_number: str,
                       customer, total: float, action_url: str) -> dict:
    """Everything documents/_send_dialog.html needs, pre-filled + consent-aware."""
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
            total=total),
        "sms_body": default_sms_body(db, doc_label=doc_label, number=doc_number, total=total),
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
        return HTML(string=html_str, base_url=base_url).write_pdf()
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
