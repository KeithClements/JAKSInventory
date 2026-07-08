"""QBO RefundReceipt pull (Phase C2).

A QBO RefundReceipt is the cash counterpart to a CreditMemo — money
actually leaves the bank account back to the customer. We persist
refunds into ``customer_credits`` with ``source_type='refund_receipt'``
and ``qbo_refund_receipt_id`` as the idempotency key. ``status='paid'``
indicates the cash already moved (vs ``status='open'`` for unapplied
credit memos).

Public API
----------
fetch_qbo_refund_receipts(customer_qbo_id=None) -> list[dict]
import_qbo_refund_receipts_to_db(...)            -> upsert customer_credits
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


def _refund_to_dict(rr) -> dict:
    cust_ref = getattr(rr, "CustomerRef", None)
    cust_id = getattr(cust_ref, "value", None) if cust_ref else None
    cust_name = getattr(cust_ref, "name", None) if cust_ref else None
    dep_ref = getattr(rr, "DepositToAccountRef", None)
    dep_name = getattr(dep_ref, "name", None) if dep_ref else None
    pm_ref = getattr(rr, "PaymentMethodRef", None)
    pm_name = getattr(pm_ref, "name", None) if pm_ref else None
    return {
        "id": str(rr.Id),
        "doc_number": getattr(rr, "DocNumber", "") or "",
        "customer_qbo_id": str(cust_id) if cust_id else None,
        "customer_name": cust_name or "",
        "txn_date": _date_str(getattr(rr, "TxnDate", None)),
        "total": float(getattr(rr, "TotalAmt", 0) or 0),
        "payment_method": pm_name or "",
        "deposit_to_account": dep_name or "",
        "private_note": getattr(rr, "PrivateNote", "") or "",
    }


def _get_mock_qbo_refund_receipts() -> list[dict]:
    return [
        {
            "id": "RR-3001", "doc_number": "RR-1001",
            "customer_qbo_id": "C-100",
            "customer_name": "Acme Fleet Services",
            "txn_date": "2026-01-15",
            "total": 120.00,
            "payment_method": "Check",
            "deposit_to_account": "Checking",
            "private_note": "Cash refund for returned item",
        },
    ]


def fetch_qbo_refund_receipts(
    customer_qbo_id: str | None = None,
) -> list[dict]:
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_refund_receipts()
        if customer_qbo_id:
            return [r for r in rows
                    if r["customer_qbo_id"] == str(customer_qbo_id)]
        return rows
    try:
        from quickbooks.objects.refundreceipt import RefundReceipt
        from qbo._throttle import throttle
        throttle()
        if customer_qbo_id:
            rows = RefundReceipt.filter(
                CustomerRef=str(customer_qbo_id),
                qb=client._client, max_results=500,
            )
        else:
            rows = RefundReceipt.all(qb=client._client, max_results=1000)
        return [_refund_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_refund_receipts failed: %s", exc)
        return []


def _resolve_local_customer_id(conn, qbo_customer_id) -> int | None:
    if not qbo_customer_id:
        return None
    row = conn.execute(
        "SELECT id FROM customers WHERE qbo_customer_id = %s LIMIT 1",
        (str(qbo_customer_id),),
    ).fetchone()
    if not row:
        return None
    return row[0] if not hasattr(row, "keys") else row["id"]


def import_qbo_refund_receipts_to_db(
    *,
    customer_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Upsert QBO RefundReceipts into ``customer_credits``.

    Refunds are stored alongside credit memos with
    ``source_type='refund_receipt'`` and ``status='paid'`` (the cash
    already moved). The idempotency key is
    ``customer_credits.qbo_refund_receipt_id``.

    Skip rule: a customer must be locally linked via ``qbo_customer_id``
    — refund receipts without a known customer are reported in
    ``skipped`` rather than created as orphans.

    Returns ``{total, created, updated, skipped, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_refund_receipts(customer_qbo_id=customer_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "updated": 0, "skipped": 0,
        "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, rr in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(
                    i, total, rr.get("doc_number") or rr["id"]
                )

            qbo_rr_id = str(rr["id"])
            customer_id = _resolve_local_customer_id(
                conn, rr.get("customer_qbo_id")
            )
            if customer_id is None:
                result["skipped"] += 1
                continue

            row = conn.execute(
                "SELECT id FROM customer_credits "
                "WHERE qbo_refund_receipt_id = %s LIMIT 1",
                (qbo_rr_id,),
            ).fetchone()

            try:
                memo = (rr.get("private_note") or "")[:500]
                if row:
                    local_id = row[0] if not hasattr(row, "keys") else row["id"]
                    conn.execute(
                        """UPDATE customer_credits SET
                              amount       = %s,
                              status       = 'paid',
                              issue_date   = COALESCE(issue_date, %s),
                              applied_date = COALESCE(applied_date, %s),
                              notes        = COALESCE(NULLIF(notes,''), %s),
                              qbo_refund_pulled_at = %s,
                              updated_at   = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (rr["total"], rr["txn_date"], rr["txn_date"],
                         memo, now_iso, local_id),
                    )
                    result["updated"] += 1
                else:
                    conn.execute(
                        """INSERT INTO customer_credits (
                                customer_id, source_type, source_id,
                                amount, status,
                                issue_date, applied_date,
                                qbo_refund_receipt_id,
                                qbo_refund_pulled_at,
                                notes
                           ) VALUES (
                                %s, 'refund_receipt', NULL,
                                %s, 'paid',
                                %s, %s,
                                %s,
                                %s,
                                %s
                           )""",
                        (customer_id, rr["total"], rr["txn_date"],
                         rr["txn_date"], qbo_rr_id, now_iso, memo),
                    )
                    result["created"] += 1
            except Exception as exc:
                result["errors"].append(
                    f"{rr.get('doc_number') or qbo_rr_id}: {exc}"
                )
        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_refund_receipts_to_db: %d created, %d updated, "
        "%d errors", result["created"], result["updated"],
        len(result["errors"]),
    )
    return result


__all__ = [
    "fetch_qbo_refund_receipts",
    "import_qbo_refund_receipts_to_db",
]
