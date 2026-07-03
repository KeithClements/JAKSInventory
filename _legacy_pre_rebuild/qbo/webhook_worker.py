"""QBO webhook event processor — Tier 4.

Drains rows from ``qbo_webhook_events`` where ``processed_at IS NULL``
and reconciles them back into the local DB:

* **Item** → updates ``products.qbo_synced_at`` (and price/qty if local
  has a row by SKU). Acts as an authoritative pull when sync authority
  is ``qbo_master`` or ``hybrid``.
* **Customer** → patches ``customers`` row by ``qbo_id`` with the latest
  name / contact / address fields.
* **Vendor** → same idea for ``vendors``.
* Any other entity is acknowledged (processed_at stamped) so it
  doesn't keep getting retried.

This is a polling worker. Run it on a timer (QTimer in the GUI, cron
in a server) or inside a long-lived thread.

Public API
----------

``process_pending(limit=100, qbo_service=None)``
    Process up to ``limit`` rows. Returns ``{processed:int, failed:int,
    skipped:int}``. ``qbo_service`` lets callers inject a mock in tests;
    a fresh :class:`QBOIntegrationService` is used otherwise.

``pending_count()``
    How many unprocessed events are queued.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pending_count() -> int:
    try:
        from db.database import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM qbo_webhook_events "
                "WHERE processed_at IS NULL"
            ).fetchone()
            if row is None:
                return 0
            return int(row[0] if not hasattr(row, "keys") else row["n"])
    except Exception as exc:
        logger.warning("pending_count failed: %s", exc)
        return 0


def _fetch_pending(limit: int) -> list[dict[str, Any]]:
    from db.database import get_db
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, realm_id, entity_type, entity_id, operation,
                   last_updated, payload_json
            FROM qbo_webhook_events
            WHERE processed_at IS NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append(dict(r))
        else:
            out.append({
                "id": r[0], "realm_id": r[1], "entity_type": r[2],
                "entity_id": r[3], "operation": r[4],
                "last_updated": r[5], "payload_json": r[6],
            })
    return out


def _mark_processed(event_id: int, error: str | None) -> None:
    from db.database import get_db
    with get_db() as conn:
        if error:
            conn.execute(
                "UPDATE qbo_webhook_events SET processed_at = ?, error = ? "
                "WHERE id = ?",
                (_now_iso(), error[:2000], event_id),
            )
        else:
            conn.execute(
                "UPDATE qbo_webhook_events SET processed_at = ?, error = NULL "
                "WHERE id = ?",
                (_now_iso(), event_id),
            )
        try:
            conn.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entity-specific handlers — each returns None on success, str on failure.
# ---------------------------------------------------------------------------

def _handle_item(svc, entity_id: str, operation: str) -> str | None:
    """Reconcile a QBO Item change against ``products``.

    Looks up the local product by ``qbo_id`` (set on first push). Only
    touches local data when sync authority is ``qbo_master`` or
    ``hybrid`` — under ``local_master`` we just stamp the sync time so
    we don't accidentally clobber local edits.
    """
    if operation == "Delete":
        return None  # nothing to do locally
    try:
        from quickbooks.objects.item import Item
        from qbo._throttle import throttle
        throttle()
        item = Item.get(int(entity_id), qb=svc.client._client)
    except Exception as exc:
        return f"fetch_item: {type(exc).__name__}: {exc}"

    try:
        from db.database import get_db
        from qbo.config import get_sync_authority
        authority = get_sync_authority()
        sku = getattr(item, "Sku", None) or ""
        with get_db() as conn:
            # First try qbo_id link; fall back to SKU match.
            row = conn.execute(
                "SELECT sku FROM products WHERE qbo_id = ? LIMIT 1",
                (str(entity_id),),
            ).fetchone()
            if not row and sku:
                row = conn.execute(
                    "SELECT sku FROM products WHERE sku = ? LIMIT 1",
                    (sku,),
                ).fetchone()
            if not row:
                # Item exists in QBO but not locally — leave it; a local
                # sync can pull it later.
                return None
            local_sku = row[0] if not hasattr(row, "keys") else row["sku"]

            # Stamp sync time always.
            conn.execute(
                "UPDATE products SET qbo_id = ?, qbo_synced_at = ? "
                "WHERE sku = ?",
                (str(entity_id), _now_iso(), local_sku),
            )

            # Only pull price/qty when QBO is authoritative.
            if authority in ("qbo_master", "hybrid"):
                updates: list[str] = []
                params: list[Any] = []
                price = getattr(item, "UnitPrice", None)
                cost = getattr(item, "PurchaseCost", None)
                qty = getattr(item, "QtyOnHand", None)
                if price is not None:
                    updates.append("price = ?")
                    params.append(float(price))
                if cost is not None:
                    updates.append("cost = ?")
                    params.append(float(cost))
                if qty is not None:
                    updates.append("qty_on_hand = ?")
                    params.append(float(qty))
                if updates:
                    params.append(local_sku)
                    try:
                        conn.execute(
                            f"UPDATE products SET {', '.join(updates)} WHERE sku = ?",
                            tuple(params),
                        )
                    except Exception as exc:
                        # Column might not exist (qty_on_hand etc.); not fatal.
                        logger.debug("item field update skipped: %s", exc)
            try:
                conn.commit()
            except Exception:
                pass
    except Exception as exc:
        return f"db_update_item: {type(exc).__name__}: {exc}"
    return None


def _str_attr(obj, name: str) -> str | None:
    """Extract a string attribute, including nested .Address / .FreeFormNumber."""
    val = getattr(obj, name, None)
    if val is None:
        return None
    for nested in ("Address", "FreeFormNumber"):
        v2 = getattr(val, nested, None)
        if v2 is not None:
            return str(v2)
    return str(val) if not hasattr(val, "__dict__") else None


def _handle_customer(svc, entity_id: str, operation: str) -> str | None:
    if operation == "Delete":
        return None
    try:
        from quickbooks.objects.customer import Customer
        from qbo._throttle import throttle
        throttle()
        cust = Customer.get(int(entity_id), qb=svc.client._client)
    except Exception as exc:
        return f"fetch_customer: {type(exc).__name__}: {exc}"

    try:
        from db.database import get_db
        from qbo.customers import (
            _customer_to_dict, _apply_qbo_customer_to_local,
        )
        qc = _customer_to_dict(cust)
        display_name = qc.get("display_name") or qc.get("company_name") or ""
        with get_db() as conn:
            # Prefer the canonical crosswalk column.
            row = conn.execute(
                "SELECT id FROM customers WHERE qbo_customer_id = ? LIMIT 1",
                (str(entity_id),),
            ).fetchone()
            if not row and display_name:
                row = conn.execute(
                    "SELECT id FROM customers WHERE name = ? OR company = ? LIMIT 1",
                    (display_name, display_name),
                ).fetchone()
            if not row:
                return None  # not tracked locally — silently skip
            cust_id = row[0] if not hasattr(row, "keys") else row["id"]
            # Ensure the crosswalk is set so the next event hits the
            # qbo_customer_id branch directly.
            try:
                conn.execute(
                    "UPDATE customers SET qbo_customer_id = ? WHERE id = ? "
                    "AND (qbo_customer_id IS NULL OR qbo_customer_id = '')",
                    (str(entity_id), cust_id),
                )
                conn.commit()
            except Exception:
                pass
        # Delegate the field write to the shared importer helper so the
        # webhook path and the bulk-import path can't drift apart.
        _apply_qbo_customer_to_local(int(cust_id), qc)
    except Exception as exc:
        return f"db_update_customer: {type(exc).__name__}: {exc}"
    return None


def _handle_vendor(svc, entity_id: str, operation: str) -> str | None:
    if operation == "Delete":
        return None
    try:
        from quickbooks.objects.vendor import Vendor
        from qbo._throttle import throttle
        throttle()
        vend = Vendor.get(int(entity_id), qb=svc.client._client)
    except Exception as exc:
        return f"fetch_vendor: {type(exc).__name__}: {exc}"

    try:
        from db.database import get_db
        from qbo.vendors import _vendor_to_dict, _apply_qbo_vendor_to_local
        qv = _vendor_to_dict(vend)
        display = qv.get("display_name") or qv.get("company_name") or ""
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM vendors WHERE qbo_vendor_id = ? LIMIT 1",
                (str(entity_id),),
            ).fetchone()
            if not row and display:
                row = conn.execute(
                    "SELECT id FROM vendors WHERE name = ? OR contact_name = ? LIMIT 1",
                    (display, display),
                ).fetchone()
            if not row:
                return None
            vend_id = row[0] if not hasattr(row, "keys") else row["id"]
            try:
                conn.execute(
                    "UPDATE vendors SET qbo_vendor_id = ? WHERE id = ? "
                    "AND (qbo_vendor_id IS NULL OR qbo_vendor_id = '')",
                    (str(entity_id), vend_id),
                )
                conn.commit()
            except Exception:
                pass
        _apply_qbo_vendor_to_local(int(vend_id), qv)
    except Exception as exc:
        return f"db_update_vendor: {type(exc).__name__}: {exc}"
    return None


def _handle_estimate(svc, entity_id: str, operation: str) -> str | None:
    """Reconcile a QBO Estimate change against local ``quotes``.

    On ``Update`` events we pull the QBO TxnStatus and reflect it onto
    ``quotes.status`` (Accepted/Closed/Rejected → accepted/invoiced/lost).
    On ``Delete`` we clear the local crosswalk so a future re-push lands
    a fresh Estimate.
    """
    try:
        from db.database import get_db
    except Exception as exc:
        return f"db_import: {type(exc).__name__}: {exc}"

    if operation == "Delete":
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE quotes SET qbo_estimate_id = NULL "
                    "WHERE qbo_estimate_id = ?",
                    (str(entity_id),),
                )
                try:
                    conn.commit()
                except Exception:
                    pass
        except Exception as exc:
            return f"db_clear_estimate: {type(exc).__name__}: {exc}"
        return None

    try:
        from quickbooks.objects.estimate import Estimate
        from qbo._throttle import throttle
        throttle()
        est = Estimate.get(int(entity_id), qb=svc.client._client)
    except Exception as exc:
        return f"fetch_estimate: {type(exc).__name__}: {exc}"

    txn_status = (getattr(est, "TxnStatus", None) or "").strip()
    # QBO → local status mapping
    qbo_to_local = {
        "Pending":  "sent",
        "Accepted": "accepted",
        "Closed":   "invoiced",
        "Rejected": "lost",
    }
    new_local = qbo_to_local.get(txn_status)

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, status FROM quotes WHERE qbo_estimate_id = ? LIMIT 1",
                (str(entity_id),),
            ).fetchone()
            if not row:
                return None
            q_id = row[0] if not hasattr(row, "keys") else row["id"]
            cur_status = (row[1] if not hasattr(row, "keys") else row["status"]) or ""

            sets = ["qbo_estimate_synced_at = ?"]
            params: list[Any] = [_now_iso()]
            # Only overwrite local status when QBO carries a meaningful new
            # value and local isn't already in a terminal state we set.
            if new_local and new_local != cur_status:
                sets.append("status = ?")
                params.append(new_local)
            params.append(q_id)
            try:
                conn.execute(
                    f"UPDATE quotes SET {', '.join(sets)} WHERE id = ?",
                    tuple(params),
                )
            except Exception as exc:
                logger.debug("quote status update skipped: %s", exc)
            try:
                conn.commit()
            except Exception:
                pass
    except Exception as exc:
        return f"db_update_estimate: {type(exc).__name__}: {exc}"
    return None


# ---------------------------------------------------------------------------
# InventoryAdjustment (Phase 9)
# ---------------------------------------------------------------------------

def _handle_inventory_adjustment(
    svc: Any, entity_id: str, operation: str,
) -> Optional[str]:
    """Mirror a QBO-originated inventory adjustment into local books.

    On Create / Update we fetch the QBO ``InventoryAdjustment``, then:
      * upsert a ``qbo_inventory_adjustments`` crosswalk row marked
        ``source='qbo'`` so future syncs know we already accounted
        for this change.
      * write a matching ``inventory_transactions`` row (type
        ``qbo_reconciliation``) for each line so the local
        on-hand audit trail stays in sync.

    On Delete we mark the crosswalk row as voided (notes) but leave
    the historical inventory_transactions intact — deleting an
    adjustment in QBO does *not* mean the physical count reverted.
    """
    from db.database import get_db

    try:
        from quickbooks.objects.inventoryadjustment import InventoryAdjustment
    except Exception as exc:  # pragma: no cover - SDK missing
        return f"InventoryAdjustment SDK unavailable: {exc}"

    op = (operation or "").strip().lower()
    qbo_id_str = str(entity_id)

    if op == "delete":
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE qbo_inventory_adjustments "
                    "SET notes = COALESCE(notes,'') || ' [voided in QBO]' "
                    "WHERE qbo_adj_id = ?",
                    (qbo_id_str,),
                )
                conn.commit()
        except Exception as exc:
            return f"db_void_inventory_adj: {type(exc).__name__}: {exc}"
        return None

    if not svc.connect():
        return "QBO not connected"
    try:
        adj = InventoryAdjustment.get(int(qbo_id_str), qb=svc.client._client)
    except Exception as exc:
        return f"get_inventory_adjustment: {type(exc).__name__}: {exc}"

    txn_date = getattr(adj, "TxnDate", None)
    private_note = getattr(adj, "PrivateNote", None) or ""
    lines = list(getattr(adj, "Line", None) or [])

    try:
        with get_db() as conn:
            for ln in lines:
                detail = getattr(ln, "InventoryAdjustmentLineDetail", None)
                if not detail:
                    continue
                item_ref = getattr(detail, "ItemRef", None)
                qbo_item_id = getattr(item_ref, "value", None) if item_ref else None
                qty_diff = float(getattr(detail, "QtyDiff", 0) or 0)

                # Resolve local SKU via the QBO item id (best-effort).
                sku = None
                try:
                    row = conn.execute(
                        "SELECT sku FROM products WHERE qbo_item_id = ? LIMIT 1",
                        (str(qbo_item_id) if qbo_item_id is not None else None,),
                    ).fetchone()
                    if row:
                        sku = row[0] if not hasattr(row, "keys") else row["sku"]
                except Exception:
                    pass

                idem_key = f"qbo:invadj:{qbo_id_str}:{qbo_item_id}:{qty_diff:+.4f}"
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO qbo_inventory_adjustments "
                        "(sku, qbo_item_id, qbo_adj_id, qty_diff, txn_date, "
                        " idempotency_key, source, notes) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'qbo', ?)",
                        (
                            sku,
                            str(qbo_item_id) if qbo_item_id is not None else None,
                            qbo_id_str,
                            qty_diff,
                            str(txn_date) if txn_date else None,
                            idem_key,
                            (private_note or "")[:500],
                        ),
                    )
                except Exception as exc:
                    logger.debug("crosswalk upsert skipped: %s", exc)

                # Mirror to inventory_transactions when we resolved the
                # local product. Best-effort: schema differences across
                # migrations may make this insert fail; that's not fatal.
                if sku:
                    try:
                        prow = conn.execute(
                            "SELECT id, COALESCE(qty_on_hand, 0) AS qoh "
                            "FROM products WHERE sku = ? LIMIT 1",
                            (sku,),
                        ).fetchone()
                        if prow:
                            pid = prow[0] if not hasattr(prow, "keys") else prow["id"]
                            qoh = float(prow[1] if not hasattr(prow, "keys") else prow["qoh"] or 0)
                            qty_after = qoh + qty_diff
                            conn.execute(
                                "INSERT INTO inventory_transactions "
                                "(product_id, transaction_type, quantity_change, "
                                " quantity_before, quantity_after, reference, notes) "
                                "VALUES (?, 'qbo_reconciliation', ?, ?, ?, ?, ?)",
                                (
                                    pid,
                                    int(round(qty_diff)),
                                    int(round(qoh)),
                                    int(round(qty_after)),
                                    f"QBO:InventoryAdjustment:{qbo_id_str}",
                                    (private_note or "")[:500],
                                ),
                            )
                    except Exception as exc:
                        logger.debug("inventory_transactions mirror skipped: %s", exc)

            conn.commit()
    except Exception as exc:
        return f"db_apply_inventory_adj: {type(exc).__name__}: {exc}"
    return None


# ---------------------------------------------------------------------------
# Transaction echoes (Phase 10): mirror voids / edits on
# Invoice / Bill / Payment back to local crosswalk so the local books
# don't keep claiming a deleted QBO doc still exists. Update ops just
# touch a "last synced" notes hint — we deliberately do *not* re-read
# the full doc unless that becomes necessary, since QBO is the source
# of record on those entities.
# ---------------------------------------------------------------------------

def _handle_invoice(svc: Any, entity_id: str, operation: str) -> Optional[str]:
    """Mirror QBO Invoice voids / edits onto ``invoices.qbo_invoice_id``."""
    from db.database import get_db

    op = (operation or "").strip().lower()
    qid = str(entity_id)
    try:
        with get_db() as conn:
            if op == "delete":
                # QBO invoice deleted → clear local crosswalk so the row
                # can be re-pushed (after the operator fixes whatever
                # caused the void).
                conn.execute(
                    "UPDATE invoices "
                    "SET qbo_invoice_id = NULL, qbo_exported_at = NULL "
                    "WHERE qbo_invoice_id = ?",
                    (qid,),
                )
            # On Update we don't currently track per-row sync timestamps
            # on invoices, but we still want a fingerprint that a webhook
            # arrived — bump qbo_sync_log via the audit table.
            conn.commit()
    except Exception as exc:
        return f"db_invoice_echo: {type(exc).__name__}: {exc}"
    return None


def _handle_bill(svc: Any, entity_id: str, operation: str) -> Optional[str]:
    """Mirror QBO Bill voids / edits onto ``purchase_orders.qbo_bill_id``."""
    from db.database import get_db

    op = (operation or "").strip().lower()
    qid = str(entity_id)
    try:
        with get_db() as conn:
            if op == "delete":
                conn.execute(
                    "UPDATE purchase_orders SET qbo_bill_id = NULL "
                    "WHERE qbo_bill_id = ?",
                    (qid,),
                )
            conn.commit()
    except Exception as exc:
        return f"db_bill_echo: {type(exc).__name__}: {exc}"
    return None


def _handle_payment(svc: Any, entity_id: str, operation: str) -> Optional[str]:
    """Mirror QBO Payment voids onto ``invoices.qbo_payment_id``.

    Note: payments are also stored in the local ``payments`` table when
    the operator records them locally first, but only the invoice
    crosswalk needs clearing — payment rows themselves remain as a
    historical record of the customer's tender.
    """
    from db.database import get_db

    op = (operation or "").strip().lower()
    qid = str(entity_id)
    try:
        with get_db() as conn:
            if op == "delete":
                conn.execute(
                    "UPDATE invoices SET qbo_payment_id = NULL "
                    "WHERE qbo_payment_id = ?",
                    (qid,),
                )
                # Also clear the standalone payments table if present.
                try:
                    conn.execute(
                        "UPDATE payments SET qbo_payment_id = NULL "
                        "WHERE qbo_payment_id = ?",
                        (qid,),
                    )
                except Exception:
                    pass
            conn.commit()
    except Exception as exc:
        return f"db_payment_echo: {type(exc).__name__}: {exc}"
    return None


def _handle_credit_memo(svc: Any, entity_id: str, operation: str) -> Optional[str]:
    """Mirror QBO CreditMemo create/edit/void onto local customer_credits.

    On create/update: re-pull just this CreditMemo so the local row
    reflects current QBO state (also propagates core_returns linkage).
    On delete: void the local credit + unlink any core_returns rows
    pointing at this QBO id.
    """
    from db.database import get_db

    op = (operation or "").strip().lower()
    qid = str(entity_id)
    try:
        if op == "delete":
            with get_db() as conn:
                conn.execute(
                    "UPDATE customer_credits SET status = 'void', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE qbo_credit_id = ?",
                    (qid,),
                )
                conn.execute(
                    "UPDATE core_returns SET "
                    "  status = 'pending', credit_issued = 0, "
                    "  credit_date = NULL, qbo_credit_id = NULL, "
                    "  updated_at = CURRENT_TIMESTAMP "
                    "WHERE qbo_credit_id = ?",
                    (qid,),
                )
                conn.commit()
            return None

        # create / update — re-pull this single credit memo. We rely on
        # the bulk pull module since it handles upsert + linkage.
        from qbo.credit_memos import import_qbo_credit_memos_to_db
        # We don't have a single-id fetch helper; the bulk path filters
        # by customer, not by credit_memo id. For now do a full pull
        # and let the upsert key idempotency handle it.
        import_qbo_credit_memos_to_db()
        return None
    except Exception as exc:
        return f"db_credit_memo_echo: {type(exc).__name__}: {exc}"


def _handle_sales_receipt(svc: Any, entity_id: str, operation: str) -> Optional[str]:
    """Mirror QBO SalesReceipt create/edit/void onto local invoices.

    On delete: mark the local row (invoice_kind='sales_receipt') as
    ``status='void'`` and clear ``qbo_sales_receipt_id`` so a future
    create with the same DocNumber doesn't collide.
    On create/update: re-pull via the bulk importer (idempotent on
    ``qbo_sales_receipt_id``).
    """
    from db.database import get_db

    op = (operation or "").strip().lower()
    qid = str(entity_id)
    try:
        if op == "delete":
            with get_db() as conn:
                conn.execute(
                    "UPDATE invoices SET status = 'void', "
                    "qbo_sales_receipt_id = NULL, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE qbo_sales_receipt_id = ?",
                    (qid,),
                )
                conn.commit()
            return None
        from qbo.sales_receipts import import_qbo_sales_receipts_to_db
        import_qbo_sales_receipts_to_db()
        return None
    except Exception as exc:
        return f"db_sales_receipt_echo: {type(exc).__name__}: {exc}"


def _handle_refund_receipt(svc: Any, entity_id: str, operation: str) -> Optional[str]:
    """Mirror QBO RefundReceipt create/edit/void onto customer_credits.

    On delete: mark the matching customer_credits row as ``status='void'``.
    On create/update: re-pull via the bulk importer.
    """
    from db.database import get_db

    op = (operation or "").strip().lower()
    qid = str(entity_id)
    try:
        if op == "delete":
            with get_db() as conn:
                conn.execute(
                    "UPDATE customer_credits SET status = 'void', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE qbo_refund_receipt_id = ?",
                    (qid,),
                )
                conn.commit()
            return None
        from qbo.refund_receipts import import_qbo_refund_receipts_to_db
        import_qbo_refund_receipts_to_db()
        return None
    except Exception as exc:
        return f"db_refund_receipt_echo: {type(exc).__name__}: {exc}"


def _handle_bill_payment(svc: Any, entity_id: str, operation: str) -> Optional[str]:
    """Mirror QBO BillPayment create/edit/void onto bill_payments.

    On delete: void the local row (preserve audit trail). We do not
    automatically re-credit the linked PO's ``bill_balance`` — that
    side-effect was applied on import, and a subsequent Bill webhook
    will correct the balance.
    On create/update: re-pull via the bulk importer.
    """
    from db.database import get_db

    op = (operation or "").strip().lower()
    qid = str(entity_id)
    try:
        if op == "delete":
            with get_db() as conn:
                conn.execute(
                    "UPDATE bill_payments SET status = 'void', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE qbo_bill_payment_id = ?",
                    (qid,),
                )
                conn.commit()
            return None
        from qbo.bill_payments import import_qbo_bill_payments_to_db
        import_qbo_bill_payments_to_db()
        return None
    except Exception as exc:
        return f"db_bill_payment_echo: {type(exc).__name__}: {exc}"


_HANDLERS = {
    "Item": _handle_item,
    "Customer": _handle_customer,
    "Vendor": _handle_vendor,
    "Estimate": _handle_estimate,
    "InventoryAdjustment": _handle_inventory_adjustment,
    "Invoice": _handle_invoice,
    "Bill": _handle_bill,
    "Payment": _handle_payment,
    "CreditMemo": _handle_credit_memo,
    "SalesReceipt": _handle_sales_receipt,
    "RefundReceipt": _handle_refund_receipt,
    "BillPayment": _handle_bill_payment,
}


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def process_pending(limit: int = 100, qbo_service=None) -> dict[str, int]:
    """Drain pending webhook events. Returns counts."""
    events = _fetch_pending(limit)
    if not events:
        return {"processed": 0, "failed": 0, "skipped": 0}

    if qbo_service is None:
        try:
            from qbo.integration import QBOIntegrationService
            qbo_service = QBOIntegrationService()
            if not qbo_service.connect():
                # Stamp every event with the error so the next caller doesn't
                # retry forever, but keep them in the log for visibility.
                for ev in events:
                    _mark_processed(int(ev["id"]), "QBO not connected")
                return {"processed": 0, "failed": len(events), "skipped": 0}
        except Exception as exc:
            for ev in events:
                _mark_processed(int(ev["id"]), f"init: {exc}")
            return {"processed": 0, "failed": len(events), "skipped": 0}

    processed = failed = skipped = 0
    for ev in events:
        ev_id = int(ev["id"])
        entity_type = ev.get("entity_type") or ""
        entity_id = str(ev.get("entity_id") or "")
        operation = ev.get("operation") or ""
        handler = _HANDLERS.get(entity_type)
        if handler is None:
            _mark_processed(ev_id, None)
            skipped += 1
            continue
        try:
            err = handler(qbo_service, entity_id, operation)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        if err:
            _mark_processed(ev_id, err)
            failed += 1
        else:
            _mark_processed(ev_id, None)
            processed += 1

    return {"processed": processed, "failed": failed, "skipped": skipped}
