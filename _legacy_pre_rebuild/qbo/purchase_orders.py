"""QBO PurchaseOrder pull.

Push lives in :mod:`qbo.push` (``push_pos_to_qbo``) — turns local
``purchase_orders`` rows into QBO ``PurchaseOrder`` objects and stamps
``purchase_orders.qbo_po_id``.

This module adds the reverse path: POs entered or modified directly
in QBO flow back into JAKS. Reconciliation is keyed on
``purchase_orders.qbo_po_id``; we never auto-create local POs from
QBO (POs carry receiving / inventory state QBO doesn't model).

Public API
----------
fetch_qbo_purchase_orders(vendor_qbo_id=None) → list[dict]
import_qbo_purchase_orders_to_db(...)         → reconcile linked POs
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from qbo.client import QBOClient, get_qbo_client

logger = logging.getLogger(__name__)


def _client() -> QBOClient:
    c = get_qbo_client()
    if not c.is_connected:
        c.connect()
    return c


def _date_str(val) -> str | None:
    if val is None or val == "":
        return None
    if isinstance(val, str):
        return val[:10]
    try:
        return val.strftime("%Y-%m-%d")
    except Exception:
        return str(val)[:10]


def _po_to_dict(po) -> dict:
    vendor_ref = getattr(po, "VendorRef", None)
    vendor_id = getattr(vendor_ref, "value", None) if vendor_ref else None
    vendor_name = getattr(vendor_ref, "name", None) if vendor_ref else None
    return {
        "id": str(po.Id),
        "doc_number": getattr(po, "DocNumber", "") or "",
        "vendor_qbo_id": str(vendor_id) if vendor_id else None,
        "vendor_name": vendor_name or "",
        "txn_date": _date_str(getattr(po, "TxnDate", None)),
        "due_date": _date_str(getattr(po, "DueDate", None)),
        "total": float(getattr(po, "TotalAmt", 0) or 0),
        "po_status": (getattr(po, "POStatus", "") or "").lower(),
        "private_note": getattr(po, "PrivateNote", "") or "",
    }


def _get_mock_qbo_pos() -> list[dict]:
    return [
        {
            "id": "QBO-PO-8001", "doc_number": "PO-9001",
            "vendor_qbo_id": "V-300", "vendor_name": "PAI Industries",
            "txn_date": "2026-01-10", "due_date": "2026-01-25",
            "total": 2750.0, "po_status": "open", "private_note": "",
        },
    ]


def fetch_qbo_purchase_orders(vendor_qbo_id: str | None = None) -> list[dict]:
    """Fetch purchase orders from QBO."""
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_pos()
        if vendor_qbo_id:
            return [r for r in rows if r["vendor_qbo_id"] == str(vendor_qbo_id)]
        return rows
    try:
        from quickbooks.objects.purchaseorder import PurchaseOrder
        from qbo._throttle import throttle
        throttle()
        if vendor_qbo_id:
            pos = PurchaseOrder.filter(
                VendorRef=str(vendor_qbo_id),
                qb=client._client, max_results=500,
            )
        else:
            pos = PurchaseOrder.all(qb=client._client, max_results=1000)
        return [_po_to_dict(p) for p in pos]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_purchase_orders failed: %s", exc)
        return []


# Map QBO POStatus → local purchase_orders.status (when remote moves on).
_QBO_PO_STATUS_TO_LOCAL = {
    "open": "ordered",
    "closed": "received",
}


def import_qbo_purchase_orders_to_db(
    *,
    vendor_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Reconcile QBO PurchaseOrder changes onto linked local POs.

    Match rule: ``purchase_orders.qbo_po_id`` == ``PurchaseOrder.Id``.
    POs with no matching local row are tracked in the ``skipped`` count
    (we deliberately do **not** auto-create local POs from QBO — they
    carry receiving / inventory state QBO doesn't model).

    Updates: ``total``, ``status`` (only when QBO shows closed),
    ``qbo_po_pulled_at``.

    Returns ``{total, matched, skipped, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_purchase_orders(vendor_qbo_id=vendor_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "matched": 0, "skipped": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, qp in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(i, total, qp.get("doc_number") or qp["id"])
            qbo_po_id = str(qp["id"])
            try:
                row = conn.execute(
                    "SELECT id, status FROM purchase_orders "
                    "WHERE qbo_po_id = %s LIMIT 1",
                    (qbo_po_id,),
                ).fetchone()
                if not row:
                    result["skipped"] += 1
                    continue
                po_id = row["id"] if hasattr(row, "keys") else row[0]
                local_status = (row["status"] if hasattr(row, "keys")
                                else row[1]) or "draft"

                # Status promotion: only advance from open/ordered → received
                # when QBO says closed; don't downgrade if local is already
                # received/closed/cancelled.
                qbo_status = (qp.get("po_status") or "").lower()
                new_status = _QBO_PO_STATUS_TO_LOCAL.get(qbo_status)
                advance = (
                    new_status == "received"
                    and local_status not in ("received", "closed", "cancelled")
                )

                if advance:
                    conn.execute(
                        """UPDATE purchase_orders SET
                              total = %s,
                              status = %s,
                              qbo_po_pulled_at = %s,
                              updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (qp["total"], new_status, now_iso, po_id),
                    )
                else:
                    conn.execute(
                        """UPDATE purchase_orders SET
                              total = %s,
                              qbo_po_pulled_at = %s,
                              updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (qp["total"], now_iso, po_id),
                    )
                result["matched"] += 1
            except Exception as exc:
                result["errors"].append(
                    f"{qp.get('doc_number') or qbo_po_id}: {exc}"
                )

        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_purchase_orders_to_db: "
        "%d matched, %d skipped, %d errors",
        result["matched"], result["skipped"], len(result["errors"]),
    )
    return result


__all__ = [
    "fetch_qbo_purchase_orders",
    "import_qbo_purchase_orders_to_db",
]
