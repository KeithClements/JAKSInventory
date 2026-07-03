"""QBO health snapshot (D5).

Single status function used by the QBO Export Screen header and any
dashboard widget that wants a one-line answer to "is QBO working?".

The returned dict is intentionally flat / serialisable so callers can
render it as a label, a JSON line, or a tooltip without further work::

    {
        "configured": bool,         # client_id present
        "connected": bool,          # client.connect() succeeded
        "mode": "mock" | "live" | "disabled",
        "write_enabled": bool,
        "realm_id": str | None,
        "environment": "sandbox" | "production",
        # Counts pulled from the local DB (never touch QBO):
        "pending_invoices": int,    # unexported invoices in DB
        "pending_bills": int,
        "pending_credits": int,
        "failed_syncs": int,        # qbo_sync_log.status='failed'
        "last_export_at": str|None, # ISO timestamp of last qbo_exports row
        "last_error": str | None,
        "label": str,               # short human label, e.g. "Mock mode
                                    # (2 pending invoices)"
    }

Nothing here calls the real QBO API beyond the cheap ``connect()`` check.
``get_qbo_health(skip_connect=True)`` lets dashboard widgets refresh
frequently without hammering the network.
"""
from __future__ import annotations

from typing import Optional


def _pending_invoice_count() -> int:
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM invoices "
                "WHERE COALESCE(qbo_exported, 0) = 0 "
                "AND status NOT IN ('draft', 'void')"
            ).fetchone()
            return int(row[0] if row else 0)
    except Exception:
        return 0


def _pending_bill_count() -> int:
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM purchase_orders "
                "WHERE COALESCE(qbo_exported, 0) = 0 "
                "AND status NOT IN ('draft', 'cancelled')"
            ).fetchone()
            return int(row[0] if row else 0)
    except Exception:
        return 0


def _pending_credit_count() -> int:
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM customer_credits "
                "WHERE qbo_credit_id IS NULL "
                "AND status NOT IN ('voided')"
            ).fetchone()
            return int(row[0] if row else 0)
    except Exception:
        return 0


def _failed_sync_count() -> int:
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM qbo_sync_log WHERE status = 'failed'"
            ).fetchone()
            return int(row[0] if row else 0)
    except Exception:
        return 0


def _last_export_at() -> Optional[str]:
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT created_at FROM qbo_exports "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return str(row[0] if not hasattr(row, "keys") else row["created_at"])
    except Exception:
        return None


def _last_sync_error() -> Optional[str]:
    try:
        from db import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT last_error FROM qbo_sync_log "
                "WHERE status = 'failed' AND last_error IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return str(row[0] if not hasattr(row, "keys") else row["last_error"])
    except Exception:
        return None


def get_qbo_health(*, skip_connect: bool = False) -> dict:
    """Return a flat health-snapshot dict (see module docstring)."""
    import os

    try:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()
    except Exception:
        return {
            "configured": False, "connected": False,
            "mode": "disabled", "write_enabled": False,
            "realm_id": None, "environment": "sandbox",
            "pending_invoices": _pending_invoice_count(),
            "pending_bills": _pending_bill_count(),
            "pending_credits": _pending_credit_count(),
            "failed_syncs": _failed_sync_count(),
            "last_export_at": _last_export_at(),
            "last_error": "QBO integration import failed",
            "label": "QBO integration unavailable",
        }

    configured = bool(os.environ.get("QBO_CLIENT_ID"))
    environment = os.environ.get("QBO_ENVIRONMENT", "sandbox")

    try:
        is_mock = service.is_mock_mode
    except Exception:
        is_mock = True
    try:
        write_enabled = service.write_enabled
    except Exception:
        write_enabled = False

    connected = False
    last_error: Optional[str] = None
    if not skip_connect:
        try:
            connected = bool(service.connect())
        except Exception as e:
            last_error = str(e)

    realm_id = None
    try:
        status = service.get_status()
        realm_id = status.get("realm_id")
    except Exception:
        pass

    if is_mock:
        mode = "mock"
    elif not write_enabled:
        mode = "disabled"
    else:
        mode = "live"

    pending_inv = _pending_invoice_count()
    pending_bill = _pending_bill_count()
    pending_cred = _pending_credit_count()
    failed = _failed_sync_count()

    bits: list[str] = []
    if mode == "mock":
        bits.append("Mock mode")
    elif mode == "disabled":
        bits.append("Writes disabled")
    else:
        bits.append("Live")
    pending_total = pending_inv + pending_bill + pending_cred
    if pending_total:
        bits.append(f"{pending_total} pending")
    if failed:
        bits.append(f"{failed} failed")
    label = " · ".join(bits)

    return {
        "configured": configured,
        "connected": connected,
        "mode": mode,
        "write_enabled": write_enabled,
        "realm_id": realm_id,
        "environment": environment,
        "pending_invoices": pending_inv,
        "pending_bills": pending_bill,
        "pending_credits": pending_cred,
        "failed_syncs": failed,
        "last_export_at": _last_export_at(),
        "last_error": last_error or _last_sync_error(),
        "label": label,
    }
