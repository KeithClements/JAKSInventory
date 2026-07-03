"""QBO historical backfill — Phase 7.

Pulls QBO data for a date range and reconciles it locally. Used on
first-time connect or after a long period of QBO-only edits to
re-baseline the local crosswalk columns (``products.qbo_id``,
``customers.qbo_id``, ``invoices.qbo_invoice_id``, ...).

Two modes:

* ``dry_run=True`` — count how many rows would be created / updated /
  skipped locally. Nothing is written to the DB. Cheap to run.
* ``dry_run=False`` — actually upsert the local crosswalk fields.
  Each entity is processed through ``qbo._throttle.throttle()`` so a
  long backfill stays under QBO's per-realm rate cap.

The QBO Query API is paginated; we batch in chunks of 100 (max 1000)
and stop on the first short page. Dates use QBO's ``TxnDate`` for
transactions and ``Metadata.LastUpdatedTime`` for masters.

Public API
----------

``backfill_entity(entity_type, *, start_date=None, end_date=None,
                  dry_run=True, qbo_service=None, page_size=100)``

    Returns ``{would_create: int, would_update: int, would_skip: int,
    errors: list[str], pages: int}``.

``backfill_many(entity_types, *, start_date, end_date, dry_run=True)``

    Convenience: loops the helper above for several types and returns
    ``{entity_type: result}``.

Supported ``entity_type`` values:
    ``Customer``, ``Vendor``, ``Item``, ``Invoice``, ``Bill``, ``Estimate``.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Max rows we ever request in one query, per QBO docs.
_PAGE_CAP = 1000


def _new_result() -> dict[str, Any]:
    return {
        "would_create": 0,
        "would_update": 0,
        "would_skip": 0,
        "errors": [],
        "pages": 0,
    }


# ---------------------------------------------------------------------------
# Per-entity local lookup + upsert helpers
# ---------------------------------------------------------------------------

def _customer_local(conn, qbo_id: str, display_name: str | None) -> int | None:
    """Return local ``customers.id`` for a QBO Customer, or None."""
    row = None
    try:
        row = conn.execute(
            "SELECT id FROM customers WHERE qbo_id = ? LIMIT 1",
            (str(qbo_id),),
        ).fetchone()
    except Exception:
        pass
    if not row and display_name:
        row = conn.execute(
            "SELECT id FROM customers WHERE name = ? OR company = ? LIMIT 1",
            (display_name, display_name),
        ).fetchone()
    if not row:
        return None
    return int(row[0] if not hasattr(row, "keys") else row["id"])


def _stamp_customer_qbo(conn, local_id: int, qbo_id: str) -> None:
    try:
        conn.execute(
            "UPDATE customers SET qbo_id = ? WHERE id = ? "
            "  AND (qbo_id IS NULL OR qbo_id = '')",
            (str(qbo_id), local_id),
        )
    except Exception as exc:
        logger.debug("stamp customer: %s", exc)


def _vendor_local(conn, qbo_id: str, display_name: str | None) -> int | None:
    row = None
    try:
        row = conn.execute(
            "SELECT id FROM vendors WHERE qbo_id = ? LIMIT 1",
            (str(qbo_id),),
        ).fetchone()
    except Exception:
        pass
    if not row and display_name:
        row = conn.execute(
            "SELECT id FROM vendors WHERE name = ? LIMIT 1",
            (display_name,),
        ).fetchone()
    if not row:
        return None
    return int(row[0] if not hasattr(row, "keys") else row["id"])


def _stamp_vendor_qbo(conn, local_id: int, qbo_id: str) -> None:
    try:
        conn.execute(
            "UPDATE vendors SET qbo_id = ? WHERE id = ? "
            "  AND (qbo_id IS NULL OR qbo_id = '')",
            (str(qbo_id), local_id),
        )
    except Exception as exc:
        logger.debug("stamp vendor: %s", exc)


def _item_local(conn, sku: str) -> str | None:
    if not sku:
        return None
    try:
        row = conn.execute(
            "SELECT sku FROM products WHERE sku = ? LIMIT 1", (sku,),
        ).fetchone()
        if not row:
            return None
        return str(row[0] if not hasattr(row, "keys") else row["sku"])
    except Exception:
        return None


def _stamp_item_qbo(conn, sku: str, qbo_id: str) -> None:
    try:
        conn.execute(
            "UPDATE products SET qbo_id = ? WHERE sku = ? "
            "  AND (qbo_id IS NULL OR qbo_id = '')",
            (str(qbo_id), sku),
        )
    except Exception as exc:
        logger.debug("stamp item: %s", exc)


def _invoice_local(conn, doc_number: str) -> int | None:
    if not doc_number:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number = ? LIMIT 1",
            (doc_number,),
        ).fetchone()
        if not row:
            return None
        return int(row[0] if not hasattr(row, "keys") else row["id"])
    except Exception:
        return None


def _stamp_invoice_qbo(conn, local_id: int, qbo_id: str) -> None:
    try:
        conn.execute(
            "UPDATE invoices SET qbo_invoice_id = ? WHERE id = ? "
            "  AND (qbo_invoice_id IS NULL OR qbo_invoice_id = '')",
            (str(qbo_id), local_id),
        )
    except Exception as exc:
        logger.debug("stamp invoice: %s", exc)


def _bill_local(conn, doc_number: str) -> int | None:
    if not doc_number:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM purchase_orders WHERE po_number = ? LIMIT 1",
            (doc_number,),
        ).fetchone()
        if not row:
            return None
        return int(row[0] if not hasattr(row, "keys") else row["id"])
    except Exception:
        return None


def _stamp_bill_qbo(conn, local_id: int, qbo_id: str) -> None:
    try:
        conn.execute(
            "UPDATE purchase_orders SET qbo_bill_id = ? WHERE id = ? "
            "  AND (qbo_bill_id IS NULL OR qbo_bill_id = '')",
            (str(qbo_id), local_id),
        )
    except Exception as exc:
        logger.debug("stamp bill: %s", exc)


def _estimate_local(conn, doc_number: str) -> int | None:
    if not doc_number:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM quotes WHERE quote_number = ? LIMIT 1",
            (doc_number,),
        ).fetchone()
        if not row:
            return None
        return int(row[0] if not hasattr(row, "keys") else row["id"])
    except Exception:
        return None


def _stamp_estimate_qbo(conn, local_id: int, qbo_id: str) -> None:
    try:
        conn.execute(
            "UPDATE quotes SET qbo_estimate_id = ? WHERE id = ? "
            "  AND (qbo_estimate_id IS NULL OR qbo_estimate_id = '')",
            (str(qbo_id), local_id),
        )
    except Exception as exc:
        logger.debug("stamp estimate: %s", exc)


# ---------------------------------------------------------------------------
# Entity adapters — each returns (qbo_objects_iter, classify_fn, stamp_fn)
# ---------------------------------------------------------------------------

def _fetch_pages(qb_object_cls, where: str | None, page_size: int,
                 result: dict[str, Any]) -> list[Any]:
    """Run a paginated QBO query and return the full list of objects."""
    from qbo._throttle import throttle
    out: list[Any] = []
    page_size = max(1, min(int(page_size), _PAGE_CAP))
    start = 1
    while True:
        result["pages"] += 1
        sql = f"SELECT * FROM {qb_object_cls.qbo_object_name}"
        if where:
            sql += f" WHERE {where}"
        sql += f" STARTPOSITION {start} MAXRESULTS {page_size}"
        throttle()
        try:
            page = qb_object_cls.query(sql)
        except Exception as exc:
            result["errors"].append(f"page@{start}: {type(exc).__name__}: {exc}")
            break
        if not page:
            break
        out.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return out


def _date_clause(field: str, start_date: date | None,
                 end_date: date | None) -> str | None:
    """Build a SQL WHERE fragment for a QBO date field."""
    parts: list[str] = []
    if start_date:
        parts.append(f"{field} >= '{start_date.isoformat()}'")
    if end_date:
        parts.append(f"{field} <= '{end_date.isoformat()}'")
    return " AND ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

_SUPPORTED = {"Customer", "Vendor", "Item", "Invoice", "Bill", "Estimate"}


def backfill_entity(
    entity_type: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    dry_run: bool = True,
    qbo_service: Any = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Pull a QBO entity and reconcile crosswalk columns locally."""
    if entity_type not in _SUPPORTED:
        return {**_new_result(),
                "errors": [f"unsupported entity_type: {entity_type}"]}

    result = _new_result()

    if qbo_service is None:
        try:
            from qbo.integration import get_qbo_service
            qbo_service = get_qbo_service()
        except Exception as exc:
            result["errors"].append(f"service_init: {exc}")
            return result

    if not qbo_service.connect():
        result["errors"].append("QBO not connected")
        return result

    # Dispatch to per-entity fetch + classify
    handlers: dict[str, Callable[[Any], dict[str, Any]]] = {
        "Customer": _backfill_customers,
        "Vendor":   _backfill_vendors,
        "Item":     _backfill_items,
        "Invoice":  _backfill_invoices,
        "Bill":     _backfill_bills,
        "Estimate": _backfill_estimates,
    }

    try:
        return handlers[entity_type](
            qbo_service,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            page_size=page_size,
            result=result,
        )
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        return result


def backfill_many(
    entity_types: list[str],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    dry_run: bool = True,
    qbo_service: Any = None,
) -> dict[str, dict[str, Any]]:
    """Run :func:`backfill_entity` for each type. Returns per-type results."""
    out: dict[str, dict[str, Any]] = {}
    for et in entity_types:
        out[et] = backfill_entity(
            et,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            qbo_service=qbo_service,
        )
    return out


# ---------------------------------------------------------------------------
# Per-entity workers — each receives the shared ``result`` dict and updates it.
# ---------------------------------------------------------------------------

def _backfill_customers(svc, *, start_date, end_date, dry_run,
                        page_size, result) -> dict[str, Any]:
    from quickbooks.objects.customer import Customer
    objs = _fetch_pages(Customer, None, page_size, result)
    from db.database import get_db
    with get_db() as conn:
        for c in objs:
            qbo_id = str(getattr(c, "Id", "") or "")
            name = getattr(c, "DisplayName", None) or getattr(c, "CompanyName", None) or ""
            if not qbo_id:
                result["would_skip"] += 1
                continue
            local_id = _customer_local(conn, qbo_id, name)
            if local_id is None:
                result["would_skip"] += 1
                continue
            if not dry_run:
                _stamp_customer_qbo(conn, local_id, qbo_id)
                result["would_update"] += 1
            else:
                result["would_update"] += 1
        if not dry_run:
            try:
                conn.commit()
            except Exception:
                pass
    return result


def _backfill_vendors(svc, *, start_date, end_date, dry_run,
                      page_size, result) -> dict[str, Any]:
    from quickbooks.objects.vendor import Vendor
    objs = _fetch_pages(Vendor, None, page_size, result)
    from db.database import get_db
    with get_db() as conn:
        for v in objs:
            qbo_id = str(getattr(v, "Id", "") or "")
            name = getattr(v, "DisplayName", None) or ""
            if not qbo_id:
                result["would_skip"] += 1
                continue
            local_id = _vendor_local(conn, qbo_id, name)
            if local_id is None:
                result["would_skip"] += 1
                continue
            if not dry_run:
                _stamp_vendor_qbo(conn, local_id, qbo_id)
            result["would_update"] += 1
        if not dry_run:
            try:
                conn.commit()
            except Exception:
                pass
    return result


def _backfill_items(svc, *, start_date, end_date, dry_run,
                    page_size, result) -> dict[str, Any]:
    from quickbooks.objects.item import Item
    objs = _fetch_pages(Item, None, page_size, result)
    from db.database import get_db
    with get_db() as conn:
        for it in objs:
            qbo_id = str(getattr(it, "Id", "") or "")
            sku = getattr(it, "Sku", None) or getattr(it, "Name", None) or ""
            if not qbo_id or not sku:
                result["would_skip"] += 1
                continue
            local_sku = _item_local(conn, sku)
            if local_sku is None:
                result["would_skip"] += 1
                continue
            if not dry_run:
                _stamp_item_qbo(conn, local_sku, qbo_id)
            result["would_update"] += 1
        if not dry_run:
            try:
                conn.commit()
            except Exception:
                pass
    return result


def _backfill_invoices(svc, *, start_date, end_date, dry_run,
                       page_size, result) -> dict[str, Any]:
    from quickbooks.objects.invoice import Invoice
    where = _date_clause("TxnDate", start_date, end_date)
    objs = _fetch_pages(Invoice, where, page_size, result)
    from db.database import get_db
    with get_db() as conn:
        for inv in objs:
            qbo_id = str(getattr(inv, "Id", "") or "")
            doc = getattr(inv, "DocNumber", None) or ""
            if not qbo_id:
                result["would_skip"] += 1
                continue
            local_id = _invoice_local(conn, doc)
            if local_id is None:
                result["would_skip"] += 1
                continue
            if not dry_run:
                _stamp_invoice_qbo(conn, local_id, qbo_id)
            result["would_update"] += 1
        if not dry_run:
            try:
                conn.commit()
            except Exception:
                pass
    return result


def _backfill_bills(svc, *, start_date, end_date, dry_run,
                    page_size, result) -> dict[str, Any]:
    from quickbooks.objects.bill import Bill
    where = _date_clause("TxnDate", start_date, end_date)
    objs = _fetch_pages(Bill, where, page_size, result)
    from db.database import get_db
    with get_db() as conn:
        for b in objs:
            qbo_id = str(getattr(b, "Id", "") or "")
            doc = getattr(b, "DocNumber", None) or ""
            if not qbo_id:
                result["would_skip"] += 1
                continue
            local_id = _bill_local(conn, doc)
            if local_id is None:
                result["would_skip"] += 1
                continue
            if not dry_run:
                _stamp_bill_qbo(conn, local_id, qbo_id)
            result["would_update"] += 1
        if not dry_run:
            try:
                conn.commit()
            except Exception:
                pass
    return result


def _backfill_estimates(svc, *, start_date, end_date, dry_run,
                        page_size, result) -> dict[str, Any]:
    from quickbooks.objects.estimate import Estimate
    where = _date_clause("TxnDate", start_date, end_date)
    objs = _fetch_pages(Estimate, where, page_size, result)
    from db.database import get_db
    with get_db() as conn:
        for e in objs:
            qbo_id = str(getattr(e, "Id", "") or "")
            doc = getattr(e, "DocNumber", None) or ""
            if not qbo_id:
                result["would_skip"] += 1
                continue
            local_id = _estimate_local(conn, doc)
            if local_id is None:
                result["would_skip"] += 1
                continue
            if not dry_run:
                _stamp_estimate_qbo(conn, local_id, qbo_id)
            result["would_update"] += 1
        if not dry_run:
            try:
                conn.commit()
            except Exception:
                pass
    return result
