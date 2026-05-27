"""QBO SalesReceipt pull (Phase C1).

A QBO SalesReceipt is a cash sale — invoice + payment in one document,
no A/R involved. We persist receipts into the existing ``invoices``
table with ``invoice_kind='sales_receipt'`` as a discriminator and
``qbo_sales_receipt_id`` for idempotency. The same invoice_lines layout
is reused.

Push lives in :meth:`qbo.integration.QBOIntegrationService.create_sales_receipt`
and is wrapped by :func:`qbo.push.push_sales_receipts_to_qbo`.

Public API
----------
fetch_qbo_sales_receipts(customer_qbo_id=None) -> list[dict]
import_qbo_sales_receipts_to_db(...)            -> upsert into invoices
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


def _line_to_dict(line) -> dict | None:
    detail_type = getattr(line, "DetailType", "") or ""
    if "SalesItemLine" not in detail_type:
        return None
    detail = getattr(line, "SalesItemLineDetail", None)
    item_ref = getattr(detail, "ItemRef", None) if detail else None
    qbo_item_name = getattr(item_ref, "name", None) if item_ref else None
    qty = float(getattr(detail, "Qty", 0) or 0) if detail else 0.0
    unit = float(getattr(detail, "UnitPrice", 0) or 0) if detail else 0.0
    amt = float(getattr(line, "Amount", 0) or 0)
    return {
        "sku": (qbo_item_name or "").strip(),
        "description": getattr(line, "Description", "") or "",
        "quantity": qty or (1 if amt else 0),
        "unit_price": unit or (amt / qty if qty else amt),
        "amount": amt,
    }


def _receipt_to_dict(sr) -> dict:
    cust_ref = getattr(sr, "CustomerRef", None)
    cust_id = getattr(cust_ref, "value", None) if cust_ref else None
    cust_name = getattr(cust_ref, "name", None) if cust_ref else None
    dep_ref = getattr(sr, "DepositToAccountRef", None)
    dep_name = getattr(dep_ref, "name", None) if dep_ref else None
    pm_ref = getattr(sr, "PaymentMethodRef", None)
    pm_name = getattr(pm_ref, "name", None) if pm_ref else None
    raw_lines = list(getattr(sr, "Line", None) or [])
    lines = [d for d in (_line_to_dict(l) for l in raw_lines) if d]
    return {
        "id": str(sr.Id),
        "doc_number": getattr(sr, "DocNumber", "") or "",
        "customer_qbo_id": str(cust_id) if cust_id else None,
        "customer_name": cust_name or "",
        "txn_date": _date_str(getattr(sr, "TxnDate", None)),
        "total": float(getattr(sr, "TotalAmt", 0) or 0),
        "payment_method": pm_name or "",
        "deposit_to_account": dep_name or "",
        "private_note": getattr(sr, "PrivateNote", "") or "",
        "lines": lines,
    }


def _get_mock_qbo_sales_receipts() -> list[dict]:
    return [
        {
            "id": "SR-7001", "doc_number": "SR-1001",
            "customer_qbo_id": "C-100",
            "customer_name": "Acme Fleet Services",
            "txn_date": "2026-01-05",
            "total": 425.00,
            "payment_method": "Cash",
            "deposit_to_account": "Undeposited Funds",
            "private_note": "Counter sale",
            "lines": [
                {"sku": "JAKS-002", "description": "Gasket kit",
                 "quantity": 1, "unit_price": 425.0, "amount": 425.0},
            ],
        },
    ]


def fetch_qbo_sales_receipts(
    customer_qbo_id: str | None = None,
) -> list[dict]:
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_sales_receipts()
        if customer_qbo_id:
            return [r for r in rows
                    if r["customer_qbo_id"] == str(customer_qbo_id)]
        return rows
    try:
        from quickbooks.objects.salesreceipt import SalesReceipt
        from qbo._throttle import throttle
        throttle()
        if customer_qbo_id:
            rows = SalesReceipt.filter(
                CustomerRef=str(customer_qbo_id),
                qb=client._client, max_results=500,
            )
        else:
            rows = SalesReceipt.all(qb=client._client, max_results=1000)
        return [_receipt_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_sales_receipts failed: %s", exc)
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


def import_qbo_sales_receipts_to_db(
    *,
    customer_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Upsert QBO SalesReceipts into ``invoices`` with kind='sales_receipt'.

    A receipt represents money already collected, so the local row is
    written with ``status='paid'``, ``balance_due=0``,
    ``amount_paid=total``. Lines flow into ``invoice_lines`` only on
    create (or when ``source='qbo'``).

    Identification key: ``invoices.qbo_sales_receipt_id``.

    Returns ``{total, created, updated, skipped, lines_synced, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_sales_receipts(customer_qbo_id=customer_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "updated": 0, "skipped": 0,
        "lines_synced": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, sr in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(
                    i, total, sr.get("doc_number") or sr["id"]
                )

            qbo_sr_id = str(sr["id"])
            customer_id = _resolve_local_customer_id(
                conn, sr.get("customer_qbo_id")
            )

            row = conn.execute(
                "SELECT id, source FROM invoices "
                "WHERE qbo_sales_receipt_id = %s LIMIT 1",
                (qbo_sr_id,),
            ).fetchone()

            try:
                local_id: int | None = None
                existing_source: str | None = None
                if row:
                    local_id = row[0] if not hasattr(row, "keys") else row["id"]
                    existing_source = (
                        (row["source"] if hasattr(row, "keys") else row[1])
                        or ""
                    )
                    conn.execute(
                        """UPDATE invoices SET
                              qbo_sales_receipt_id = %s,
                              invoice_kind         = 'sales_receipt',
                              total                = %s,
                              amount_paid          = %s,
                              balance_due          = 0,
                              status               = CASE
                                  WHEN status IN ('draft','void') THEN status
                                  ELSE 'paid'
                              END,
                              invoice_date         = COALESCE(invoice_date, %s),
                              payment_method       = COALESCE(
                                  NULLIF(payment_method,''), %s
                              ),
                              deposit_to_account   = COALESCE(
                                  NULLIF(deposit_to_account,''), %s
                              ),
                              qbo_sales_receipt_pulled_at = %s,
                              updated_at           = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (qbo_sr_id, sr["total"], sr["total"],
                         sr["txn_date"], sr["payment_method"],
                         sr["deposit_to_account"], now_iso, local_id),
                    )
                    result["updated"] += 1
                else:
                    inv_number = (
                        sr.get("doc_number") or f"QBO-SR-{qbo_sr_id}"
                    )
                    # Skip if invoice_number collides with an existing row
                    # (UNIQUE constraint); fall back to QBO-SR-<id>.
                    coll = conn.execute(
                        "SELECT id FROM invoices WHERE invoice_number = %s LIMIT 1",
                        (inv_number,),
                    ).fetchone()
                    if coll:
                        inv_number = f"QBO-SR-{qbo_sr_id}"
                    conn.execute(
                        """INSERT INTO invoices (
                                invoice_number, customer_id, status,
                                invoice_kind, invoice_date,
                                total, amount_paid, balance_due,
                                payment_method, deposit_to_account,
                                qbo_sales_receipt_id,
                                source, qbo_sales_receipt_pulled_at,
                                notes
                           ) VALUES (
                                %s, %s, 'paid',
                                'sales_receipt', %s,
                                %s, %s, 0,
                                %s, %s,
                                %s,
                                'qbo', %s,
                                %s
                           )""",
                        (inv_number, customer_id, sr["txn_date"],
                         sr["total"], sr["total"],
                         sr["payment_method"], sr["deposit_to_account"],
                         qbo_sr_id, now_iso,
                         (sr.get("private_note") or "")[:1000]),
                    )
                    local_id = conn.lastrowid
                    existing_source = "qbo"
                    result["created"] += 1

                # Sync lines only when QBO is authoritative.
                qbo_lines = sr.get("lines") or []
                if (local_id and qbo_lines
                        and (existing_source or "") == "qbo"):
                    conn.execute(
                        "DELETE FROM invoice_lines WHERE invoice_id = %s",
                        (local_id,),
                    )
                    for ln in qbo_lines:
                        sku = (ln.get("sku") or "")[:64]
                        prod_id = None
                        if sku:
                            pr = conn.execute(
                                "SELECT id FROM products WHERE sku = %s LIMIT 1",
                                (sku,),
                            ).fetchone()
                            if pr:
                                prod_id = (
                                    pr["id"] if hasattr(pr, "keys") else pr[0]
                                )
                        line_kind = (
                            "core" if sku.upper() == "CORE-CHARGE"
                            else "product"
                        )
                        conn.execute(
                            """INSERT INTO invoice_lines (
                                    invoice_id, product_id, sku, description,
                                    quantity, unit_price, line_total, line_kind
                               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                            (local_id, prod_id, sku,
                             (ln.get("description") or "")[:500],
                             int(round(float(ln.get("quantity") or 0))) or 1,
                             float(ln.get("unit_price") or 0),
                             float(ln.get("amount") or 0),
                             line_kind),
                        )
                        result["lines_synced"] += 1
            except Exception as exc:
                result["errors"].append(
                    f"{sr.get('doc_number') or qbo_sr_id}: {exc}"
                )
        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_sales_receipts_to_db: %d created, %d updated, "
        "%d errors", result["created"], result["updated"],
        len(result["errors"]),
    )
    return result


__all__ = [
    "fetch_qbo_sales_receipts",
    "import_qbo_sales_receipts_to_db",
]
