"""Bridge between the messaging UI and the Twilio SMS service.

Keeps all Twilio logic in sms_service; this module just exposes a
simple send_sms_message(phone, body) for the chat screen.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def send_sms_message(phone: str, body: str) -> dict:
    """Send a free-form SMS to *phone*. Returns {success, twilio_sid, error}."""
    from jaks_inventory.utils.sms_service import is_configured, normalise_phone, _get_client, _phone

    if not is_configured():
        return {"success": False, "error": "Twilio not configured"}

    to_number = normalise_phone(phone)
    try:
        client = _get_client()
        msg = client.messages.create(
            body=body,
            from_=_phone(),
            to=to_number,
        )
        log.info("SMS → %s  SID=%s", to_number, msg.sid)
        return {"success": True, "twilio_sid": msg.sid}
    except Exception as exc:
        log.error("SMS send failed: %s", exc)
        return {"success": False, "error": str(exc)}
