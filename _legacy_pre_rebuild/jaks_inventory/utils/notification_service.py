"""Notification service for sending invoices and tracking info via SMS and email.

Supports:
- Sending invoice summaries via SMS / email
- Sending tracking numbers via SMS / email
- SMTP configuration via app_settings
"""

from __future__ import annotations

import logging
import smtplib
from html import escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from db import get_customer, get_setting

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  SMS helpers (delegate to sms_service)
# ---------------------------------------------------------------------------
def _sms_configured() -> bool:
    from .sms_service import is_configured
    return is_configured()


def _send_sms(phone: str, body: str) -> dict:
    """Send a raw SMS message. Returns {success, twilio_sid, error}."""
    from .sms_service import is_configured, normalise_phone, _get_client, _phone

    if not is_configured():
        return {"success": False, "error": "Twilio not configured"}

    to_number = normalise_phone(phone)
    try:
        client = _get_client()
        message = client.messages.create(
            body=body,
            from_=_phone(),
            to=to_number,
        )
        log.info("SMS sent to %s  SID=%s", to_number, message.sid)
        return {"success": True, "twilio_sid": message.sid}
    except Exception as exc:
        log.error("SMS send failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
#  Email helpers
# ---------------------------------------------------------------------------
def email_configured() -> bool:
    """Return True if SMTP settings are present in app_settings."""
    host = get_setting("smtp_host", "")
    user = get_setting("smtp_user", "")
    return bool(host and user)


def _friendly_smtp_error(host: str, exc: Exception) -> str:
    """Translate common SMTP failures into actionable messages."""
    msg = str(exc)
    lower = msg.lower()

    # Gmail typically returns 535 5.7.8 when a normal account password is used
    # instead of an app password.
    if isinstance(exc, smtplib.SMTPAuthenticationError) or "5.7.8" in lower or "badcredentials" in lower:
        if "gmail" in (host or "").lower():
            return (
                "Gmail login failed (535 5.7.8). Use a Google App Password, not your normal Gmail password.\n\n"
                "Steps:\n"
                "1) In your Google account, enable 2-Step Verification\n"
                "2) Create an App Password for Mail\n"
                "3) In Settings -> Email/SMTP:\n"
                "   - Host: smtp.gmail.com\n"
                "   - Port: 587\n"
                "   - TLS: enabled\n"
                "   - Username: full Gmail address\n"
                "   - Password: 16-character App Password"
            )
        return "SMTP authentication failed. Verify username/password and SMTP provider security settings."

    if isinstance(exc, smtplib.SMTPConnectError):
        return f"Could not connect to SMTP server '{host}'. Verify host/port and network access."

    if isinstance(exc, TimeoutError):
        return "SMTP connection timed out. Verify host/port and firewall settings."

    return msg


def _send_email(to_addr: str, subject: str, html_body: str) -> dict:
    """Send an HTML email via SMTP settings from app_settings."""
    return _send_email_with_attachments(to_addr, subject, html_body, attachments=None)


def _send_email_with_attachments(
    to_addr: str,
    subject: str,
    html_body: str,
    attachments: list | None = None,
    cc: list | None = None,
) -> dict:
    """Send an HTML email via SMTP, optionally with file attachments.

    attachments: list of file paths (str/Path).
    cc: list of CC addresses.
    """
    from pathlib import Path
    host = get_setting("smtp_host", "")
    port = int(get_setting("smtp_port", "587"))
    user = get_setting("smtp_user", "")
    password = get_setting("smtp_password", "")
    from_addr = get_setting("smtp_from", user)
    use_tls = get_setting("smtp_tls", "1") == "1"

    if not host or not user:
        return {"success": False, "error": "SMTP not configured. Set smtp_host and smtp_user in Settings."}
    if not password:
        return {"success": False, "error": "SMTP password is empty. Set smtp_password in Settings."}

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    if cc:
        msg["Cc"] = ", ".join(cc)

    body = MIMEMultipart("alternative")
    body.attach(MIMEText(html_body, "html"))
    msg.attach(body)

    for path in (attachments or []):
        try:
            p = Path(path)
            with open(p, "rb") as fh:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(fh.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{p.name}"')
            msg.attach(part)
        except Exception as exc:
            log.warning("Failed to attach %s: %s", path, exc)

    recipients = [to_addr] + (cc or [])
    try:
        if use_tls:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP(host, port, timeout=30)

        if password:
            server.login(user, password)
        server.sendmail(from_addr, recipients, msg.as_string())
        server.quit()
        log.info("Email sent to %s  subject=%s  attachments=%d", to_addr, subject, len(attachments or []))
        return {"success": True}
    except Exception as exc:
        friendly = _friendly_smtp_error(host, exc)
        log.error("Email send failed: %s", friendly)
        return {"success": False, "error": friendly}


# ---------------------------------------------------------------------------
#  Purchase Order email
# ---------------------------------------------------------------------------
def send_po_to_vendor(
    po_id: int,
    recipient_email: str | None = None,
    extra_message: str | None = None,
) -> dict:
    """Email a Purchase Order PDF to the vendor.

    Recipient defaults to vendor's contact_email. extra_message is appended to
    the PO's vendor_message in the email body. Returns the email send result.
    """
    from db import get_purchase_order, get_vendor
    from .pdf_purchase_order import generate_purchase_order_pdf

    po = get_purchase_order(po_id)
    if not po:
        return {"success": False, "error": f"PO {po_id} not found"}

    vendor_id = po.get("vendor_id")
    vendor = get_vendor(vendor_id) if vendor_id else None
    to_addr = (recipient_email or (vendor or {}).get("contact_email") or "").strip()
    if not to_addr:
        return {"success": False, "error": "Vendor has no contact_email and no recipient was supplied."}

    try:
        pdf_path = generate_purchase_order_pdf(po)
    except Exception as exc:
        return {"success": False, "error": f"Failed to generate PO PDF: {exc}"}

    company = get_setting("company_name", "JAK'S Diesel")
    po_num = po.get("po_number", "")
    vendor_name = (vendor or {}).get("name") or po.get("vendor_name") or ""
    vendor_message = (po.get("vendor_message") or "").strip()
    notes_blob = "\n".join(x for x in (vendor_message, (extra_message or "").strip()) if x)

    subject = f"Purchase Order {po_num} from {company}"
    html_lines = [
        f"<p>Hello {escape(vendor_name) or 'Vendor'},</p>",
        f"<p>Please find attached Purchase Order <b>{escape(str(po_num))}</b> from {escape(company)}.</p>",
    ]
    if notes_blob:
        safe = escape(notes_blob).replace("\n", "<br>")
        html_lines.append(f"<p><b>Message from {escape(company)}:</b><br>{safe}</p>")
    html_lines.append(
        f"<p>Order date: {escape(str(po.get('order_date') or '')[:10])}<br>"
        f"Expected: {escape(str(po.get('expected_date') or '')[:10])}<br>"
        f"Total: ${float(po.get('total') or 0):,.2f}</p>"
    )
    html_lines.append("<p>Thank you,<br>" + escape(company) + "</p>")
    html_body = "\n".join(html_lines)

    return _send_email_with_attachments(to_addr, subject, html_body, attachments=[pdf_path])


# ---------------------------------------------------------------------------
#  Sales Order email (customer-facing)
# ---------------------------------------------------------------------------
def send_sales_order_email(so_id: int, recipient_email: str | None = None) -> dict:
    """Email a Sales Order PDF to the customer.

    Recipient defaults to the customer's email on file. Returns the standard
    ``{"success": bool, "error": str?}`` shape from the email helper.
    """
    from db import get_sales_order, get_customer
    from .pdf_sales_order import generate_sales_order_pdf

    so = get_sales_order(so_id)
    if not so:
        return {"success": False, "error": f"SO {so_id} not found"}

    cust_id = so.get("customer_id")
    cust = get_customer(cust_id) if cust_id else None
    to_addr = (recipient_email or (cust or {}).get("email") or "").strip()
    if not to_addr:
        return {"success": False, "error": "Customer has no email and no recipient was supplied."}

    try:
        pdf_path = generate_sales_order_pdf(so)
    except Exception as exc:
        return {"success": False, "error": f"Failed to generate SO PDF: {exc}"}

    company = get_setting("company_name", "JAK'S Diesel")
    so_num = so.get("so_number", "")
    cust_name = (cust or {}).get("name") or so.get("customer_name") or ""
    customer_notes = (so.get("customer_notes") or "").strip()
    tracking = (so.get("tracking_number") or "").strip()

    subject = f"Sales Order {so_num} from {company}"
    html_lines = [
        f"<p>Hello {escape(cust_name) or 'Customer'},</p>",
        f"<p>Please find attached Sales Order <b>{escape(str(so_num))}</b> from {escape(company)}.</p>",
    ]
    if tracking:
        html_lines.append(f"<p><b>Tracking #:</b> {escape(tracking)}</p>")
    if customer_notes:
        safe = escape(customer_notes).replace("\n", "<br>")
        html_lines.append(f"<p><b>Notes:</b><br>{safe}</p>")
    html_lines.append(
        f"<p>Total: ${float(so.get('total') or 0):,.2f}</p>"
    )
    html_lines.append("<p>Thank you,<br>" + escape(company) + "</p>")
    html_body = "\n".join(html_lines)

    return _send_email_with_attachments(to_addr, subject, html_body, attachments=[pdf_path])


# ---------------------------------------------------------------------------
#  Invoice email (customer-facing)
# ---------------------------------------------------------------------------
def send_invoice_email(invoice_id: int, recipient_email: str | None = None) -> dict:
    """Email an invoice PDF to the customer."""
    from db import get_invoice, get_customer
    from .pdf_invoice import generate_invoice_pdf

    inv = get_invoice(invoice_id)
    if not inv:
        return {"success": False, "error": f"Invoice {invoice_id} not found"}

    cust_id = inv.get("customer_id")
    cust = get_customer(cust_id) if cust_id else None
    to_addr = (recipient_email or (cust or {}).get("email") or "").strip()
    if not to_addr:
        return {"success": False, "error": "Customer has no email and no recipient was supplied."}

    try:
        pdf_path = generate_invoice_pdf(inv)
    except Exception as exc:
        return {"success": False, "error": f"Failed to generate invoice PDF: {exc}"}

    # If this invoice has any materialized warranty certificates, attach the
    # combined certificate PDF alongside the invoice. This is the
    # auto-attach path; reprint flows must NOT trigger cert generation.
    attachments = [pdf_path]
    try:
        from db import list_warranties_for_invoice
        if list_warranties_for_invoice(int(invoice_id)):
            from .pdf_warranty_certificate import (
                generate_warranty_certificates_for_invoice,
            )
            cert_pdf = generate_warranty_certificates_for_invoice(int(invoice_id))
            attachments.append(cert_pdf)
    except Exception as exc:
        log.warning("Warranty cert attach skipped for invoice %s: %s", invoice_id, exc)

    # UAT 1.7b — attach the Core Return slip when the invoice carries
    # core charges so the customer has paperwork to bring the core back.
    try:
        from db import invoice_has_cores, get_core_returns_for_invoice
        if invoice_has_cores(int(invoice_id)):
            from .pdf_core_receipt import (
                generate_core_receipt_pdf,
                generate_core_slip_for_invoice_preview,
            )
            cores = get_core_returns_for_invoice(int(invoice_id)) or []
            try:
                if cores:
                    core_pdf = generate_core_receipt_pdf(int(invoice_id))
                else:
                    core_pdf = generate_core_slip_for_invoice_preview(int(invoice_id))
                if core_pdf:
                    attachments.append(core_pdf)
            except Exception as exc:
                log.warning(
                    "Core slip attach skipped for invoice %s: %s", invoice_id, exc
                )
    except Exception as exc:
        log.warning("Core slip lookup failed for invoice %s: %s", invoice_id, exc)

    company = get_setting("company_name", "JAK'S Diesel")
    inv_num = inv.get("invoice_number", "")
    cust_name = (cust or {}).get("name") or inv.get("customer_name") or ""
    customer_notes = (inv.get("customer_notes") or "").strip()

    subject = f"Invoice {inv_num} from {company}"
    html_lines = [
        f"<p>Hello {escape(cust_name) or 'Customer'},</p>",
        f"<p>Please find attached Invoice <b>{escape(str(inv_num))}</b> from {escape(company)}.</p>",
        f"<p>Total: ${float(inv.get('total') or 0):,.2f}<br>"
        f"Balance Due: ${float(inv.get('balance_due') or 0):,.2f}</p>",
    ]
    if customer_notes:
        safe = escape(customer_notes).replace("\n", "<br>")
        html_lines.append(f"<p><b>Notes:</b><br>{safe}</p>")
    html_lines.append("<p>Thank you,<br>" + escape(company) + "</p>")
    html_body = "\n".join(html_lines)

    return _send_email_with_attachments(to_addr, subject, html_body, attachments=attachments)


# ---------------------------------------------------------------------------
#  Invoice notification
# ---------------------------------------------------------------------------
def _build_invoice_sms(invoice: dict) -> str:
    """Build SMS body for an invoice notification."""
    inv_num = invoice.get("invoice_number", "")
    total = float(invoice.get("total") or 0)
    balance = float(invoice.get("balance_due") or 0)
    due = str(invoice.get("due_date", ""))[:10]

    parts = [f"JAK'S Invoice {inv_num}"]
    parts.append(f"Total: ${total:,.2f}")
    if balance and balance != total:
        parts.append(f"Balance due: ${balance:,.2f}")
    if due:
        parts.append(f"Due: {due}")
    if any(float(ln.get("core_charge") or 0) > 0 for ln in invoice.get("lines", [])):
        parts.append("Core paperwork required for refundable core deposit.")
    parts.append("")
    parts.append("Thank you for your business!")
    return "\n".join(parts)


def _build_invoice_email(invoice: dict) -> str:
    """Build HTML email body for an invoice notification."""
    inv_num = invoice.get("invoice_number", "")
    total = float(invoice.get("total") or 0)
    balance = float(invoice.get("balance_due") or 0)
    due = str(invoice.get("due_date", ""))[:10]
    notes = invoice.get("notes", "") or ""

    lines_html = ""
    for ln in invoice.get("lines", []):
        sku = str(ln.get("sku", "") or "")
        desc = escape(str(ln.get("description", "") or ""))
        qty = int(ln.get("quantity", 1) or 1)
        price = float(ln.get("unit_price", 0) or 0)
        line_total = float(ln.get("line_total", 0) or 0)
        core_each = float(ln.get("core_charge", 0) or 0)
        core_total = core_each * qty
        lines_html += (
            f"<tr><td>{escape(sku)}</td><td>{desc}</td>"
            f"<td style='text-align:center'>{qty}</td>"
            f"<td style='text-align:right'>${price:,.2f}</td>"
            f"<td style='text-align:right'>${line_total:,.2f}</td></tr>"
        )
        if core_total > 0 and not sku.upper().startswith(("CORE-", "RTN-")):
            lines_html += (
                "<tr>"
                "<td></td>"
                "<td style='color:#6b7280;font-size:9pt;'><em>Core Deposit - refundable on accepted core return</em></td>"
                f"<td style='text-align:center;color:#6b7280;font-size:9pt;'>{qty}</td>"
                f"<td style='text-align:right;color:#6b7280;font-size:9pt;'>${core_each:,.2f}</td>"
                f"<td style='text-align:right;color:#6b7280;font-size:9pt;'>${core_total:,.2f}</td>"
                "</tr>"
            )

    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px;">
        <h2 style="color: #3d4a38;">JAK'S Invoice {inv_num}</h2>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <thead>
                <tr style="background: #3d4a38; color: white;">
                    <th style="padding: 8px; text-align: left;">SKU</th>
                    <th style="padding: 8px; text-align: left;">Description</th>
                    <th style="padding: 8px; text-align: center;">Qty</th>
                    <th style="padding: 8px; text-align: right;">Price</th>
                    <th style="padding: 8px; text-align: right;">Total</th>
                </tr>
            </thead>
            <tbody>{lines_html}</tbody>
        </table>
        <p style="font-size: 14pt; font-weight: bold;">
            Total: ${total:,.2f}
            {"&nbsp;&nbsp;|&nbsp;&nbsp;Balance Due: $" + f"{balance:,.2f}" if balance and balance != total else ""}
        </p>
        {"<p>Due: " + due + "</p>" if due else ""}
        {"<p><em>" + notes + "</em></p>" if notes else ""}
        <hr>
        <p style="color: gray; font-size: 9pt;">Thank you for your business! — JAK'S</p>
    </div>
    """


def send_invoice_sms(invoice: dict, phone: str) -> dict:
    """Send invoice summary via SMS."""
    body = _build_invoice_sms(invoice)
    return _send_sms(phone, body)


def send_invoice_email(invoice: dict, email: str) -> dict:
    """Send invoice via email."""
    inv_num = invoice.get("invoice_number", "")
    subject = f"JAK'S Invoice {inv_num}"
    html = _build_invoice_email(invoice)
    return _send_email(email, subject, html)


# ---------------------------------------------------------------------------
#  Tracking number notification
# ---------------------------------------------------------------------------
def _build_tracking_sms(invoice: dict) -> str:
    """Build SMS for tracking number notification."""
    inv_num = invoice.get("invoice_number", "")
    tracking = invoice.get("tracking_number", "")

    parts = [f"JAK'S — Your order {inv_num} has shipped!"]
    parts.append(f"Tracking #: {tracking}")
    parts.append("")
    parts.append("Thank you for your business!")
    return "\n".join(parts)


def _build_tracking_email(invoice: dict) -> str:
    """Build HTML email for tracking notification."""
    inv_num = invoice.get("invoice_number", "")
    tracking = invoice.get("tracking_number", "")
    ship_date = str(invoice.get("ship_date", ""))[:10]

    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px;">
        <h2 style="color: #3d4a38;">Your Order Has Shipped!</h2>
        <p>Invoice: <strong>{inv_num}</strong></p>
        {"<p>Ship Date: " + ship_date + "</p>" if ship_date else ""}
        <p style="font-size: 14pt;">
            Tracking Number: <strong>{tracking}</strong>
        </p>
        <hr>
        <p style="color: gray; font-size: 9pt;">Thank you for your business! — JAK'S</p>
    </div>
    """


def send_tracking_sms(invoice: dict, phone: str) -> dict:
    """Send tracking number via SMS."""
    tracking = invoice.get("tracking_number", "")
    if not tracking:
        return {"success": False, "error": "No tracking number on this invoice."}
    body = _build_tracking_sms(invoice)
    return _send_sms(phone, body)


def send_tracking_email(invoice: dict, email: str) -> dict:
    """Send tracking number via email."""
    tracking = invoice.get("tracking_number", "")
    if not tracking:
        return {"success": False, "error": "No tracking number on this invoice."}
    inv_num = invoice.get("invoice_number", "")
    subject = f"JAK'S — Shipping Confirmation for {inv_num}"
    html = _build_tracking_email(invoice)
    return _send_email(email, subject, html)


# ---------------------------------------------------------------------------
#  Convenience: auto-detect customer contact and channel
# ---------------------------------------------------------------------------
def get_customer_contact(customer_id: int) -> dict:
    """Return {phone, email, name} for a customer."""
    c = get_customer(customer_id)
    if not c:
        return {"phone": "", "email": "", "name": ""}
    return {
        "phone": c.get("phone", "") or "",
        "email": c.get("email", "") or "",
        "name": c.get("name", "") or "",
    }


# ---------------------------------------------------------------------------
#  Quote notification
# ---------------------------------------------------------------------------
def _build_quote_email(quote: dict) -> str:
    """Build HTML email body for a quote."""
    from engine.warranty import format_warranty_long
    from db import get_setting

    show_warranty = get_setting("quote_show_warranty", "1") == "1"

    q_num = quote.get("quote_number", "")
    customer_name = quote.get("customer_name", "Customer")
    total = float(quote.get("total", 0) or 0)
    core = float(quote.get("core_total", 0) or 0)
    expires = str(quote.get("expiration_date", ""))[:10]
    notes = quote.get("notes", "") or ""
    esn = quote.get("esn", "") or ""
    vin = quote.get("vin", "") or ""

    def _line_warranty_text(ln: dict) -> str:
        if not ln.get("is_optional") and not ln.get("parent_line_id"):
            try:
                from engine.warranty import format_standard_warranty, supplier_standard_warranty_for_product
                std_months, _, _ = supplier_standard_warranty_for_product(ln)
                std_text = format_standard_warranty(std_months)
            except Exception:
                std_text = ""
            if std_text:
                return std_text

        w_months = int(ln.get("warranty_months") or 0)
        w_type = ln.get("warranty_type") or "parts_only"
        w_mileage = ln.get("warranty_mileage_limit")
        w_years = int(ln.get("warranty_years") or 0)
        w_price = float(ln.get("warranty_price") or 0)

        if w_months <= 0:
            jaks_enabled = bool(ln.get("jaks_warranty_enabled"))
            jaks_value = ln.get("jaks_warranty_value")
            jaks_unit = (ln.get("jaks_warranty_unit") or "").lower().strip()
            sup_enabled = bool(ln.get("supplier_warranty_enabled"))
            sup_value = ln.get("supplier_warranty_value")
            sup_unit = (ln.get("supplier_warranty_unit") or "").lower().strip()

            legacy_value = None
            legacy_unit = "months"
            if jaks_enabled and jaks_value:
                legacy_value = jaks_value
                legacy_unit = jaks_unit or "months"
                w_type = "parts_labor"
            elif sup_enabled and sup_value:
                legacy_value = sup_value
                legacy_unit = sup_unit or "months"
                w_type = "parts_only"

            if legacy_value:
                try:
                    legacy_num = float(legacy_value)
                    if legacy_unit.startswith("year"):
                        w_months = int(round(legacy_num * 12))
                    elif legacy_unit.startswith("day"):
                        w_months = int(round(legacy_num / 30.0))
                    else:
                        w_months = int(round(legacy_num))
                except (TypeError, ValueError):
                    w_months = 0

        if w_months > 0:
            return format_warranty_long(w_months, w_type, w_mileage)
        if w_years > 0:
            return f"+{w_years}yr ${w_price:,.0f}"
        return "-"

    lines_html = ""
    selected_lines = [ln for ln in quote.get("lines", []) if ln.get("is_selected", True)]
    required = [ln for ln in selected_lines if not ln.get("is_optional")]
    optional = [ln for ln in selected_lines if ln.get("is_optional")]

    def _append_section(label: str, items: list[dict], tone: str = "#3d4a38") -> str:
        html = ""
        if not items:
            return html
        html += (
            f"<tr><td colspan='6' style='padding:7px 8px;background:#f7f7f3;"
            f"font-weight:bold;color:{tone};border-top:1px solid #eee;'>"
            f"{escape(label)}</td></tr>"
        )
        for ln in items:
            desc = escape(str(ln.get("description", "") or ln.get("title", "")))
            qty = int(ln.get("qty", 1) or 1)
            price = float(ln.get("unit_price", 0) or 0)
            line_ship = float(ln.get("line_shipping", 0) or 0)
            line_total = float(ln.get("line_total", qty * price) or 0) + line_ship
            core_ch = float(ln.get("core_charge", 0) or 0)
            warranty_text = escape(_line_warranty_text(ln)) if show_warranty else ""
            sku = escape(str(ln.get("sku", "")))
            if warranty_text and "Standard Warranty" in warranty_text:
                desc += (
                    f"<br><span style='color:#2e7d32;font-size:9pt;font-weight:bold;'>"
                    f"{warranty_text}</span>"
                )

            html += (
                f"<tr><td style='padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top'>{sku}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top'>{desc}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:center;vertical-align:top'>{qty}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right;vertical-align:top'>${price:,.2f}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:center;vertical-align:top'>{'' if 'Standard Warranty' in warranty_text else warranty_text}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right;vertical-align:top'>${line_total:,.2f}</td></tr>"
            )
            if core_ch > 0:
                html += (
                    f"<tr><td></td><td style='padding:2px 8px;color:#888;font-size:9pt;' colspan='4'>"
                    f"Core charge</td><td style='padding:2px 8px;text-align:right;color:#888;font-size:9pt;'>"
                    f"${core_ch:,.2f}</td></tr>"
                )
        return html

    lines_html += _append_section("Option A - Requested Parts", required)
    lines_html += _append_section("Option B - Recommended Upgrades", optional, tone="#2e7d32")

    meta_rows = ""
    if esn:
        meta_rows += f"<p><strong>ESN:</strong> {esn}</p>"
    if vin:
        meta_rows += f"<p><strong>VIN:</strong> {vin}</p>"

    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 640px; margin: auto;">
        <div style="background: #3d4a38; color: white; padding: 16px 20px; border-radius: 6px 6px 0 0;">
            <h2 style="margin:0;">JAK'S — Quote {q_num}</h2>
        </div>
        <div style="padding: 16px 20px; border: 1px solid #ddd; border-top: none;">
            <p>Hi {customer_name},</p>
            <p>Thank you for your interest. Here is your quote:</p>
            {meta_rows}
            <table style="width:100%; border-collapse:collapse; margin:16px 0;">
                <thead>
                    <tr style="background:#f5f5f5;">
                        <th style="padding:8px; text-align:left;">SKU</th>
                        <th style="padding:8px; text-align:left;">Description</th>
                        <th style="padding:8px; text-align:center;">Qty</th>
                        <th style="padding:8px; text-align:right;">Price</th>
                        <th style="padding:8px; text-align:center;">Warranty</th>
                        <th style="padding:8px; text-align:right;">Total</th>
                    </tr>
                </thead>
                <tbody>{lines_html}</tbody>
            </table>
            <table style="width:100%; margin-top:8px;">
                {"<tr><td style='text-align:right;padding:4px 8px;'>Refundable Core Deposit:</td><td style='text-align:right;padding:4px 8px;font-weight:bold;'>$" + f"{core:,.2f}" + "</td></tr>" if core > 0 else ""}
                <tr><td style="text-align:right;padding:4px 8px;font-size:14pt;">Total:</td>
                    <td style="text-align:right;padding:4px 8px;font-size:14pt;font-weight:bold;">${total:,.2f}</td></tr>
            </table>
            {"<p>Valid until: <strong>" + expires + "</strong></p>" if expires else ""}
            {"<p><em>" + notes + "</em></p>" if notes else ""}
            <p>Please reply to this email or call us to accept this quote.</p>
        </div>
        <div style="padding:12px 20px; background:#f9f9f9; border: 1px solid #ddd; border-top:none; border-radius: 0 0 6px 6px;">
            <p style="color:gray; font-size:9pt; margin:0;">Thank you for your business! — JAK'S</p>
        </div>
    </div>
    """


def send_quote_email(quote: dict, email: str) -> dict:
    """Send a quote via email."""
    q_num = quote.get("quote_number", "")
    subject = f"JAK'S Quote {q_num}"
    html = _build_quote_email(quote)
    return _send_email(email, subject, html)


# ---------------------------------------------------------------------------
#  Sales Order notification
# ---------------------------------------------------------------------------
def _build_so_email(so: dict, lines: list) -> str:
    """Build HTML email body for a sales order confirmation."""
    so_num = so.get("so_number", "")
    customer_name = so.get("customer_name", "Customer")
    status = (so.get("status", "") or "").replace("_", " ").title()
    total = float(so.get("total", 0) or 0)
    promised = str(so.get("promised_date", ""))[:10]
    tracking = so.get("tracking_number", "") or ""
    notes = so.get("notes", "") or ""

    lines_html = ""
    for ln in lines:
        desc = ln.get("description", "") or ln.get("product_title", "")
        qty = int(ln.get("quantity", 1) or 1)
        price = float(ln.get("unit_price", 0) or 0)
        line_total = float(ln.get("line_total", qty * price) or 0)
        lines_html += (
            f"<tr><td style='padding:6px 8px;border-bottom:1px solid #eee;'>{ln.get('sku', '')}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee;'>{desc}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:center'>{qty}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>${price:,.2f}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>${line_total:,.2f}</td></tr>"
        )

    tracking_section = ""
    if tracking:
        tracking_section = f"""
        <div style="background:#e8f5e9; padding:10px 16px; border-radius:4px; margin:12px 0;">
            <strong>Tracking Number:</strong> {tracking}
        </div>
        """

    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 640px; margin: auto;">
        <div style="background: #3d4a38; color: white; padding: 16px 20px; border-radius: 6px 6px 0 0;">
            <h2 style="margin:0;">JAK'S — Sales Order {so_num}</h2>
        </div>
        <div style="padding: 16px 20px; border: 1px solid #ddd; border-top: none;">
            <p>Hi {customer_name},</p>
            <p>Here is your order confirmation. <strong>Status: {status}</strong></p>
            {"<p>Promised Date: <strong>" + promised + "</strong></p>" if promised else ""}
            {tracking_section}
            <table style="width:100%; border-collapse:collapse; margin:16px 0;">
                <thead>
                    <tr style="background:#f5f5f5;">
                        <th style="padding:8px; text-align:left;">SKU</th>
                        <th style="padding:8px; text-align:left;">Description</th>
                        <th style="padding:8px; text-align:center;">Qty</th>
                        <th style="padding:8px; text-align:right;">Price</th>
                        <th style="padding:8px; text-align:right;">Total</th>
                    </tr>
                </thead>
                <tbody>{lines_html}</tbody>
            </table>
            <p style="font-size:14pt; font-weight:bold; text-align:right;">
                Total: ${total:,.2f}
            </p>
            {"<p><em>" + notes + "</em></p>" if notes else ""}
        </div>
        <div style="padding:12px 20px; background:#f9f9f9; border: 1px solid #ddd; border-top:none; border-radius: 0 0 6px 6px;">
            <p style="color:gray; font-size:9pt; margin:0;">Thank you for your business! — JAK'S</p>
        </div>
    </div>
    """


def send_so_email(so: dict, lines: list, email: str) -> dict:
    """Send a sales order confirmation via email."""
    so_num = so.get("so_number", "")
    subject = f"JAK'S Sales Order {so_num} — Confirmation"
    html = _build_so_email(so, lines)
    return _send_email(email, subject, html)


def send_so_tracking_email(so: dict, email: str) -> dict:
    """Send sales order shipping/tracking notification via email."""
    tracking = so.get("tracking_number", "")
    if not tracking:
        return {"success": False, "error": "No tracking number on this order."}
    so_num = so.get("so_number", "")
    customer_name = so.get("customer_name", "Customer")
    ship_method = so.get("ship_method", "") or ""

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 640px; margin: auto;">
        <div style="background: #3d4a38; color: white; padding: 16px 20px; border-radius: 6px 6px 0 0;">
            <h2 style="margin:0;">Your Order Has Shipped!</h2>
        </div>
        <div style="padding: 16px 20px; border: 1px solid #ddd; border-top: none;">
            <p>Hi {customer_name},</p>
            <p>Great news — your order <strong>{so_num}</strong> is on its way!</p>
            <div style="background:#e8f5e9; padding:14px 20px; border-radius:6px; margin:16px 0; text-align:center;">
                <p style="margin:0 0 4px 0; color:#555;">Tracking Number</p>
                <p style="margin:0; font-size:18pt; font-weight:bold; letter-spacing:1px;">{tracking}</p>
                {"<p style='margin:4px 0 0 0; color:#555;'>via " + ship_method + "</p>" if ship_method else ""}
            </div>
        </div>
        <div style="padding:12px 20px; background:#f9f9f9; border: 1px solid #ddd; border-top:none; border-radius: 0 0 6px 6px;">
            <p style="color:gray; font-size:9pt; margin:0;">Thank you for your business! — JAK'S</p>
        </div>
    </div>
    """
    subject = f"JAK'S — Shipping Confirmation for {so_num}"
    return _send_email(email, subject, html)
