"""Twilio SMS service for sending quote notifications and processing replies.

Outbound: sends a quote summary to the customer's phone.
Inbound:  webhook parses "Y"/"Yes" → marks quote accepted, moves to Sales Order.
"""

from __future__ import annotations

import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Config — read lazily so dotenv has time to load
# ---------------------------------------------------------------------------
def _sid():
    return os.getenv("TWILIO_ACCOUNT_SID", "")

def _token():
    return os.getenv("TWILIO_AUTH_TOKEN", "")

def _phone():
    return os.getenv("TWILIO_PHONE_NUMBER", "")


def is_configured() -> bool:
    """Return True if all three Twilio env vars are set."""
    return bool(_sid() and _token() and _phone())


def _get_client():
    """Lazy-create the Twilio REST client."""
    from twilio.rest import Client
    return Client(_sid(), _token())


# ---------------------------------------------------------------------------
#  Normalise phone numbers
# ---------------------------------------------------------------------------
def normalise_phone(raw: str) -> str:
    """Strip to digits, prepend +1 if needed (US numbers)."""
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


# ---------------------------------------------------------------------------
#  Build the SMS body from a quote dict
# ---------------------------------------------------------------------------
def _build_quote_sms(quote: dict) -> str:
    """Compose a concise SMS with quote summary."""
    q_num = quote.get("quote_number", "")
    customer_name = quote.get("customer_name", "Customer")
    total = float(quote.get("total", 0) or 0)
    core = float(quote.get("core_total", 0) or 0)
    lines = quote.get("lines", [])
    expires = quote.get("expiration_date", "")

    parts = [f"JAK'S Quote {q_num}"]
    parts.append(f"Hi {customer_name},")

    # Summarise up to 4 line items
    for ln in lines[:4]:
        desc = ln.get("description", "") or ln.get("title", "")
        qty = int(ln.get("qty", 1) or 1)
        price = float(ln.get("unit_price", 0) or 0)
        if desc:
            short = desc[:40] + ("..." if len(desc) > 40 else "")
            parts.append(f"  {qty}x {short} — ${price:,.2f}")
    if len(lines) > 4:
        parts.append(f"  ... +{len(lines) - 4} more items")

    parts.append(f"Total: ${total:,.2f}")
    if core > 0:
        parts.append(f"Core deposit: ${core:,.2f}")
    if expires:
        parts.append(f"Valid until: {expires}")

    parts.append("")
    parts.append("Reply Y to accept or call us with questions.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
#  Send a quote via SMS
# ---------------------------------------------------------------------------
def send_quote_sms(quote: dict, phone: str) -> dict:
    """Send quote summary SMS. Returns {success, twilio_sid, error}."""
    if not is_configured():
        return {"success": False, "error": "Twilio not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER in .env"}

    to_number = normalise_phone(phone)
    body = _build_quote_sms(quote)

    try:
        client = _get_client()
        message = client.messages.create(
            body=body,
            from_=_phone(),
            to=to_number,
        )
        log.info("SMS sent to %s  SID=%s", to_number, message.sid)

        # Log to DB
        _log_sms(
            quote_id=quote["id"],
            customer_id=quote.get("customer_id"),
            phone=to_number,
            direction="outbound",
            twilio_sid=message.sid,
            message_body=body,
            status="sent",
        )

        return {"success": True, "twilio_sid": message.sid}

    except Exception as exc:
        log.error("SMS send failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
#  Process an inbound reply (called by webhook)
# ---------------------------------------------------------------------------
ACCEPT_KEYWORDS = {"y", "yes", "yeah", "yep", "approve", "accept", "confirmed", "ok"}


def process_inbound_reply(from_phone: str, body: str, twilio_sid: str) -> dict:
    """Match reply to a pending quote SMS and update status if accepted.

    Returns {matched, quote_id, accepted, error}.
    """
    from db import get_db
    from db.inventory import update_quote

    normalised = normalise_phone(from_phone)

    with get_db() as conn:
        # Find the most recent outbound SMS to this phone that hasn't been replied to
        row = conn.execute("""
            SELECT id, quote_id, customer_id
            FROM quote_sms_log
            WHERE phone = %s
              AND direction = 'outbound'
              AND reply_body IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """, (normalised,)).fetchone()

        if not row:
            log.warning("Inbound SMS from %s with no matching outbound quote", normalised)
            return {"matched": False, "error": "No pending quote for this number"}

        sms_id = row["id"] if hasattr(row, "__getitem__") and isinstance(row["id"], int) else row[0]
        quote_id = row["quote_id"] if hasattr(row, "__getitem__") else row[1]

        # Record the reply
        conn.execute("""
            UPDATE quote_sms_log
            SET reply_body = %s, replied_at = datetime('now'), status = 'replied'
            WHERE id = %s
        """, (body.strip(), sms_id))

        # Log the inbound message too
        _log_sms_conn(
            conn,
            quote_id=quote_id,
            customer_id=row["customer_id"] if hasattr(row, "__getitem__") else row[2],
            phone=normalised,
            direction="inbound",
            twilio_sid=twilio_sid,
            message_body=body.strip(),
            status="received",
        )
        conn.commit()

    # Check if the reply is an acceptance
    cleaned = body.strip().lower().rstrip(".!,")
    accepted = cleaned in ACCEPT_KEYWORDS

    if accepted:
        result = update_quote(quote_id, status="accepted")
        log.info("Quote %s ACCEPTED via SMS from %s", quote_id, normalised)

        # Send confirmation SMS
        if is_configured():
            try:
                client = _get_client()
                client.messages.create(
                    body="Thank you! Your quote has been accepted. We'll be in touch with next steps.",
                    from_=_phone(),
                    to=normalised,
                )
            except Exception as exc:
                log.error("Failed to send confirmation SMS: %s", exc)
    else:
        log.info("Quote %s reply from %s (not accepted): %s", quote_id, normalised, body.strip())

    return {"matched": True, "quote_id": quote_id, "accepted": accepted}


# ---------------------------------------------------------------------------
#  Get SMS history for a quote
# ---------------------------------------------------------------------------
def get_sms_log(quote_id: int) -> list[dict]:
    """Return all SMS messages for a given quote."""
    from db import get_db
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM quote_sms_log
            WHERE quote_id = %s
            ORDER BY created_at
        """, (quote_id,)).fetchall()
        return [dict(r) if hasattr(r, "keys") else {} for r in rows]


# ---------------------------------------------------------------------------
#  Internal DB helpers
# ---------------------------------------------------------------------------
def _log_sms(quote_id, customer_id, phone, direction, twilio_sid, message_body, status):
    from db import get_db
    with get_db() as conn:
        _log_sms_conn(conn, quote_id, customer_id, phone, direction, twilio_sid, message_body, status)
        conn.commit()


def _log_sms_conn(conn, quote_id, customer_id, phone, direction, twilio_sid, message_body, status):
    conn.execute("""
        INSERT INTO quote_sms_log
            (quote_id, customer_id, phone, direction, twilio_sid, message_body, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (quote_id, customer_id, phone, direction, twilio_sid, message_body, status))
