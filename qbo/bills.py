"""QBO Bill pull.

Push lives in :mod:`qbo.push` (``push_bills_to_qbo``) \u2014 it turns local
``purchase_orders`` rows into QBO ``Bill`` objects and stamps
``purchase_orders.qbo_bill_id``.

This module adds the reverse path so bills entered directly in QBO
(or modified there post-push) update the linked local PO's ``bill_balance``
and ``qbo_bill_pulled_at`` columns.

Public API
----------
fetch_qbo_bills(vendor_qbo_id=None) \u2192 list[dict]
import_qbo_bills_to_db(...)         \u2192 update linked purchase_orders rows
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


def _bill_to_dict(bill) -> dict:
    vendor_ref = getattr(bill, "VendorRef", None)
    vendor_id = getattr(vendor_ref, "value", None) if vendor_ref else None
    vendor_name = getattr(vendor_ref, "name", None) if vendor_ref else None
    return {
        "id": str(bill.Id),
        "doc_number": getattr(bill, "DocNumber", "") or "",
        "vendor_qbo_id": str(vendor_id) if vendor_id else None,
        "vendor_name": vendor_name or "",
        "txn_date": _date_str(getattr(bill, "TxnDate", None)),
        "due_date": _date_str(getattr(bill, "DueDate", None)),
        "total": float(getattr(bill, "TotalAmt", 0) or 0),
        "balance": float(getattr(bill, "Balance", 0) or 0),
        "private_note": getattr(bill, "PrivateNote", "") or "",
    }


def _get_mock_qbo_bills() -> list[dict]:
    return [
        {
            "id": "BILL-6001", "doc_number": "B-1001",
            "vendor_qbo_id": "V-300", "vendor_name": "PAI Industries",
            "txn_date": "2025-12-05", "due_date": "2026-01-04",
            "total": 4500.0, "balance": 4500.0,
            "private_note": "",
        },
    ]


def fetch_qbo_bills(vendor_qbo_id: str | None = None) -> list[dict]:
    """Fetch vendor bills from QBO."""
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_bills()
        if vendor_qbo_id:
            return [r for r in rows if r["vendor_qbo_id"] == str(vendor_qbo_id)]
        return rows
    try:
        from quickbooks.objects.bill import Bill
        from qbo._throttle import throttle
        throttle()
        if vendor_qbo_id:
            bills = Bill.filter(VendorRef=str(vendor_qbo_id),
                                qb=client._client, max_results=500)
        else:
            bills = Bill.all(qb=client._client, max_results=1000)
        return [_bill_to_dict(b) for b in bills]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_bills failed: %s", exc)
        return []


def _create_missing_enabled() -> bool:
    """Read opt-in flag for B2 auto-create of POs from QBO Bills."""
    import os
    try:
        from db import get_setting
        val = get_setting("qbo_bill_pull_create_missing", None)
        if val is not None:
            return str(val).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    return str(os.environ.get("QBO_BILL_PULL_CREATE_MISSING", "")
               ).strip().lower() in ("1", "true", "yes", "on")


def _lookup_vendor_id(conn, vendor_qbo_id: str | None) -> int | None:
    if not vendor_qbo_id:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM vendors WHERE qbo_vendor_id = %s LIMIT 1",
            (str(vendor_qbo_id),),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row["id"] if hasattr(row, "keys") else row[0]


def _next_po_number(conn, doc_number: str, qbo_bill_id: str) -> str:
    """Pick a unique po_number for an auto-created PO."""
    base = (doc_number or "").strip() or f"QBO-BILL-{qbo_bill_id}"
    candidate = f"QBO-{base}"[:60]
    # Ensure uniqueness (po_number UNIQUE NOT NULL).
    suffix = 0
    while True:
        check = candidate if suffix == 0 else f"{candidate}-{suffix}"
        try:
            existing = conn.execute(
                "SELECT 1 FROM purchase_orders WHERE po_number = %s LIMIT 1",
                (check,),
            ).fetchone()
        except Exception:
            return check
        if not existing:
            return check
        suffix += 1
        if suffix > 99:
            return f"{candidate}-{qbo_bill_id}"


def import_qbo_bills_to_db(
    *,
    vendor_qbo_id: str | None = None,
    create_missing: bool | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Reconcile QBO Bill balances onto linked ``purchase_orders``.

    Match rule: ``purchase_orders.qbo_bill_id`` == ``Bill.Id``.

    By default, bills with no matching local PO are counted as
    ``skipped``. When ``create_missing`` is True (or the
    ``qbo_bill_pull_create_missing`` app-setting / env flag is set),
    a stub PO is created locally with ``status='received'`` and the
    QBO bill's vendor + total, so AP balances reconcile end-to-end.
    The stub carries ``po_number='QBO-<doc>'`` to make its origin
    obvious in the UI.

    Returns ``{total, matched, created, skipped, errors}``.
    """
    from db.database import get_db

    if create_missing is None:
        create_missing = _create_missing_enabled()

    qbo_rows = fetch_qbo_bills(vendor_qbo_id=vendor_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "matched": 0, "created": 0,
        "skipped": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, qb_ in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(i, total, qb_.get("doc_number") or qb_["id"])
            qbo_bill_id = str(qb_["id"])
            try:
                row = conn.execute(
                    "SELECT id FROM purchase_orders WHERE qbo_bill_id = %s LIMIT 1",
                    (qbo_bill_id,),
                ).fetchone()
                if row:
                    po_id = row["id"] if hasattr(row, "keys") else row[0]
                    conn.execute(
                        """UPDATE purchase_orders SET
                              bill_balance = %s,
                              qbo_bill_pulled_at = %s,
                              updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (qb_["balance"], now_iso, po_id),
                    )
                    result["matched"] += 1
                    continue

                # No local PO matched.
                if not create_missing:
                    result["skipped"] += 1
                    continue

                vendor_id = _lookup_vendor_id(conn, qb_.get("vendor_qbo_id"))
                if not vendor_id:
                    # Can't auto-create without a linked vendor.
                    result["skipped"] += 1
                    continue

                po_number = _next_po_number(
                    conn, qb_.get("doc_number") or "", qbo_bill_id,
                )
                conn.execute(
                    """INSERT INTO purchase_orders (
                          po_number, vendor_id, status,
                          order_date, total,
                          qbo_bill_id, qbo_bill_pulled_at, bill_balance,
                          notes
                       ) VALUES (%s, %s, 'received',
                                 %s, %s,
                                 %s, %s, %s,
                                 %s)""",
                    (po_number, vendor_id,
                     qb_.get("txn_date"), qb_.get("total") or 0,
                     qbo_bill_id, now_iso, qb_["balance"],
                     f"Auto-created from QBO Bill {qb_.get('doc_number') or qbo_bill_id}"),
                )
                result["created"] += 1
            except Exception as exc:
                result["errors"].append(
                    f"{qb_.get('doc_number') or qbo_bill_id}: {exc}"
                )

        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_bills_to_db: %d matched, %d created, %d skipped, %d errors",
        result["matched"], result["created"],
        result["skipped"], len(result["errors"]),
    )
    return result


__all__ = ["fetch_qbo_bills", "import_qbo_bills_to_db"]
