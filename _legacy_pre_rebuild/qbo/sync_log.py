"""QBO sync-log audit helpers.

Every QBO write (live or mocked) calls :func:`start_log` before issuing
the request and :func:`complete_log` after, so the new Audit Log UI can
show exactly what was sent, what came back, and what QBO stored on
read-back.

The schema lives in :mod:`db.migrations.088_qbo_sync_log_audit`, which
extends the original idempotency table from migration 051.

All helpers are import-safe — they swallow DB errors with a logged
warning so a failure to write an audit row never blocks the QBO push
itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL construction (for "what the SDK would have called")
# ---------------------------------------------------------------------------

_SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com"
_PRODUCTION_BASE = "https://quickbooks.api.intuit.com"


def build_qbo_url(realm_id: str | None,
                  environment: str | None,
                  qbo_object_type: str | None) -> str:
    """Return the API URL the SDK *would* hit, for documentation/debug.

    We do not actually issue HTTP requests from this module — the
    `python-quickbooks` SDK handles that — but the URL is useful in the
    audit log and lets the UI surface a "what endpoint?" hint.
    """
    env = (environment or "sandbox").lower()
    base = _PRODUCTION_BASE if env == "production" else _SANDBOX_BASE
    realm = str(realm_id or "").strip() or "<no-realm>"
    obj = (qbo_object_type or "").lower() or "<no-object>"
    return f"{base}/v3/company/{realm}/{obj}?minorversion=75"


def current_environment() -> str:
    """Return ``"mock"`` / ``"sandbox"`` / ``"production"`` for the audit row."""
    try:
        from qbo.config import is_mock_mode
        if is_mock_mode():
            return "mock"
    except Exception:
        pass
    import os
    env = (os.environ.get("QBO_ENVIRONMENT") or "sandbox").strip().lower()
    return "production" if env == "production" else "sandbox"


def current_realm_id() -> str:
    import os
    return (os.environ.get("QBO_REALM_ID") or "").strip()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def to_json_text(obj: Any) -> str:
    """Best-effort JSON dump. Falls back to ``repr`` for non-JSON types."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, indent=2, sort_keys=True, default=str)
    except Exception:
        return repr(obj)


def qbo_obj_to_dict(qbo_obj: Any) -> dict:
    """Convert a ``python-quickbooks`` SDK object to a plain dict."""
    if qbo_obj is None:
        return {}
    for attr in ("to_dict", "to_json"):
        fn = getattr(qbo_obj, attr, None)
        if callable(fn):
            try:
                data = fn()
                if isinstance(data, str):
                    return json.loads(data)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    # Last resort: introspect ``__dict__`` and strip private attrs.
    try:
        return {k: v for k, v in vars(qbo_obj).items() if not k.startswith("_")}
    except Exception:
        return {"_repr": repr(qbo_obj)}


# ---------------------------------------------------------------------------
# CRUD on qbo_sync_log
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def start_log(
    *,
    source_type: str,
    source_id: int | None,
    qbo_object_type: str,
    request_method: str = "POST",
    request_url: str = "",
    request_json: str = "",
    environment: str | None = None,
    realm_id: str | None = None,
) -> int | None:
    """Insert an initial audit row before the QBO call. Returns its id.

    Returns ``None`` if the row could not be inserted — callers should
    treat the log id as advisory and never crash on ``None``.
    """
    env = (environment or current_environment()).lower()
    url = request_url or build_qbo_url(
        realm_id or current_realm_id(), env, qbo_object_type,
    )
    # Migration 051 declared ``idempotency_key`` NOT NULL UNIQUE for the
    # retry tracker. The audit layer doesn't need true idempotency, but
    # we still need to satisfy the constraint — generate a unique key
    # per audit row.
    idem = hashlib.sha256(
        f"audit|{source_type}|{source_id}|{uuid.uuid4().hex}".encode()
    ).hexdigest()
    try:
        from db import get_db
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO qbo_sync_log (
                    entity_type, local_ref, idempotency_key,
                    source_type, source_id, qbo_object_type,
                    request_method, request_url, request_json,
                    environment, status, attempts,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    source_type,
                    str(source_id) if source_id is not None else "",
                    idem,
                    source_type,
                    int(source_id) if source_id is not None else None,
                    qbo_object_type,
                    request_method,
                    url,
                    request_json or "",
                    env,
                    "pending",
                    1,
                    _now(),
                    _now(),
                ),
            )
            conn.commit()
            log_id = cursor.lastrowid
            if not log_id:
                row = conn.execute(
                    "SELECT MAX(id) AS id FROM qbo_sync_log"
                ).fetchone()
                log_id = row["id"] if hasattr(row, "keys") else row[0]
            return int(log_id) if log_id else None
    except Exception as exc:
        logger.warning("[qbo_sync_log] start_log failed: %s", exc)
        return None


def update_request(log_id: int | None, *, request_json: str) -> None:
    """Backfill the request payload once the SDK object is built."""
    if not log_id:
        return
    try:
        from db import get_db
        with get_db() as conn:
            conn.execute(
                "UPDATE qbo_sync_log "
                "SET request_json=%s, updated_at=%s WHERE id=%s",
                (request_json, _now(), int(log_id)),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[qbo_sync_log] update_request failed: %s", exc)


def complete_log(
    log_id: int | None,
    *,
    status: str,
    response_status: int | None = None,
    response_json: str = "",
    qbo_object_id: str | None = None,
    error_message: str | None = None,
) -> None:
    """Mark the audit row with the final outcome.

    ``status`` is one of ``success`` / ``failed`` / ``skipped``.
    """
    if not log_id:
        return
    try:
        from db import get_db
        with get_db() as conn:
            conn.execute(
                """
                UPDATE qbo_sync_log
                SET status=%s,
                    response_status=%s,
                    response_json=%s,
                    qbo_id=COALESCE(%s, qbo_id),
                    last_error=%s,
                    error_message=%s,
                    updated_at=%s
                WHERE id=%s
                """,
                (
                    status,
                    response_status,
                    response_json or "",
                    qbo_object_id,
                    error_message,
                    error_message,
                    _now(),
                    int(log_id),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[qbo_sync_log] complete_log failed: %s", exc)


def record_readback(log_id: int, *, readback_json: str) -> None:
    """Stamp a manual Read Back From QBO result on the audit row."""
    try:
        from db import get_db
        with get_db() as conn:
            conn.execute(
                "UPDATE qbo_sync_log "
                "SET readback_json=%s, readback_at=%s, updated_at=%s "
                "WHERE id=%s",
                (readback_json or "", _now(), _now(), int(log_id)),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[qbo_sync_log] record_readback failed: %s", exc)


def mark_resolved(log_id: int) -> None:
    try:
        from db import get_db
        with get_db() as conn:
            conn.execute(
                "UPDATE qbo_sync_log "
                "SET resolved_at=%s, updated_at=%s WHERE id=%s",
                (_now(), _now(), int(log_id)),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[qbo_sync_log] mark_resolved failed: %s", exc)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    return dict(enumerate(row))


def get_log(log_id: int) -> dict | None:
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM qbo_sync_log WHERE id=%s", (int(log_id),),
            ).fetchone()
        return _row_to_dict(row) if row else None
    except Exception as exc:
        logger.warning("[qbo_sync_log] get_log failed: %s", exc)
        return None


def list_logs(
    *,
    limit: int = 200,
    source_type: str | None = None,
    source_id: int | None = None,
    qbo_object_type: str | None = None,
    status: str | None = None,
    environment: str | None = None,
    include_resolved: bool = True,
) -> list[dict]:
    """Return the most recent matching audit rows (newest first)."""
    where: list[str] = []
    params: list[Any] = []
    if source_type:
        where.append("source_type=%s")
        params.append(source_type)
    if source_id is not None:
        where.append("source_id=%s")
        params.append(int(source_id))
    if qbo_object_type:
        where.append("qbo_object_type=%s")
        params.append(qbo_object_type)
    if status:
        where.append("status=%s")
        params.append(status)
    if environment:
        where.append("environment=%s")
        params.append(environment)
    if not include_resolved:
        where.append("(resolved_at IS NULL OR resolved_at='')")
    sql = "SELECT * FROM qbo_sync_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(int(limit))
    try:
        from db import get_db
        with get_db() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[qbo_sync_log] list_logs failed: %s", exc)
        return []


def latest_for(source_type: str, source_id: int) -> dict | None:
    """Convenience: most recent audit row for a single local entity."""
    rows = list_logs(source_type=source_type, source_id=source_id, limit=1)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Open-in-QBO deep links
# ---------------------------------------------------------------------------

_QBO_UI_OBJECT_MAP = {
    "Invoice": "invoice",
    "Bill": "bill",
    "Payment": "recvpayment",
    "CreditMemo": "creditmemo",
    "JournalEntry": "journal",
    "Customer": "customerdetail",
    "Vendor": "vendordetail",
    "Item": "item",
}


def open_in_qbo_url(environment: str | None,
                    qbo_object_type: str | None,
                    qbo_object_id: str | None) -> str | None:
    """Return a UI URL the user can click to view the object in QBO."""
    if not qbo_object_type or not qbo_object_id:
        return None
    slug = _QBO_UI_OBJECT_MAP.get(qbo_object_type)
    if not slug:
        return None
    env = (environment or "sandbox").lower()
    host = "app.sandbox.qbo.intuit.com" if env != "production" else "app.qbo.intuit.com"
    if slug == "customerdetail":
        return f"https://{host}/app/customerdetail?nameId={qbo_object_id}"
    if slug == "vendordetail":
        return f"https://{host}/app/vendordetail?nameId={qbo_object_id}"
    if slug == "item":
        return f"https://{host}/app/items"
    return f"https://{host}/app/{slug}?txnId={qbo_object_id}"
