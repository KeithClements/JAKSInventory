"""QBO webhook receiver — Tier 3.

QBO calls a configured HTTPS endpoint whenever entities change in the
realm. Each request carries:

* ``intuit-signature`` HTTP header — a base64-encoded HMAC-SHA256 of
  the raw body using the realm's *Verifier Token* (set in the Intuit
  Developer dashboard and stored locally as setting
  ``qbo.webhook_verifier_token``).
* A JSON body containing one or more ``eventNotifications`` keyed by
  ``realmId`` and ``dataChangeEvent.entities[]``.

This module exposes two pure functions:

``verify_signature(raw_body, signature_header)``
    Constant-time compare against HMAC-SHA256 of the configured
    verifier token. Returns ``False`` when the token is unset
    (so production won't accept unsigned traffic by accident).

``record_payload(raw_body, signature_header)``
    Parses the QBO notification envelope and inserts one row per
    entity event into ``qbo_webhook_events``. Returns ``{accepted:int}``.

Routing into Flask lives in :mod:`webapp.app` so this module remains
framework-agnostic and unit-testable.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Iterable

logger = logging.getLogger(__name__)


def _verifier_token() -> str | None:
    """Read the Intuit verifier token from settings (or env fallback)."""
    try:
        from db import get_setting
        val = (get_setting("qbo.webhook_verifier_token") or "").strip()
        if val:
            return val
    except Exception:
        pass
    import os
    return (os.environ.get("QBO_WEBHOOK_VERIFIER_TOKEN") or "").strip() or None


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Return True if the HMAC-SHA256 of ``raw_body`` matches header.

    Rejects requests when no verifier token is configured — that's a
    deliberate fail-closed default for an exposed endpoint.
    """
    token = _verifier_token()
    if not token or not signature_header:
        return False
    try:
        digest = hmac.new(
            token.encode("utf-8"), raw_body, hashlib.sha256,
        ).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, signature_header.strip())
    except Exception as exc:
        logger.warning("verify_signature failed: %s", exc)
        return False


def _iter_events(payload: dict) -> Iterable[tuple[str, str, dict]]:
    """Yield ``(realm_id, last_updated, entity_dict)`` for every event."""
    for notif in (payload.get("eventNotifications") or []):
        realm = str(notif.get("realmId") or "")
        change = notif.get("dataChangeEvent") or {}
        last_updated = str(change.get("lastUpdated") or "")
        for ent in (change.get("entities") or []):
            yield realm, last_updated, ent


def record_payload(raw_body: bytes, signature_header: str | None) -> dict:
    """Verify signature and persist every event to the DB.

    Returns ``{accepted: N}`` on success, ``{accepted: 0,
    error: "..."}`` on signature failure.
    """
    if not verify_signature(raw_body, signature_header):
        return {"accepted": 0, "error": "invalid signature"}

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception as exc:
        return {"accepted": 0, "error": f"bad json: {exc}"}

    rows: list[tuple] = []
    for realm, last_updated, ent in _iter_events(payload):
        rows.append((
            realm,
            str(ent.get("name") or ""),
            str(ent.get("id") or ""),
            str(ent.get("operation") or ""),
            last_updated,
            json.dumps(ent),
            (signature_header or "")[:200],
        ))

    if not rows:
        return {"accepted": 0}

    try:
        from db.database import get_db
        with get_db() as conn:
            conn.executemany(
                """
                INSERT INTO qbo_webhook_events
                    (realm_id, entity_type, entity_id, operation,
                     last_updated, payload_json, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            try:
                conn.commit()
            except Exception:
                pass
    except Exception as exc:
        logger.exception("record_payload DB insert failed")
        return {"accepted": 0, "error": str(exc)}

    return {"accepted": len(rows)}
