"""Recompute line ETAs for open quotes / sales orders.

Run manually with::

    python -m jaks_inventory.scripts.refresh_etas

or schedule nightly. Also called from notification_service before sending
quote / SO emails so the customer-facing ETA is always current.
"""
from __future__ import annotations


def refresh_open_quote_etas() -> dict:
    """Recompute eta_date for every line on every open quote.

    "Open" = status in {draft, sent, needs_follow_up}. Returns a small
    summary dict for logging.
    """
    from db.database import get_db
    from engine.eta import compute_line_eta

    updated = 0
    scanned = 0
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT ql.id, ql.product_id, ql.qty
            FROM quote_lines ql
            JOIN quotes q ON q.id = ql.quote_id
            WHERE COALESCE(q.status, 'draft') IN ('draft','sent','needs_follow_up')
              AND ql.product_id IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            scanned += 1
            if hasattr(row, "keys"):
                lid, pid, qty = row["id"], row["product_id"], row["qty"]
            else:
                lid, pid, qty = row[0], row[1], row[2]
            try:
                new_eta = compute_line_eta(int(pid), int(qty or 1)).get("eta_date")
            except Exception:
                continue
            if new_eta:
                conn.execute(
                    "UPDATE quote_lines SET eta_date = %s WHERE id = %s",
                    (new_eta, lid),
                )
                updated += 1
        conn.commit()
    return {"scanned": scanned, "updated": updated, "scope": "quotes"}


def refresh_open_so_etas() -> dict:
    """Recompute eta_date for every line on every open sales order."""
    from db.database import get_db
    from engine.eta import compute_line_eta

    updated = 0
    scanned = 0
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT sol.id, sol.product_id, sol.quantity_ordered
            FROM sales_order_lines sol
            JOIN sales_orders so ON so.id = sol.so_id
            WHERE COALESCE(so.status, '') NOT IN ('shipped','closed','cancelled','invoiced')
              AND sol.product_id IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            scanned += 1
            if hasattr(row, "keys"):
                lid, pid, qty = row["id"], row["product_id"], row["quantity_ordered"]
            else:
                lid, pid, qty = row[0], row[1], row[2]
            try:
                new_eta = compute_line_eta(int(pid), int(qty or 1)).get("eta_date")
            except Exception:
                continue
            if new_eta:
                conn.execute(
                    "UPDATE sales_order_lines SET eta_date = %s WHERE id = %s",
                    (new_eta, lid),
                )
                updated += 1
        conn.commit()
    return {"scanned": scanned, "updated": updated, "scope": "sales_orders"}


def refresh_quote_etas(quote_id: int) -> int:
    """Recompute ETAs for a single quote (used pre-send)."""
    from db.database import get_db
    from engine.eta import compute_line_eta

    updated = 0
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, product_id, qty FROM quote_lines "
            "WHERE quote_id = %s AND product_id IS NOT NULL",
            (quote_id,),
        ).fetchall()
        for row in rows:
            if hasattr(row, "keys"):
                lid, pid, qty = row["id"], row["product_id"], row["qty"]
            else:
                lid, pid, qty = row[0], row[1], row[2]
            try:
                new_eta = compute_line_eta(int(pid), int(qty or 1)).get("eta_date")
            except Exception:
                continue
            if new_eta:
                conn.execute(
                    "UPDATE quote_lines SET eta_date = %s WHERE id = %s",
                    (new_eta, lid),
                )
                updated += 1
        conn.commit()
    return updated


def refresh_all() -> dict:
    """Run all nightly refreshers and return combined counts."""
    from engine.quote_lifecycle import expire_overdue_quotes

    q = refresh_open_quote_etas()
    s = refresh_open_so_etas()
    e = expire_overdue_quotes()
    return {
        "quotes_scanned": q["scanned"], "quotes_updated": q["updated"],
        "sos_scanned": s["scanned"], "sos_updated": s["updated"],
        "quotes_expired_scanned": e["scanned"], "quotes_expired": e["expired"],
    }
