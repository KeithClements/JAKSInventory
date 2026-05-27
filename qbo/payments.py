"""QBO Payment pull.

Push lives in :mod:`qbo.push` (``push_payments_to_qbo``). This module
adds the missing **inbound** path so payments recorded directly inside
QuickBooks Online (over-the-phone credit card runs, payments applied by
the accountant, etc.) flow back into JAKS and update
``invoices.balance_due`` / ``invoices.status``.

Public API
----------
fetch_qbo_payments(customer_qbo_id=None) \u2192 list of dicts
import_qbo_payments_to_db(...)            \u2192 upsert into ``invoice_payments``

Each pulled payment is split across its applied invoices: one
``invoice_payments`` row per LinkedTxn referencing an Invoice. Payments
without any linked invoice (overpayments / unapplied credits) increase
the customer's open credit balance \u2014 those are surfaced via
``customer_credits`` in a future iteration.
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


def _payment_to_dict(pay) -> dict:
    cust_ref = getattr(pay, "CustomerRef", None)
    cust_id = getattr(cust_ref, "value", None) if cust_ref else None
    cust_name = getattr(cust_ref, "name", None) if cust_ref else None
    # Lines: each Line has LinkedTxn[] pointing at applied invoices.
    applied: list[dict] = []
    for line in getattr(pay, "Line", None) or []:
        line_amt = float(getattr(line, "Amount", 0) or 0)
        for lt in getattr(line, "LinkedTxn", None) or []:
            if getattr(lt, "TxnType", "") == "Invoice":
                applied.append({
                    "qbo_invoice_id": str(getattr(lt, "TxnId", "")),
                    "amount": line_amt,
                })
    return {
        "id": str(pay.Id),
        "customer_qbo_id": str(cust_id) if cust_id else None,
        "customer_name": cust_name or "",
        "txn_date": _date_str(getattr(pay, "TxnDate", None)),
        "total": float(getattr(pay, "TotalAmt", 0) or 0),
        "unapplied": float(getattr(pay, "UnappliedAmt", 0) or 0),
        "payment_method": (
            getattr(getattr(pay, "PaymentMethodRef", None), "name", None) or ""
        ),
        "reference": getattr(pay, "PaymentRefNum", "") or "",
        "applied": applied,
    }


def _get_mock_qbo_payments() -> list[dict]:
    return [
        {
            "id": "PAY-7001",
            "customer_qbo_id": "C-100",
            "customer_name": "Acme Fleet Services",
            "txn_date": "2025-12-15",
            "total": 1000.0,
            "unapplied": 0.0,
            "payment_method": "Check",
            "reference": "Check #4421",
            "applied": [
                {"qbo_invoice_id": "INV-9001", "amount": 1000.0},
            ],
        },
    ]


def fetch_qbo_payments(customer_qbo_id: str | None = None) -> list[dict]:
    """Fetch payments from QBO (filtered by customer if given)."""
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_payments()
        if customer_qbo_id:
            return [r for r in rows if r["customer_qbo_id"] == str(customer_qbo_id)]
        return rows
    try:
        from quickbooks.objects.payment import Payment
        from qbo._throttle import throttle
        throttle()
        if customer_qbo_id:
            payments = Payment.filter(CustomerRef=str(customer_qbo_id),
                                      qb=client._client, max_results=500)
        else:
            payments = Payment.all(qb=client._client, max_results=1000)
        return [_payment_to_dict(p) for p in payments]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_payments failed: %s", exc)
        return []


def import_qbo_payments_to_db(
    *,
    customer_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Upsert QBO payments into ``invoice_payments`` and reconcile balances.

    Identification key: ``invoice_payments.qbo_payment_id`` (one row per
    applied invoice / per QBO payment combination so a single QBO
    Payment applied across N invoices becomes N rows tagged with the
    same QBO id).

    For each pulled payment we then UPDATE the parent invoice's
    ``amount_paid`` and ``balance_due`` so the open balance matches the
    QBO snapshot.

    Returns ``{total, created, updated, skipped, invoices_reconciled, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_payments(customer_qbo_id=customer_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "updated": 0, "skipped": 0,
        "invoices_reconciled": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        touched_invoices: set[int] = set()
        for i, qp in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(i, total, qp.get("id"))
            qbo_pay_id = str(qp["id"])
            applied = qp.get("applied") or []
            if not applied:
                # Unapplied / customer-credit payments: skip for now.
                result["skipped"] += 1
                continue

            for ap in applied:
                qbo_inv = ap.get("qbo_invoice_id")
                amt = float(ap.get("amount") or 0)
                if not qbo_inv or amt <= 0:
                    continue
                try:
                    inv_row = conn.execute(
                        "SELECT id FROM invoices WHERE qbo_invoice_id = %s LIMIT 1",
                        (str(qbo_inv),),
                    ).fetchone()
                    if not inv_row:
                        result["skipped"] += 1
                        continue
                    local_inv_id = (
                        inv_row["id"] if hasattr(inv_row, "keys") else inv_row[0]
                    )

                    # One invoice_payments row per (qbo_payment_id, invoice_id).
                    existing = conn.execute(
                        "SELECT id FROM invoice_payments "
                        "WHERE qbo_payment_id = %s AND invoice_id = %s LIMIT 1",
                        (qbo_pay_id, local_inv_id),
                    ).fetchone()
                    if existing:
                        ep_id = (
                            existing["id"] if hasattr(existing, "keys") else existing[0]
                        )
                        conn.execute(
                            "UPDATE invoice_payments SET amount = %s, "
                            "payment_method = %s, reference = %s, "
                            "qbo_last_pulled_at = %s "
                            "WHERE id = %s",
                            (amt, qp.get("payment_method") or "",
                             qp.get("reference") or "", now_iso, ep_id),
                        )
                        result["updated"] += 1
                    else:
                        conn.execute(
                            """INSERT INTO invoice_payments (
                                  invoice_id, amount, payment_method,
                                  reference, status,
                                  qbo_payment_id, qbo_last_pulled_at,
                                  source, recorded_by, recorded_at
                               ) VALUES (%s, %s, %s, %s, 'posted',
                                         %s, %s, 'qbo', 'qbo-import',
                                         COALESCE(%s, CURRENT_TIMESTAMP))""",
                            (local_inv_id, amt,
                             qp.get("payment_method") or "",
                             qp.get("reference") or "",
                             qbo_pay_id, now_iso,
                             qp.get("txn_date")),
                        )
                        result["created"] += 1
                    touched_invoices.add(int(local_inv_id))
                except Exception as exc:
                    result["errors"].append(
                        f"PAY {qbo_pay_id}\u2192INV {qbo_inv}: {exc}"
                    )

        # Reconcile each touched invoice: sum posted payments, set
        # amount_paid + balance_due + status.
        for inv_id in touched_invoices:
            try:
                row = conn.execute(
                    "SELECT total, COALESCE(SUM(p.amount), 0) AS paid "
                    "FROM invoices i "
                    "LEFT JOIN invoice_payments p "
                    "  ON p.invoice_id = i.id AND p.status = 'posted' "
                    "WHERE i.id = %s GROUP BY i.id",
                    (inv_id,),
                ).fetchone()
                if not row:
                    continue
                tot = float(row["total"] if hasattr(row, "keys") else row[0] or 0)
                paid = float(row["paid"] if hasattr(row, "keys") else row[1] or 0)
                bal = max(0.0, tot - paid)
                if tot <= 0:
                    new_status = "void"
                elif bal <= 0.005:
                    new_status = "paid"
                elif paid > 0:
                    new_status = "partial"
                else:
                    new_status = "open"
                conn.execute(
                    "UPDATE invoices SET amount_paid = %s, balance_due = %s, "
                    "status = CASE WHEN status IN ('draft','void') THEN status "
                    "              ELSE %s END, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s",
                    (paid, bal, new_status, inv_id),
                )
                result["invoices_reconciled"] += 1
            except Exception as exc:
                result["errors"].append(f"reconcile inv {inv_id}: {exc}")

        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_payments_to_db: %d created, %d updated, "
        "%d invoices reconciled, %d errors",
        result["created"], result["updated"],
        result["invoices_reconciled"], len(result["errors"]),
    )
    return result


__all__ = ["fetch_qbo_payments", "import_qbo_payments_to_db"]
