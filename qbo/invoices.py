"""QBO Invoice pull.

Only the **inbound** path lives here — push is handled by
:meth:`qbo.integration.QBOIntegration.create_invoice` and the Sync Center.

Public API
----------
fetch_qbo_invoices(customer_qbo_id=None) — list invoice dicts from QBO
import_qbo_invoices_to_db(...)           — upsert into ``invoices`` table
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
    """Map a QBO Invoice line to a flat dict, or None for non-item lines.

    We only persist SalesItemLine entries (DescriptionOnly, SubTotalLine,
    DiscountLine etc. are ignored — we keep the QBO total as authoritative
    for now). A future enhancement can pull DiscountLine into
    ``invoice_lines.discount_amount``.
    """
    detail_type = getattr(line, "DetailType", "") or ""
    if "SalesItemLine" not in detail_type:
        return None
    detail = getattr(line, "SalesItemLineDetail", None)
    item_ref = getattr(detail, "ItemRef", None) if detail else None
    qbo_item_id = getattr(item_ref, "value", None) if item_ref else None
    qbo_item_name = getattr(item_ref, "name", None) if item_ref else None
    qty = float(getattr(detail, "Qty", 0) or 0) if detail else 0.0
    unit = float(getattr(detail, "UnitPrice", 0) or 0) if detail else 0.0
    amt = float(getattr(line, "Amount", 0) or 0)
    return {
        "qbo_item_id": str(qbo_item_id) if qbo_item_id else None,
        "sku": qbo_item_name or "",
        "description": getattr(line, "Description", "") or "",
        "quantity": qty or (1 if amt else 0),
        "unit_price": unit or (amt / qty if qty else amt),
        "amount": amt,
    }


def _invoice_to_dict(inv) -> dict:
    """Map a QBO Invoice SDK object to a flat dict."""
    cust_ref = getattr(inv, "CustomerRef", None)
    cust_id = getattr(cust_ref, "value", None) if cust_ref else None
    cust_name = getattr(cust_ref, "name", None) if cust_ref else None
    raw_lines = list(getattr(inv, "Line", None) or [])
    lines = [d for d in (_line_to_dict(l) for l in raw_lines) if d]
    return {
        "id": str(inv.Id),
        "doc_number": getattr(inv, "DocNumber", "") or "",
        "customer_qbo_id": str(cust_id) if cust_id else None,
        "customer_name": cust_name or "",
        "txn_date": _date_str(getattr(inv, "TxnDate", None)),
        "due_date": _date_str(getattr(inv, "DueDate", None)),
        "total": float(getattr(inv, "TotalAmt", 0) or 0),
        "balance": float(getattr(inv, "Balance", 0) or 0),
        "private_note": getattr(inv, "PrivateNote", "") or "",
        "customer_memo": (
            getattr(getattr(inv, "CustomerMemo", None), "value", None) or ""
        ),
        "lines": lines,
    }


def _get_mock_qbo_invoices() -> list[dict]:
    return [
        {
            "id": "INV-9001", "doc_number": "1001",
            "customer_qbo_id": "C-100", "customer_name": "Acme Fleet Services",
            "txn_date": "2025-12-01", "due_date": "2025-12-31",
            "total": 1250.00, "balance": 250.00,
            "private_note": "", "customer_memo": "",
            "lines": [
                {"qbo_item_id": "I-200", "sku": "JAKS-001",
                 "description": "Reman Injector", "quantity": 2,
                 "unit_price": 500.0, "amount": 1000.0},
                {"qbo_item_id": "I-201", "sku": "CORE-CHARGE",
                 "description": "Core deposit for JAKS-001", "quantity": 2,
                 "unit_price": 125.0, "amount": 250.0},
            ],
        },
        {
            "id": "INV-9002", "doc_number": "1002",
            "customer_qbo_id": "C-101", "customer_name": "Big Rig Repair LLC",
            "txn_date": "2025-12-10", "due_date": "2026-01-09",
            "total": 875.50, "balance": 875.50,
            "private_note": "", "customer_memo": "",
            "lines": [
                {"qbo_item_id": "I-202", "sku": "JAKS-002",
                 "description": "Turbocharger Rebuild", "quantity": 1,
                 "unit_price": 875.50, "amount": 875.50},
            ],
        },
    ]


def fetch_qbo_invoices(customer_qbo_id: str | None = None) -> list[dict]:
    """Fetch invoices from QBO.

    When ``customer_qbo_id`` is given, only invoices belonging to that
    customer are returned. Returns ``[]`` on any error so callers can
    show "no rows" rather than crashing.
    """
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_invoices()
        if customer_qbo_id:
            return [r for r in rows if r["customer_qbo_id"] == str(customer_qbo_id)]
        return rows
    try:
        from quickbooks.objects.invoice import Invoice
        from qbo._throttle import throttle
        throttle()
        if customer_qbo_id:
            invoices = Invoice.filter(CustomerRef=str(customer_qbo_id),
                                      qb=client._client, max_results=500)
        else:
            invoices = Invoice.all(qb=client._client, max_results=1000)
        return [_invoice_to_dict(i) for i in invoices]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_invoices failed: %s", exc)
        return []


def _resolve_local_customer_id(conn, qbo_customer_id: str | None) -> int | None:
    if not qbo_customer_id:
        return None
    row = conn.execute(
        "SELECT id FROM customers WHERE qbo_customer_id = %s LIMIT 1",
        (str(qbo_customer_id),),
    ).fetchone()
    if not row:
        return None
    return row[0] if not hasattr(row, "keys") else row["id"]


def _status_for(balance: float, total: float) -> str:
    """Translate QBO balance/total into a local invoice status."""
    if total <= 0:
        return "void"
    if balance <= 0.005:
        return "paid"
    if balance < total:
        return "partial"
    return "open"


def import_qbo_invoices_to_db(
    *,
    customer_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Upsert QBO invoices into the local ``invoices`` table.

    Identification key: ``invoices.qbo_invoice_id`` (set on push, also
    set here on first inbound import). For invoices that don't yet
    have a local row, we INSERT with ``source = 'qbo'`` so the UI can
    distinguish them from locally-authored invoices.

    Returns ``{total, created, updated, skipped, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_invoices(customer_qbo_id=customer_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "updated": 0, "skipped": 0,
        "lines_synced": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, qi in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(i, total, qi.get("doc_number") or qi["id"])

            qbo_invoice_id = str(qi["id"])
            customer_id = _resolve_local_customer_id(conn, qi.get("customer_qbo_id"))

            # Find existing row by qbo_invoice_id, then by doc_number as
            # fallback (handles invoices created locally before being
            # pushed). DocNumber maps to invoice_number on the local side.
            row = conn.execute(
                "SELECT id, invoice_number FROM invoices WHERE qbo_invoice_id = %s LIMIT 1",
                (qbo_invoice_id,),
            ).fetchone()
            doc_no = qi.get("doc_number") or ""
            if not row and doc_no:
                row = conn.execute(
                    "SELECT id, invoice_number FROM invoices WHERE invoice_number = %s LIMIT 1",
                    (doc_no,),
                ).fetchone()

            status = _status_for(qi["balance"], qi["total"])

            try:
                local_id: int | None = None
                existing_source: str | None = None
                if row:
                    local_id = row[0] if not hasattr(row, "keys") else row["id"]
                    # Re-read source so we know whether to overwrite lines.
                    src_row = conn.execute(
                        "SELECT source FROM invoices WHERE id = %s",
                        (local_id,),
                    ).fetchone()
                    if src_row:
                        existing_source = (
                            src_row["source"] if hasattr(src_row, "keys") else src_row[0]
                        ) or ""
                    conn.execute(
                        """UPDATE invoices SET
                              qbo_invoice_id = %s,
                              qbo_balance    = %s,
                              total          = COALESCE(NULLIF(total, 0), %s),
                              balance_due    = %s,
                              status         = CASE
                                  WHEN status IN ('draft','void') THEN status
                                  ELSE %s
                              END,
                              invoice_date   = COALESCE(invoice_date, %s),
                              due_date       = COALESCE(due_date, %s),
                              source         = COALESCE(NULLIF(source,''), 'qbo'),
                              qbo_last_pulled_at = %s,
                              updated_at     = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (qbo_invoice_id, qi["balance"], qi["total"],
                         qi["balance"], status,
                         qi["txn_date"], qi["due_date"], now_iso, local_id),
                    )
                    result["updated"] += 1
                else:
                    # Create a stub local row marked source='qbo'. The
                    # invoice_number column is UNIQUE NOT NULL so we
                    # fall back to "QBO-<id>" when DocNumber is missing.
                    inv_number = doc_no or f"QBO-{qbo_invoice_id}"
                    conn.execute(
                        """INSERT INTO invoices (
                                invoice_number, customer_id, status,
                                invoice_date, due_date,
                                total, balance_due,
                                qbo_invoice_id, qbo_balance,
                                source, qbo_last_pulled_at,
                                notes
                           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'qbo', %s, %s)""",
                        (inv_number, customer_id, status,
                         qi["txn_date"], qi["due_date"],
                         qi["total"], qi["balance"],
                         qbo_invoice_id, qi["balance"],
                         now_iso,
                         (qi.get("customer_memo") or "")[:1000]),
                    )
                    local_id = conn.lastrowid
                    existing_source = "qbo"
                    result["created"] += 1

                # Sync lines only when the invoice is owned by QBO
                # (source='qbo' or first-time import). Locally-authored
                # invoices keep their lines authoritative.
                qbo_lines = qi.get("lines") or []
                if local_id and qbo_lines and (existing_source or "") == "qbo":
                    conn.execute(
                        "DELETE FROM invoice_lines WHERE invoice_id = %s",
                        (local_id,),
                    )
                    for ln in qbo_lines:
                        sku = (ln.get("sku") or "")[:64]
                        # Map SKU \u2192 local product_id when known.
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
                        line_kind = "core" if sku.upper() == "CORE-CHARGE" else "product"
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
                    f"{qi.get('doc_number') or qbo_invoice_id}: {exc}"
                )
        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_invoices_to_db: %d created, %d updated, %d errors",
        result["created"], result["updated"], len(result["errors"]),
    )
    return result


__all__ = [
    "fetch_qbo_invoices",
    "import_qbo_invoices_to_db",
]
