"""ETA computation for quote / sales-order line items.

Single source of truth for "when can this part ship?". Used by:
- quote_dialog / so_detail_dialog when a line is added or its qty changes
- nightly refresher (engine/eta_refresh.py) for open quotes/SOs
- email/PDF rendering hooks just before send

Result is a small dict so callers can render their own copy:
    {
        "in_stock": bool,            # qoh - reserved >= qty_needed
        "available_qty": int,        # qty_on_hand - qty_reserved (clamped)
        "vendor_id": int | None,
        "vendor_name": str,
        "lead_time_days": int,
        "po_id": int | None,         # nearest open-PO id (if used)
        "po_number": str | None,
        "po_eta_date": str | None,   # ISO date of that PO
        "eta_date": str | None,      # ISO; today if in_stock
        "source": "in_stock" | "incoming_po" | "vendor_lead",
        "eta_text": str,             # short label for UI / docs
    }
"""
from __future__ import annotations

from datetime import date, timedelta


_INSTOCK_TEXT = "Available now"


def _today_iso() -> str:
    return date.today().isoformat()


def _add_days_iso(days: int) -> str:
    return (date.today() + timedelta(days=int(days))).isoformat()


def compute_line_eta(product_id: int | None, qty_needed: int = 1) -> dict:
    """Compute ETA for a quote/SO line.

    Falls back gracefully when DB lookups fail or the product has no
    vendor — never raises.
    """
    result = {
        "in_stock": False,
        "available_qty": 0,
        "vendor_id": None,
        "vendor_name": "",
        "lead_time_days": 0,
        "po_id": None,
        "po_number": None,
        "po_eta_date": None,
        "eta_date": None,
        "source": "vendor_lead",
        "eta_text": "",
    }
    if not product_id:
        return result

    try:
        qty_needed = max(1, int(qty_needed or 1))
    except (TypeError, ValueError):
        qty_needed = 1

    try:
        from db.database import get_db
        from db.inventory import get_setting
    except Exception:
        return result

    try:
        with get_db() as conn:
            # ── 1. Stock check ──
            row = conn.execute(
                "SELECT COALESCE(qty_on_hand,0) AS qoh, "
                "COALESCE(qty_reserved,0) AS reserved, "
                "preferred_vendor_id AS vendor_id "
                "FROM products WHERE id = %s",
                (product_id,),
            ).fetchone()
            if not row:
                return result
            if hasattr(row, "keys"):
                qoh = int(row["qoh"] or 0)
                reserved = int(row["reserved"] or 0)
                vendor_id = row["vendor_id"]
            else:
                qoh = int(row[0] or 0)
                reserved = int(row[1] or 0)
                vendor_id = row[2]
            available = max(0, qoh - reserved)
            result["available_qty"] = available

            if available >= qty_needed:
                result["in_stock"] = True
                result["eta_date"] = _today_iso()
                result["source"] = "in_stock"
                result["eta_text"] = _INSTOCK_TEXT
                return result

            # ── 2. Open PO check ──
            po_row = None
            try:
                po_row = conn.execute(
                    """
                    SELECT po.id, po.po_number, po.expected_date,
                           pol.quantity_ordered, pol.quantity_received
                    FROM purchase_order_lines pol
                    JOIN purchase_orders po ON po.id = pol.po_id
                    WHERE pol.product_id = %s
                      AND COALESCE(pol.quantity_received, 0) < pol.quantity_ordered
                      AND COALESCE(po.status, '') NOT IN ('cancelled', 'closed', 'received')
                      AND po.expected_date IS NOT NULL
                    ORDER BY po.expected_date ASC
                    LIMIT 1
                    """,
                    (product_id,),
                ).fetchone()
            except Exception:
                po_row = None

            if po_row is not None:
                if hasattr(po_row, "keys"):
                    po_id = po_row["id"]
                    po_number = po_row["po_number"]
                    expected = po_row["expected_date"]
                else:
                    po_id, po_number, expected = po_row[0], po_row[1], po_row[2]
                if expected:
                    result["po_id"] = po_id
                    result["po_number"] = po_number
                    result["po_eta_date"] = str(expected)[:10]
                    result["eta_date"] = result["po_eta_date"]
                    result["source"] = "incoming_po"
                    label = f"PO {po_number}" if po_number else "incoming PO"
                    result["eta_text"] = f"ETA ~ {result['po_eta_date']} ({label})"
                    # also include vendor info if available
                    if vendor_id:
                        result["vendor_id"] = vendor_id
                    return result

            # ── 3. Vendor lead time fallback ──
            lead_time = 0
            vendor_name = ""
            if vendor_id:
                try:
                    vrow = conn.execute(
                        "SELECT COALESCE(lead_time_days,0) AS ltd, name "
                        "FROM vendors WHERE id = %s",
                        (vendor_id,),
                    ).fetchone()
                    if vrow:
                        if hasattr(vrow, "keys"):
                            lead_time = int(vrow["ltd"] or 0)
                            vendor_name = vrow["name"] or ""
                        else:
                            lead_time = int(vrow[0] or 0)
                            vendor_name = vrow[1] or ""
                except Exception:
                    pass

            if lead_time <= 0:
                try:
                    lead_time = int(get_setting("default_lead_time_days", "14") or 14)
                except Exception:
                    lead_time = 14

            result["vendor_id"] = vendor_id
            result["vendor_name"] = vendor_name
            result["lead_time_days"] = lead_time
            result["eta_date"] = _add_days_iso(lead_time)
            result["source"] = "vendor_lead"
            # Show only the lead-time tag (no vendor name) on customer-facing docs.
            label_parts = [f"{lead_time}-day lead"]
            result["eta_text"] = f"ETA ~ {result['eta_date']} ({label_parts[0]})"
            return result
    except Exception:
        return result


def format_eta_short(eta: dict) -> str:
    """Compact one-line label for tables / inline note tags."""
    if not eta or not eta.get("eta_date"):
        return ""
    if eta.get("source") == "in_stock":
        return _INSTOCK_TEXT
    return eta.get("eta_text") or f"ETA ~ {eta['eta_date']}"
