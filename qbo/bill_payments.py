"""QBO BillPayment pull (Phase C3).

A QBO BillPayment represents cash leaving the bank (or credit card)
to pay a vendor against one or more bills. We persist them into a
dedicated ``bill_payments`` table and, when a linked PO is identifiable
via the linked Bill's ``qbo_bill_id``, also decrement that PO's
``bill_balance`` so AP reporting stays accurate.

Push is a follow-on enhancement; this iteration delivers the inbound
path so payments made directly in QuickBooks Online flow into JAKS.

Public API
----------
fetch_qbo_bill_payments(vendor_qbo_id=None) -> list[dict]
import_qbo_bill_payments_to_db(...)          -> upsert bill_payments
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


def _linked_bill_ids(bp) -> list[str]:
    """Extract Bill QBO ids from BillPayment.Line[].LinkedTxn[]."""
    out: list[str] = []
    for line in (getattr(bp, "Line", None) or []):
        for lt in (getattr(line, "LinkedTxn", None) or []):
            if (getattr(lt, "TxnType", "") or "") == "Bill":
                tid = getattr(lt, "TxnId", None)
                if tid:
                    out.append(str(tid))
    return out


def _bp_to_dict(bp) -> dict:
    vendor_ref = getattr(bp, "VendorRef", None)
    vid = getattr(vendor_ref, "value", None) if vendor_ref else None
    vname = getattr(vendor_ref, "name", None) if vendor_ref else None
    pay_type = getattr(bp, "PayType", "") or ""
    bank_account = ""
    if pay_type == "Check":
        chk = getattr(bp, "CheckPayment", None)
        ba_ref = getattr(chk, "BankAccountRef", None) if chk else None
        bank_account = getattr(ba_ref, "name", "") if ba_ref else ""
    elif pay_type == "CreditCard":
        cc = getattr(bp, "CreditCardPayment", None)
        cc_ref = getattr(cc, "CCAccountRef", None) if cc else None
        bank_account = getattr(cc_ref, "name", "") if cc_ref else ""
    return {
        "id": str(bp.Id),
        "doc_number": getattr(bp, "DocNumber", "") or "",
        "vendor_qbo_id": str(vid) if vid else None,
        "vendor_name": vname or "",
        "txn_date": _date_str(getattr(bp, "TxnDate", None)),
        "total": float(getattr(bp, "TotalAmt", 0) or 0),
        "pay_type": pay_type,
        "bank_account": bank_account,
        "private_note": getattr(bp, "PrivateNote", "") or "",
        "linked_bill_ids": _linked_bill_ids(bp),
    }


def _get_mock_qbo_bill_payments() -> list[dict]:
    return [
        {
            "id": "BP-9001", "doc_number": "1001",
            "vendor_qbo_id": "V-300",
            "vendor_name": "PAI Industries",
            "txn_date": "2026-01-12",
            "total": 1500.00,
            "pay_type": "Check",
            "bank_account": "Checking",
            "private_note": "Partial payment",
            "linked_bill_ids": ["BILL-6001"],
        },
    ]


def fetch_qbo_bill_payments(
    vendor_qbo_id: str | None = None,
) -> list[dict]:
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_bill_payments()
        if vendor_qbo_id:
            return [r for r in rows
                    if r["vendor_qbo_id"] == str(vendor_qbo_id)]
        return rows
    try:
        from quickbooks.objects.billpayment import BillPayment
        from qbo._throttle import throttle
        throttle()
        if vendor_qbo_id:
            rows = BillPayment.filter(
                VendorRef=str(vendor_qbo_id),
                qb=client._client, max_results=500,
            )
        else:
            rows = BillPayment.all(qb=client._client, max_results=1000)
        return [_bp_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_bill_payments failed: %s", exc)
        return []


def _resolve_local_vendor_id(conn, qbo_vendor_id) -> int | None:
    if not qbo_vendor_id:
        return None
    row = conn.execute(
        "SELECT id FROM vendors WHERE qbo_vendor_id = %s LIMIT 1",
        (str(qbo_vendor_id),),
    ).fetchone()
    if not row:
        return None
    return row[0] if not hasattr(row, "keys") else row["id"]


def _po_for_bill(conn, qbo_bill_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM purchase_orders WHERE qbo_bill_id = %s LIMIT 1",
        (str(qbo_bill_id),),
    ).fetchone()
    if not row:
        return None
    return row[0] if not hasattr(row, "keys") else row["id"]


def import_qbo_bill_payments_to_db(
    *,
    vendor_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Upsert QBO BillPayments into ``bill_payments``.

    Side effect (AP reconciliation): when a payment's linked Bill maps
    to a local PO via ``purchase_orders.qbo_bill_id``, the PO's
    ``bill_balance`` is decremented by the payment amount (clamped at 0)
    so the PO list reflects what's actually outstanding.

    Returns ``{total, created, updated, skipped, po_reconciled, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_bill_payments(vendor_qbo_id=vendor_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "updated": 0, "skipped": 0,
        "po_reconciled": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, bp in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(
                    i, total, bp.get("doc_number") or bp["id"]
                )

            qbo_bp_id = str(bp["id"])
            vendor_id = _resolve_local_vendor_id(
                conn, bp.get("vendor_qbo_id")
            )

            # Resolve linked PO via first linked Bill that has a local match.
            linked_po_id: int | None = None
            for bill_id in bp.get("linked_bill_ids") or []:
                pid = _po_for_bill(conn, bill_id)
                if pid is not None:
                    linked_po_id = pid
                    break

            try:
                row = conn.execute(
                    "SELECT id FROM bill_payments "
                    "WHERE qbo_bill_payment_id = %s LIMIT 1",
                    (qbo_bp_id,),
                ).fetchone()

                memo = (bp.get("private_note") or "")[:500]
                if row:
                    local_id = row[0] if not hasattr(row, "keys") else row["id"]
                    conn.execute(
                        """UPDATE bill_payments SET
                              vendor_id           = COALESCE(vendor_id, %s),
                              purchase_order_id   = COALESCE(
                                  purchase_order_id, %s
                              ),
                              payment_date        = COALESCE(payment_date, %s),
                              amount              = %s,
                              pay_type            = COALESCE(
                                  NULLIF(pay_type,''), %s
                              ),
                              bank_account        = COALESCE(
                                  NULLIF(bank_account,''), %s
                              ),
                              reference           = COALESCE(
                                  NULLIF(reference,''), %s
                              ),
                              notes               = COALESCE(
                                  NULLIF(notes,''), %s
                              ),
                              qbo_last_synced_at  = %s,
                              qbo_pulled_at       = %s,
                              updated_at          = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (vendor_id, linked_po_id, bp["txn_date"],
                         bp["total"], bp["pay_type"], bp["bank_account"],
                         bp.get("doc_number") or "", memo, now_iso, now_iso,
                         local_id),
                    )
                    result["updated"] += 1
                else:
                    conn.execute(
                        """INSERT INTO bill_payments (
                                vendor_id, purchase_order_id,
                                payment_date, amount,
                                payment_method, pay_type, bank_account,
                                reference, notes, status,
                                qbo_bill_payment_id,
                                qbo_last_synced_at, qbo_pulled_at
                           ) VALUES (
                                %s, %s,
                                %s, %s,
                                %s, %s, %s,
                                %s, %s, 'posted',
                                %s,
                                %s, %s
                           )""",
                        (vendor_id, linked_po_id,
                         bp["txn_date"], bp["total"],
                         bp["pay_type"], bp["pay_type"], bp["bank_account"],
                         bp.get("doc_number") or "", memo,
                         qbo_bp_id, now_iso, now_iso),
                    )
                    result["created"] += 1

                # Reconcile PO bill_balance.
                if linked_po_id and bp["total"] > 0:
                    cur = conn.execute(
                        "SELECT bill_balance FROM purchase_orders "
                        "WHERE id = %s", (linked_po_id,),
                    ).fetchone()
                    if cur is not None:
                        bal = (
                            cur["bill_balance"] if hasattr(cur, "keys")
                            else cur[0]
                        )
                        try:
                            current_bal = float(bal or 0)
                        except (TypeError, ValueError):
                            current_bal = 0.0
                        new_bal = max(0.0, current_bal - float(bp["total"]))
                        conn.execute(
                            "UPDATE purchase_orders "
                            "SET bill_balance = %s, qbo_bill_pulled_at = %s "
                            "WHERE id = %s",
                            (new_bal, now_iso, linked_po_id),
                        )
                        result["po_reconciled"] += 1
            except Exception as exc:
                result["errors"].append(
                    f"{bp.get('doc_number') or qbo_bp_id}: {exc}"
                )
        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_bill_payments_to_db: %d created, %d updated, "
        "%d po-reconciled, %d errors",
        result["created"], result["updated"],
        result["po_reconciled"], len(result["errors"]),
    )
    return result


__all__ = [
    "fetch_qbo_bill_payments",
    "import_qbo_bill_payments_to_db",
]
