"""Core handling resolution + vendor core obligation helpers.

Resolves the effective core behavior for a (product, vendor) pair given
``products.core_type`` and the optional ``vendors.core_handling`` override.
Also exposes CRUD helpers for the ``vendor_core_obligations`` ledger.

Resolution rules
----------------
1. If product.core_type is missing or ``'none'`` → behavior is ``'none'``.
2. Else if vendor.core_handling is set → that value wins (vendor override).
3. Else map product.core_type to a default behavior:
     refundable_deposit  → separate_line
     exchange_required   → conditional_exchange
     included_in_price   → included_in_price

The four behavior values used elsewhere in the system are:
    'none' | 'separate_line' | 'included_in_price' | 'conditional_exchange'
"""
from __future__ import annotations

from typing import Optional

from .database import get_db


PRODUCT_CORE_TYPES: tuple[str, ...] = (
    "none",
    "refundable_deposit",
    "exchange_required",
    "included_in_price",
)

VENDOR_CORE_HANDLING_OPTIONS: tuple[str, ...] = (
    "separate_line",
    "included_in_price",
    "conditional_exchange",
)

BEHAVIOR_VALUES: tuple[str, ...] = (
    "none",
    "separate_line",
    "included_in_price",
    "conditional_exchange",
)

# Default behavior for each product.core_type when no vendor override exists.
_PRODUCT_TYPE_DEFAULT_BEHAVIOR: dict[str, str] = {
    "none": "none",
    "refundable_deposit": "separate_line",
    "exchange_required": "conditional_exchange",
    "included_in_price": "included_in_price",
}


CONDITIONAL_EXCHANGE_NOTE = (
    "Core required if rebuildable exchange not returned."
)

REFUNDABLE_CORE_FOOTER = (
    "Refundable upon return of rebuildable core."
)


def resolve_core_behavior(
    product_id: Optional[int],
    vendor_id: Optional[int] = None,
) -> str:
    """Return one of BEHAVIOR_VALUES for the given (product, vendor)."""
    if not product_id:
        return "none"
    with get_db() as conn:
        prow = conn.execute(
            "SELECT COALESCE(core_type, 'none') AS core_type, "
            "       COALESCE(core_charge, 0) AS core_charge, "
            "       COALESCE(has_core, 0) AS has_core "
            "FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if not prow:
            return "none"
        core_type = str(prow["core_type"] or "none").strip().lower() or "none"
        # Treat legacy products (core_charge > 0, no core_type) as deposits.
        if core_type == "none" and (
            float(prow["core_charge"] or 0) > 0 or int(prow["has_core"] or 0) == 1
        ):
            core_type = "refundable_deposit"
        if core_type == "none":
            return "none"

        vrow = None
        if vendor_id:
            vrow = conn.execute(
                "SELECT core_handling FROM vendors WHERE id = ?",
                (vendor_id,),
            ).fetchone()
        if vrow:
            override = str(vrow["core_handling"] or "").strip().lower()
            if override in VENDOR_CORE_HANDLING_OPTIONS:
                return override

        return _PRODUCT_TYPE_DEFAULT_BEHAVIOR.get(core_type, "none")


def get_core_amount_for_product(product_id: int) -> float:
    """Return the per-unit refundable core deposit amount for a product."""
    if not product_id:
        return 0.0
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(core_charge, 0) AS cc FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if not row:
            return 0.0
        try:
            return float(row["cc"] or 0)
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# vendor_core_obligations helpers
# ---------------------------------------------------------------------------

# New 5-state lifecycle (May 2026 plan) plus side branches.
# Legacy values kept in this tuple so update_vendor_core_obligation does not
# reject historical writes mid-migration; new code should prefer the new
# vocabulary on the left of the mig 114 mapping.
VCO_STATUSES: tuple[str, ...] = (
    # New 5-state vocabulary (Phase G, migration 115).
    "on_shelf",
    "awaiting_customer_core",
    "pending_vendor_ship",
    "shipped",
    "credit_received",
    "write_off",
    "cancelled",
    # Legacy aliases (still accepted on write for back-compat reads).
    "ready_to_ship",   # → pending_vendor_ship
    "closed",          # → credit_received
    "pending",
    "awaiting_return",
    "rga_created",
    "vendor_received",
    "credited",
    "rejected",
    "written_off",
)

VCO_OPEN_STATUSES: tuple[str, ...] = (
    "on_shelf",
    "awaiting_customer_core",
    "pending_vendor_ship",
    "shipped",
    # legacy aliases (still open)
    "ready_to_ship",
    "pending",
    "awaiting_return",
    "rga_created",
    "vendor_received",
)

VCO_CLOSED_STATUSES: tuple[str, ...] = (
    "credit_received",
    "write_off",
    "cancelled",
    # legacy aliases
    "closed",
    "credited",
    "rejected",
    "written_off",
)

# Canonical 5-stage lifecycle for UI rendering (column order on board,
# tile order on overview screen). Keep in sync with the Phase G plan.
VCO_LIFECYCLE: tuple[tuple[str, str], ...] = (
    ("on_shelf",               "On Shelf"),
    ("awaiting_customer_core", "Awaiting Customer Core"),
    ("pending_vendor_ship",    "Pending Ship to Vendor"),
    ("shipped",                "Shipped"),
    ("credit_received",        "Credit Received"),
)

# Legacy-value fold-in: when grouping by status for display, treat each
# legacy value as its modern equivalent.
VCO_STATUS_FOLD: dict[str, str] = {
    "ready_to_ship":   "pending_vendor_ship",
    "closed":          "credit_received",
    "credited":        "credit_received",
    "rga_created":     "shipped",
    "vendor_received": "shipped",
    "awaiting_return": "awaiting_customer_core",
    "pending":         "on_shelf",   # best-effort for pre-mig-114 rows
    "rejected":        "write_off",
    "written_off":     "write_off",
}


def fold_status(status: Optional[str]) -> str:
    """Map any legacy status name onto its canonical modern equivalent."""
    s = (status or "").strip()
    return VCO_STATUS_FOLD.get(s, s)


def create_vendor_core_obligation(
    *,
    po_id: int,
    po_line_id: int,
    vendor_id: Optional[int],
    product_id: Optional[int],
    quantity: int,
    core_amount: float,
    status: str = "pending",
    notes: Optional[str] = None,
) -> int:
    """Insert a vendor_core_obligations row; return new id."""
    if status not in VCO_STATUSES:
        raise ValueError(f"Invalid obligation status: {status}")
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO vendor_core_obligations
                (po_id, po_line_id, vendor_id, product_id, quantity,
                 core_amount, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (po_id, po_line_id, vendor_id, product_id, int(quantity or 0),
             float(core_amount or 0), status, notes),
        )
        new_id = cur.lastrowid
        conn.commit()
        return int(new_id or 0)


def list_vendor_core_obligations(
    *,
    po_id: Optional[int] = None,
    vendor_id: Optional[int] = None,
    status: Optional[str] = None,
    only_open: bool = False,
) -> list[dict]:
    """Return obligations matching filters, newest first."""
    where: list[str] = []
    params: list = []
    if po_id is not None:
        where.append("vco.po_id = ?")
        params.append(po_id)
    if vendor_id is not None:
        where.append("vco.vendor_id = ?")
        params.append(vendor_id)
    if status:
        where.append("vco.status = ?")
        params.append(status)
    if only_open:
        placeholders = ",".join("?" for _ in VCO_CLOSED_STATUSES)
        where.append(f"vco.status NOT IN ({placeholders})")
        params.extend(VCO_CLOSED_STATUSES)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT vco.*,
               v.name AS vendor_name,
               p.sku AS product_sku,
               p.title AS product_title,
               po.po_number AS po_number,
               vr.rga_number AS rga_number,
               pol.quantity_ordered AS po_qty_ordered
          FROM vendor_core_obligations vco
          LEFT JOIN vendors v          ON v.id = vco.vendor_id
          LEFT JOIN products p         ON p.id = vco.product_id
          LEFT JOIN purchase_orders po ON po.id = vco.po_id
          LEFT JOIN vendor_returns vr  ON vr.id = vco.rga_id
          LEFT JOIN purchase_order_lines pol ON pol.id = vco.po_line_id
          {clause}
          ORDER BY vco.id DESC
    """
    with get_db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def update_vendor_core_obligation(
    obligation_id: int,
    *,
    status: Optional[str] = None,
    rga_id: Optional[int] = None,
    tracking_number: Optional[str] = None,
    credit_received: Optional[float] = None,
    credit_date: Optional[str] = None,
    vendor_credit_number: Optional[str] = None,
    notes: Optional[str] = None,
    shipped_date: Optional[str] = None,
    credit_received_date: Optional[str] = None,
    due_date: Optional[str] = None,
    rejected_at: Optional[str] = None,
    written_off_at: Optional[str] = None,
) -> dict:
    """Patch a vendor_core_obligations row; returns {success, error?}."""
    sets: list[str] = []
    params: list = []
    if status is not None:
        if status not in VCO_STATUSES:
            return {"success": False, "error": f"Invalid status: {status}"}
        sets.append("status = ?")
        params.append(status)
    if rga_id is not None:
        sets.append("rga_id = ?"); params.append(rga_id)
    if tracking_number is not None:
        sets.append("tracking_number = ?"); params.append(tracking_number)
    if credit_received is not None:
        sets.append("credit_received = ?"); params.append(float(credit_received))
    if credit_date is not None:
        sets.append("credit_date = ?"); params.append(credit_date)
    if vendor_credit_number is not None:
        sets.append("vendor_credit_number = ?"); params.append(vendor_credit_number)
    if notes is not None:
        sets.append("notes = ?"); params.append(notes)
    if shipped_date is not None:
        sets.append("shipped_date = ?"); params.append(shipped_date)
    if credit_received_date is not None:
        sets.append("credit_received_date = ?"); params.append(credit_received_date)
    if due_date is not None:
        sets.append("due_date = ?"); params.append(due_date)
    if rejected_at is not None:
        sets.append("rejected_at = ?"); params.append(rejected_at)
    if written_off_at is not None:
        sets.append("written_off_at = ?"); params.append(written_off_at)
    if not sets:
        return {"success": True}
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(obligation_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE vendor_core_obligations SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()
    return {"success": True}


def delete_vendor_core_obligations_for_po_line(po_line_id: int) -> int:
    """Hard-delete obligations tied to a PO line (used when line is removed)."""
    if not po_line_id:
        return 0
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM vendor_core_obligations WHERE po_line_id = ?",
            (po_line_id,),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def get_outstanding_vendor_core_amount(vendor_id: Optional[int] = None) -> float:
    """Sum of (core_amount * quantity) - credit_received for open obligations."""
    placeholders = ",".join("?" for _ in VCO_CLOSED_STATUSES)
    where = f"status NOT IN ({placeholders})"
    params: tuple = tuple(VCO_CLOSED_STATUSES)
    if vendor_id is not None:
        where += " AND vendor_id = ?"
        params = params + (vendor_id,)
    with get_db() as conn:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(core_amount * quantity - COALESCE(credit_received, 0)), 0)
              FROM vendor_core_obligations
              WHERE {where}
            """,
            params,
        ).fetchone()
        try:
            return float(row[0] or 0)
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# Customer-return → vendor-obligation trigger
# ---------------------------------------------------------------------------

def create_vendor_core_obligation_from_customer_return(
    core_return_id: int,
) -> dict:
    """Create a ``vendor_core_obligations`` row when a customer core is
    marked received.

    Vendor lineage is resolved by:
        1. Customer's ``core_returns`` row → ``invoice_line_id`` → product_id.
        2. Most recent open core-bearing PO line for that product
           (``purchase_orders.status`` in receivable / received / partial /
           closed states, ``purchase_order_lines.core_charge > 0``).
        3. If no such PO exists → fall back to ``products.preferred_vendor_id``
           and ``products.core_charge`` for the deposit amount.
        4. If neither path resolves a vendor → return
           ``{success: False, error: ..., requires_manual: True}`` so the
           caller can warn the user. The customer credit must NOT block.

    The function is idempotent: if a vendor_core_obligations row already
    references this ``customer_core_id`` it is returned unchanged.

    Returns
    -------
    dict with keys:
        success: bool
        obligation_id: int   (when success)
        vendor_id: int       (when success)
        core_amount: float   (when success)
        source: 'po_line' | 'preferred_vendor'
        error: str           (when not success)
        requires_manual: bool (when no vendor could be resolved)
    """
    if not core_return_id:
        return {"success": False, "error": "core_return_id required"}

    with get_db() as conn:
        # Idempotency guard
        existing = conn.execute(
            "SELECT id, vendor_id, core_amount FROM vendor_core_obligations "
            " WHERE customer_core_id = ? LIMIT 1",
            (core_return_id,),
        ).fetchone()
        if existing:
            d = dict(existing)
            return {
                "success": True,
                "obligation_id": d["id"],
                "vendor_id": d["vendor_id"],
                "core_amount": d["core_amount"],
                "source": "existing",
            }

        cr = conn.execute(
            "SELECT id, invoice_id, invoice_line_id, product_id, "
            "       COALESCE(core_charge, 0) AS core_charge "
            "  FROM core_returns WHERE id = ?",
            (core_return_id,),
        ).fetchone()
        if not cr:
            return {"success": False, "error": f"core_return {core_return_id} not found"}
        cr = dict(cr)
        product_id = cr.get("product_id")
        if not product_id:
            return {
                "success": False,
                "error": "core_return has no product_id; cannot resolve vendor",
                "requires_manual": True,
            }

        # 0. Preferred path: claim an existing on-shelf / awaiting obligation
        # for this product. Those rows were created when JAK's received a
        # core-bearing PO line (status 'on_shelf') or when an invoice with a
        # CORE child line shipped (status 'awaiting_customer_core'). Either
        # way they're waiting for a customer to bring a matching core back.
        # Fulfilling one keeps PO lineage intact and avoids phantom rows.
        awaiting = conn.execute(
            """
            SELECT id, vendor_id, core_amount, po_id, po_line_id
              FROM vendor_core_obligations
             WHERE product_id = ?
               AND status IN ('on_shelf', 'awaiting_customer_core',
                              'pending', 'awaiting_return')
               AND (customer_core_id IS NULL OR customer_core_id = 0)
               AND (rga_id IS NULL OR rga_id = 0)
             ORDER BY COALESCE(due_date, '9999-12-31') ASC, id ASC
             LIMIT 1
            """,
            (product_id,),
        ).fetchone()
        if awaiting:
            a = dict(awaiting)
            conn.execute(
                """
                UPDATE vendor_core_obligations
                   SET status            = 'ready_to_ship',
                       customer_core_id  = ?,
                       updated_at        = CURRENT_TIMESTAMP,
                       notes             = COALESCE(notes, '') ||
                           ' | Fulfilled by customer core return #' || ?
                 WHERE id = ?
                """,
                (core_return_id, core_return_id, a["id"]),
            )
            conn.commit()
            return {
                "success": True,
                "obligation_id": int(a["id"]),
                "vendor_id": a.get("vendor_id"),
                "core_amount": float(a.get("core_amount") or 0),
                "source": "claimed_awaiting",
            }

        # 1. Try to trace a recent open core-bearing PO line for this product.
        po_row = conn.execute(
            """
            SELECT pol.id          AS po_line_id,
                   pol.po_id       AS po_id,
                   pol.core_charge AS pol_core_charge,
                   po.vendor_id    AS vendor_id,
                   po.status       AS po_status
              FROM purchase_order_lines pol
              JOIN purchase_orders po ON po.id = pol.po_id
             WHERE pol.product_id = ?
               AND COALESCE(pol.core_charge, 0) > 0
               AND LOWER(COALESCE(po.status, '')) IN
                   ('sent','confirmed','ordered','partial','received','closed')
             ORDER BY COALESCE(po.received_date, po.expected_date,
                               po.order_date, po.created_at) DESC,
                      po.id DESC
             LIMIT 1
            """,
            (product_id,),
        ).fetchone()

        vendor_id = None
        po_id = None
        po_line_id = None
        core_amount = float(cr.get("core_charge") or 0)
        source = None

        if po_row:
            pr = dict(po_row)
            vendor_id = pr.get("vendor_id")
            po_id = pr.get("po_id")
            po_line_id = pr.get("po_line_id")
            pol_cc = float(pr.get("pol_core_charge") or 0)
            if pol_cc > 0:
                core_amount = pol_cc
            source = "po_line"

        # 2. Fall back to product's preferred vendor.
        if not vendor_id:
            prod = conn.execute(
                "SELECT preferred_vendor_id, "
                "       COALESCE(core_charge, 0) AS core_charge "
                "  FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if prod:
                prod_d = dict(prod)
                vendor_id = prod_d.get("preferred_vendor_id")
                if not core_amount:
                    core_amount = float(prod_d.get("core_charge") or 0)
                source = "preferred_vendor"

        if not vendor_id:
            return {
                "success": False,
                "error": (
                    "No vendor could be resolved for this core "
                    "(no source PO line, no preferred vendor on product)."
                ),
                "requires_manual": True,
            }

        cur = conn.execute(
            """
            INSERT INTO vendor_core_obligations
                (po_id, po_line_id, vendor_id, product_id, quantity,
                 core_amount, status, customer_core_id, due_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, 'ready_to_ship', ?,
                    DATE('now', '+90 days'), ?)
            """,
            (po_id, po_line_id, vendor_id, product_id, 1,
             core_amount, core_return_id,
             f"Auto-created from customer core return #{core_return_id} "
             f"(source: {source})"),
        )
        new_id = cur.lastrowid
        conn.commit()

    return {
        "success": True,
        "obligation_id": int(new_id or 0),
        "vendor_id": vendor_id,
        "core_amount": core_amount,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Lifecycle transition helpers (May 2026 plan)
#
# These provide a single, audit-friendly entry point for moving an obligation
# between the 5 main states + 2 side branches. Callers should prefer these
# over raw ``update_vendor_core_obligation`` so the human-readable status
# names stay consistent across the codebase.
# ---------------------------------------------------------------------------

def transition_to_awaiting_customer_core(
    invoice_id: int, product_id: int, units: int = 1
) -> dict:
    """Customer just bought a product with a refundable core (invoice
    finalized / shipped). Move that many ``on_shelf`` obligations for the
    same product into ``awaiting_customer_core``, stamping the invoice id
    in notes for traceability.

    Returns ``{success, transitioned, obligation_ids, shortfall}``. A
    positive ``shortfall`` means we had fewer on-shelf cores than units
    sold — caller may want to alert the user.
    """
    if not invoice_id or not product_id or units <= 0:
        return {"success": False, "error": "invoice_id, product_id, units required"}
    transitioned: list[int] = []
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id FROM vendor_core_obligations
             WHERE product_id = ?
               AND status = 'on_shelf'
               AND (customer_core_id IS NULL OR customer_core_id = 0)
             ORDER BY COALESCE(due_date, '9999-12-31') ASC, id ASC
             LIMIT ?
            """,
            (product_id, int(units)),
        ).fetchall()
        for r in rows:
            obl_id = r["id"] if hasattr(r, "keys") else r[0]
            conn.execute(
                """
                UPDATE vendor_core_obligations
                   SET status = 'awaiting_customer_core',
                       updated_at = CURRENT_TIMESTAMP,
                       notes = COALESCE(notes, '') ||
                           ' | Sold on invoice #' || ?
                 WHERE id = ?
                """,
                (invoice_id, obl_id),
            )
            transitioned.append(int(obl_id))
        conn.commit()
    return {
        "success": True,
        "transitioned": len(transitioned),
        "obligation_ids": transitioned,
        "shortfall": max(0, int(units) - len(transitioned)),
    }


def transition_to_shipped(
    rga_id: int,
    tracking_number: Optional[str] = None,
    shipped_date: Optional[str] = None,
) -> dict:
    """All obligations attached to an RGA become ``shipped``. Stamps
    tracking number + ship date on each row.
    """
    if not rga_id:
        return {"success": False, "error": "rga_id required"}
    with get_db() as conn:
        sets = ["status = 'shipped'", "updated_at = CURRENT_TIMESTAMP"]
        params: list = []
        if shipped_date:
            sets.append("shipped_date = ?"); params.append(shipped_date)
        else:
            sets.append("shipped_date = COALESCE(shipped_date, CURRENT_TIMESTAMP)")
        if tracking_number:
            sets.append("tracking_number = ?"); params.append(tracking_number)
        params.append(rga_id)
        cur = conn.execute(
            f"UPDATE vendor_core_obligations SET {', '.join(sets)} "
            f" WHERE rga_id = ? AND status NOT IN "
            f"      ('credit_received','closed','write_off','cancelled','credited','rejected','written_off')",
            tuple(params),
        )
        conn.commit()
        return {"success": True, "transitioned": int(cur.rowcount or 0)}


def transition_to_pending_vendor_ship(core_return_id: int) -> dict:
    """When a customer returns a core (vendor_core_returns row received),
    the matching ``awaiting_customer_core`` obligation flips to
    ``pending_vendor_ship`` — i.e. we now physically hold the core and
    just need to box it up and ship to the vendor.

    Lookup strategy: find obligation(s) whose ``customer_core_id`` ties
    back to this core return via the products linkage (best-effort). If
    nothing matches by customer_core_id, fall back to product+vendor
    match on the oldest awaiting row.
    """
    if not core_return_id:
        return {"success": False, "error": "core_return_id required"}
    transitioned: list[int] = []
    with get_db() as conn:
        # Direct join by customer_core_id (when populated upstream).
        rows = conn.execute(
            """
            SELECT id FROM vendor_core_obligations
             WHERE customer_core_id = ?
               AND status = 'awaiting_customer_core'
            """,
            (core_return_id,),
        ).fetchall()
        for r in rows:
            obl_id = r["id"] if hasattr(r, "keys") else r[0]
            conn.execute(
                """
                UPDATE vendor_core_obligations
                   SET status = 'pending_vendor_ship',
                       updated_at = CURRENT_TIMESTAMP,
                       notes = COALESCE(notes, '') ||
                           ' | Customer core received #' || ?
                 WHERE id = ?
                """,
                (core_return_id, obl_id),
            )
            transitioned.append(int(obl_id))
        conn.commit()
    return {"success": True, "transitioned": len(transitioned),
            "obligation_ids": transitioned}


def transition_to_credit_received(
    rga_id: int,
    vendor_credit_number: Optional[str] = None,
    credit_received_date: Optional[str] = None,
) -> dict:
    """Mark obligations attached to an RGA as ``credit_received`` (vendor
    issued credit memo). Stamps credit number + date.
    """
    if not rga_id:
        return {"success": False, "error": "rga_id required"}
    with get_db() as conn:
        sets = ["status = 'credit_received'", "updated_at = CURRENT_TIMESTAMP"]
        params: list = []
        if vendor_credit_number:
            sets.append("vendor_credit_number = ?"); params.append(vendor_credit_number)
        if credit_received_date:
            sets.append("credit_received_date = ?"); params.append(credit_received_date)
        else:
            sets.append(
                "credit_received_date = COALESCE(credit_received_date, CURRENT_TIMESTAMP)"
            )
        params.append(rga_id)
        cur = conn.execute(
            f"UPDATE vendor_core_obligations SET {', '.join(sets)} "
            f" WHERE rga_id = ?",
            tuple(params),
        )
        conn.commit()
        return {"success": True, "transitioned": int(cur.rowcount or 0)}


# Back-compat alias: callers still importing transition_to_closed work.
def transition_to_closed(
    rga_id: int,
    vendor_credit_number: Optional[str] = None,
    credit_received_date: Optional[str] = None,
) -> dict:
    return transition_to_credit_received(
        rga_id,
        vendor_credit_number=vendor_credit_number,
        credit_received_date=credit_received_date,
    )


def transition_to_write_off(obligation_id: int, reason: str = "") -> dict:
    """Manual forfeit — vendor rejected the core, or it was never returned
    by the customer in time. Removes the row from outstanding balance
    rollups but keeps it visible for audit.
    """
    if not obligation_id:
        return {"success": False, "error": "obligation_id required"}
    note_suffix = f" | Written off: {reason}" if reason else " | Written off"
    with get_db() as conn:
        conn.execute(
            """
            UPDATE vendor_core_obligations
               SET status = 'write_off',
                   updated_at = CURRENT_TIMESTAMP,
                   notes = COALESCE(notes, '') || ?
             WHERE id = ?
            """,
            (note_suffix, obligation_id),
        )
        conn.commit()
    return {"success": True}


def transition_to_cancelled(obligation_id: int, reason: str = "") -> dict:
    """Cancel an obligation that should never have existed (e.g. PO line
    deleted, duplicate auto-create). Distinct from ``write_off`` which
    implies the core was real but the credit was lost.
    """
    if not obligation_id:
        return {"success": False, "error": "obligation_id required"}
    note_suffix = f" | Cancelled: {reason}" if reason else " | Cancelled"
    with get_db() as conn:
        conn.execute(
            """
            UPDATE vendor_core_obligations
               SET status = 'cancelled',
                   updated_at = CURRENT_TIMESTAMP,
                   notes = COALESCE(notes, '') || ?
             WHERE id = ?
            """,
            (note_suffix, obligation_id),
        )
        conn.commit()
    return {"success": True}


def rollback_customer_core_claim(core_return_id: int) -> dict:
    """When a customer-product-return reverses a core return, push the
    claimed obligation back to ``on_shelf`` so the inventory deposit is
    re-available to fulfill the next customer return.
    """
    if not core_return_id:
        return {"success": False, "error": "core_return_id required"}
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE vendor_core_obligations
               SET status = 'on_shelf',
                   customer_core_id = NULL,
                   updated_at = CURRENT_TIMESTAMP,
                   notes = COALESCE(notes, '') ||
                       ' | Rolled back from customer return #' || ?
             WHERE customer_core_id = ?
               AND status IN ('pending_vendor_ship', 'ready_to_ship', 'pending')
               AND (rga_id IS NULL OR rga_id = 0)
            """,
            (core_return_id, core_return_id),
        )
        conn.commit()
        return {"success": True, "rolled_back": int(cur.rowcount or 0)}


# ---------------------------------------------------------------------------
# Phase G — Vendor Cores overview / board aggregates.
# ---------------------------------------------------------------------------

def list_vendor_core_summary(*, include_closed: bool = False) -> list[dict]:
    """One row per vendor with bucket counts for the Overview screen.

    Returns rows shaped::

        {
            vendor_id, vendor_name,
            on_shelf, awaiting_customer_core, pending_vendor_ship,
            shipped, credit_received,
            open_total, outstanding_amount, overdue,
        }

    ``open_total`` excludes closed buckets (credit_received / write_off /
    cancelled). ``outstanding_amount`` sums ``deposit_each * units`` for
    open obligations only. ``overdue`` is 1 when any open obligation's
    ``due_date`` is in the past.

    Legacy status values are folded into the canonical 5 buckets so
    pre-migration rows still display correctly.
    """
    # Pull everything in one query, fold legacy values in Python so the
    # SQL stays simple and the fold table remains the single source of
    # truth.
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT vco.vendor_id          AS vendor_id,
                   v.name                 AS vendor_name,
                   vco.status             AS status,
                   COALESCE(vco.core_amount, 0) AS deposit_each,
                   COALESCE(vco.quantity, 1)    AS units,
                   vco.due_date           AS due_date
              FROM vendor_core_obligations vco
              LEFT JOIN vendors v ON v.id = vco.vendor_id
            """
        ).fetchall()

    today = None
    try:
        from datetime import date as _date
        today = _date.today().isoformat()
    except Exception:
        pass

    buckets = ("on_shelf", "awaiting_customer_core",
               "pending_vendor_ship", "shipped", "credit_received")
    closed_buckets = {"credit_received", "write_off", "cancelled"}

    by_vendor: dict[int, dict] = {}
    for r in rows:
        vid = int((r["vendor_id"] if hasattr(r, "keys") else r[0]) or 0)
        if not vid:
            continue
        name = (r["vendor_name"] if hasattr(r, "keys") else r[1]) or "(unknown)"
        status_raw = (r["status"] if hasattr(r, "keys") else r[2]) or ""
        status = fold_status(status_raw)
        deposit = float((r["deposit_each"] if hasattr(r, "keys") else r[3]) or 0)
        units = int((r["units"] if hasattr(r, "keys") else r[4]) or 1)
        due_date = (r["due_date"] if hasattr(r, "keys") else r[5]) or ""

        entry = by_vendor.setdefault(vid, {
            "vendor_id": vid,
            "vendor_name": name,
            "on_shelf": 0,
            "awaiting_customer_core": 0,
            "pending_vendor_ship": 0,
            "shipped": 0,
            "credit_received": 0,
            "write_off": 0,
            "cancelled": 0,
            "open_total": 0,
            "outstanding_amount": 0.0,
            "overdue": 0,
        })

        if status in buckets:
            entry[status] = entry.get(status, 0) + 1
        elif status in closed_buckets:
            entry[status] = entry.get(status, 0) + 1
        # else: unknown status — count under open_total only if obviously open

        if status not in closed_buckets:
            entry["open_total"] += 1
            entry["outstanding_amount"] += deposit * max(units, 1)
            if today and due_date and str(due_date) < today:
                entry["overdue"] = 1

    result = list(by_vendor.values())
    if not include_closed:
        result = [r for r in result if r["open_total"] > 0]
    # Default sort: outstanding $ desc.
    result.sort(key=lambda r: (-r["outstanding_amount"], r["vendor_name"].lower()))
    return result


def next_packing_slip_number() -> str:
    """Allocate the next sequential CRP-XXXX packing-slip number atomically.

    Stored as an integer string in ``app_settings.next_packing_slip_number``;
    we read, increment, write back inside one transaction.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='next_packing_slip_number'"
        ).fetchone()
        try:
            current = int((row["value"] if hasattr(row, "keys") else row[0]) or 1001)
        except Exception:
            current = 1001
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
            ("next_packing_slip_number", str(current + 1)),
        )
        conn.commit()
    return f"CRP-{current:04d}"


def mark_packing_slip_generated(
    vendor_return_id: int,
    *,
    doc_number: str,
    path: str,
) -> dict:
    """Persist the packing-slip metadata on ``vendor_returns``. Versions
    bump on every regenerate so reprints have a clean audit trail.
    """
    if not vendor_return_id:
        return {"success": False, "error": "vendor_return_id required"}
    with get_db() as conn:
        row = conn.execute(
            "SELECT packing_slip_version FROM vendor_returns WHERE id = ?",
            (vendor_return_id,),
        ).fetchone()
        cur_ver = 0
        if row is not None:
            cur_ver = int((row["packing_slip_version"] if hasattr(row, "keys") else row[0]) or 0)
        conn.execute(
            """
            UPDATE vendor_returns
               SET packing_slip_doc_number = ?,
                   packing_slip_path = ?,
                   packing_slip_version = ?,
                   packing_slip_generated_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (doc_number, path, cur_ver + 1, vendor_return_id),
        )
        conn.commit()
    return {"success": True, "version": cur_ver + 1}


def mark_freight_label_generated(
    vendor_return_id: int,
    *,
    path: str,
    carrier: Optional[str] = None,
    tracking: Optional[str] = None,
    cost: Optional[float] = None,
) -> dict:
    """Persist freight-label metadata. Tracking is typically blank at
    generation time (label is generic) and filled in later when the user
    enters the hand-applied carrier sticker number.
    """
    if not vendor_return_id:
        return {"success": False, "error": "vendor_return_id required"}
    with get_db() as conn:
        conn.execute(
            """
            UPDATE vendor_returns
               SET freight_label_path = ?,
                   freight_label_carrier = COALESCE(?, freight_label_carrier),
                   freight_label_tracking = COALESCE(?, freight_label_tracking),
                   freight_label_cost = COALESCE(?, freight_label_cost),
                   freight_label_generated_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (path, carrier, tracking, cost, vendor_return_id),
        )
        conn.commit()
    return {"success": True}


def shipping_gate_status(vendor_return_id: int) -> dict:
    """Return ``{ready_to_ship, missing}`` for the Mark-Shipped gate.

    ``ready_to_ship`` is True iff *both* a packing slip and a freight
    label have been generated. ``missing`` is a human-readable list of
    requirements still outstanding, suitable for a tooltip on the
    disabled button.
    """
    missing: list[str] = []
    if not vendor_return_id:
        return {"ready_to_ship": False, "missing": ["No return selected"]}
    with get_db() as conn:
        row = conn.execute(
            "SELECT packing_slip_path, freight_label_path "
            "  FROM vendor_returns WHERE id = ?",
            (vendor_return_id,),
        ).fetchone()
    if row is None:
        return {"ready_to_ship": False, "missing": ["Return not found"]}
    slip = (row["packing_slip_path"] if hasattr(row, "keys") else row[0]) or ""
    label = (row["freight_label_path"] if hasattr(row, "keys") else row[1]) or ""
    if not slip:
        missing.append("Packing slip PDF not generated")
    if not label:
        missing.append("Freight label PDF not generated")
    return {"ready_to_ship": not missing, "missing": missing}


# ---------------------------------------------------------------------------
# Phase H — Unified Core Processing view.
# ---------------------------------------------------------------------------
#
# A single "core" can exist in three combinations:
#
#   1. customer-only: customer was charged a core deposit on an invoice but
#      we have not yet received the old part back. (core_returns row;
#      vendor_core_obligations row may not exist yet.)
#   2. linked: customer-side row + vendor-side obligation linked via
#      ``vco.customer_core_id = cr.id``. Typical path once the customer
#      returns the core and we now owe the vendor a core in turn.
#   3. vendor-only: warranty/over-shipment cores we owe back to a vendor
#      with no customer counterpart (vendor_core_obligations row only).
#
# ``list_unified_cores`` produces ONE dict per core regardless of which
# combination it falls into, plus a derived ``lifecycle_phase`` that powers
# the 5-column Processing Center kanban.

UNIFIED_PHASES: tuple[tuple[str, str], ...] = (
    ("awaiting_customer", "Awaiting Customer Core"),
    ("received_ready",    "Core Received / Ready to Ship"),
    ("in_rga",            "Vendor RGA / Shipment"),
    ("awaiting_credit",   "Waiting on Vendor Credit"),
    ("closed",            "Closed / Paid"),
)


def _derive_unified_phase(*, cr_status: str | None, vco_status: str | None,
                          vco_rga_id: int | None) -> str | None:
    """Single source of truth for mapping (cr, vco) state → kanban column.

    Returns ``None`` for terminal/special states (void, write_off, cancelled)
    so callers can suppress them from the main kanban while still surfacing
    them in supporting screens.
    """
    cr = (cr_status or "").lower()
    vco = fold_status(vco_status or "")

    # Terminal / excluded states.
    if cr == "void" or vco in ("write_off", "cancelled"):
        return None

    # Vendor side is closed.
    if vco == "credit_received":
        return "closed"
    if vco == "shipped":
        return "awaiting_credit"
    if vco in ("pending_vendor_ship",) and vco_rga_id:
        return "in_rga"
    if vco in ("on_shelf", "awaiting_customer_core", "pending_vendor_ship"):
        # No RGA yet → either awaiting customer return, or received and
        # ready to ship.
        if cr == "pending":
            return "awaiting_customer"
        return "received_ready"

    # No vendor obligation yet — look only at the customer side.
    if cr == "pending":
        return "awaiting_customer"
    if cr in ("received",):
        return "received_ready"
    if cr in ("credited", "closed"):
        return "closed"

    # Unknown combo: surface in the awaiting_customer column so it doesn't
    # silently disappear from operator view.
    return "awaiting_customer"


def list_unified_cores(
    *,
    phase: str | None = None,
    vendor_id: int | None = None,
    customer_id: int | None = None,
    overdue_only: bool = False,
    include_closed: bool = True,
    limit: int = 500,
) -> list[dict]:
    """Return one dict per core combining customer + vendor side state.

    Keys returned per row::

        core_return_id, vco_id, sku, part_number, description,
        customer_id, customer_name, invoice_id, invoice_number,
        cr_status, due_date,
        vendor_id, vendor_name, rga_id, rga_number, vco_status,
        deposit, expected_credit, credit_received, credit_date,
        vendor_credit_number,
        lifecycle_phase, is_overdue, age_days,
        tracking_number, shipped_date,

    Filters are applied after phase derivation. ``include_closed=False``
    drops the ``closed`` bucket which is the common kanban default.
    """
    from datetime import date as _date, datetime as _dt

    with get_db() as conn:
        # FULL OUTER JOIN isn't supported in sqlite, so we union two halves:
        # (a) every core_returns row LEFT JOIN its obligation, and
        # (b) every vendor_core_obligations row with NO customer_core_id.
        rows_cr = conn.execute(
            """
            SELECT cr.id            AS core_return_id,
                   cr.status        AS cr_status,
                   cr.due_date      AS cr_due_date,
                   cr.core_charge   AS cr_charge,
                   cr.customer_id   AS customer_id,
                   cr.invoice_id    AS invoice_id,
                   cr.product_id    AS cr_product_id,
                   cr.created_at    AS cr_created_at,
                   c.name           AS customer_name,
                   inv.invoice_number AS invoice_number,
                   p.sku            AS sku,
                   p.title          AS description,
                   vco.id           AS vco_id,
                   vco.status       AS vco_status,
                   vco.rga_id       AS rga_id,
                   vco.vendor_id    AS vendor_id,
                   vco.product_id   AS vco_product_id,
                   vco.core_amount  AS deposit,
                   vco.quantity     AS quantity,
                   vco.credit_received AS credit_received,
                   vco.credit_date  AS credit_date,
                   vco.vendor_credit_number AS vendor_credit_number,
                   vco.tracking_number AS tracking_number,
                   vco.shipped_date AS shipped_date,
                   vco.due_date     AS vco_due_date,
                   v.name           AS vendor_name,
                   vr.rga_number    AS rga_number
              FROM core_returns cr
              LEFT JOIN vendor_core_obligations vco ON vco.customer_core_id = cr.id
              LEFT JOIN customers c     ON c.id = cr.customer_id
              LEFT JOIN invoices inv    ON inv.id = cr.invoice_id
              LEFT JOIN products p      ON p.id = cr.product_id
              LEFT JOIN vendors v       ON v.id = vco.vendor_id
              LEFT JOIN vendor_returns vr ON vr.id = vco.rga_id
            """
        ).fetchall()

        rows_vco_only = conn.execute(
            """
            SELECT NULL             AS core_return_id,
                   NULL             AS cr_status,
                   NULL             AS cr_due_date,
                   NULL             AS cr_charge,
                   NULL             AS customer_id,
                   NULL             AS invoice_id,
                   NULL             AS cr_product_id,
                   vco.created_at   AS cr_created_at,
                   NULL             AS customer_name,
                   NULL             AS invoice_number,
                   p.sku            AS sku,
                   p.title          AS description,
                   vco.id           AS vco_id,
                   vco.status       AS vco_status,
                   vco.rga_id       AS rga_id,
                   vco.vendor_id    AS vendor_id,
                   vco.product_id   AS vco_product_id,
                   vco.core_amount  AS deposit,
                   vco.quantity     AS quantity,
                   vco.credit_received AS credit_received,
                   vco.credit_date  AS credit_date,
                   vco.vendor_credit_number AS vendor_credit_number,
                   vco.tracking_number AS tracking_number,
                   vco.shipped_date AS shipped_date,
                   vco.due_date     AS vco_due_date,
                   v.name           AS vendor_name,
                   vr.rga_number    AS rga_number
              FROM vendor_core_obligations vco
              LEFT JOIN products p      ON p.id = vco.product_id
              LEFT JOIN vendors v       ON v.id = vco.vendor_id
              LEFT JOIN vendor_returns vr ON vr.id = vco.rga_id
             WHERE vco.customer_core_id IS NULL
                OR vco.customer_core_id = 0
            """
        ).fetchall()

    today_iso = _date.today().isoformat()
    today_dt = _date.today()

    def _row_to_dict(r) -> dict:
        return dict(r) if hasattr(r, "keys") else dict(enumerate(r))

    out: list[dict] = []
    for raw in list(rows_cr) + list(rows_vco_only):
        r = _row_to_dict(raw)
        vco_rga_id = r.get("rga_id")
        phase_key = _derive_unified_phase(
            cr_status=r.get("cr_status"),
            vco_status=r.get("vco_status"),
            vco_rga_id=int(vco_rga_id) if vco_rga_id else None,
        )
        if phase_key is None:
            continue  # void / written_off / cancelled — skip in unified view.

        # Pick the most relevant due_date for overdue calc.
        due = r.get("cr_due_date") or r.get("vco_due_date") or ""
        is_overdue = bool(due and str(due) < today_iso and phase_key in (
            "awaiting_customer", "received_ready", "in_rga",
        ))

        # Age in days since creation.
        age_days = 0
        created_at = r.get("cr_created_at")
        if created_at:
            try:
                ca = _dt.fromisoformat(str(created_at).replace("Z", "").split(".")[0])
                age_days = max(0, (today_dt - ca.date()).days)
            except Exception:
                age_days = 0

        deposit = float(r.get("deposit") or r.get("cr_charge") or 0)
        qty = int(r.get("quantity") or 1)
        expected_credit = deposit * max(qty, 1)

        row = {
            "core_return_id": r.get("core_return_id"),
            "vco_id": r.get("vco_id"),
            "sku": r.get("sku") or "",
            "part_number": r.get("sku") or "",
            "description": r.get("description") or "",
            "customer_id": r.get("customer_id"),
            "customer_name": r.get("customer_name") or "",
            "invoice_id": r.get("invoice_id"),
            "invoice_number": r.get("invoice_number") or "",
            "cr_status": r.get("cr_status") or "",
            "due_date": due,
            "vendor_id": r.get("vendor_id"),
            "vendor_name": r.get("vendor_name") or "",
            "rga_id": r.get("rga_id"),
            "rga_number": r.get("rga_number") or "",
            "vco_status": r.get("vco_status") or "",
            "deposit": deposit,
            "expected_credit": expected_credit,
            "credit_received": float(r.get("credit_received") or 0),
            "credit_date": r.get("credit_date") or "",
            "vendor_credit_number": r.get("vendor_credit_number") or "",
            "tracking_number": r.get("tracking_number") or "",
            "shipped_date": r.get("shipped_date") or "",
            "lifecycle_phase": phase_key,
            "is_overdue": is_overdue,
            "age_days": age_days,
        }

        # Filters.
        if phase and row["lifecycle_phase"] != phase:
            continue
        if vendor_id and (row["vendor_id"] or 0) != vendor_id:
            continue
        if customer_id and (row["customer_id"] or 0) != customer_id:
            continue
        if overdue_only and not row["is_overdue"]:
            continue
        if not include_closed and row["lifecycle_phase"] == "closed":
            continue

        out.append(row)

    # Sort: overdue first within each phase, then newest first.
    phase_rank = {k: i for i, (k, _) in enumerate(UNIFIED_PHASES)}
    out.sort(key=lambda x: (
        phase_rank.get(x["lifecycle_phase"], 99),
        0 if x["is_overdue"] else 1,
        -(x["age_days"] or 0),
    ))
    return out[:limit] if limit else out


def core_processing_kpis() -> dict:
    """Aggregate counts + dollar totals for the Processing Center tiles.

    Returns::

        {
            overdue:           {count, dollars},
            received_ready:    {count, dollars},
            awaiting_credit:   {count, dollars},
            closed_30d:        {count, dollars},
            written_off:       {count, dollars},
        }

    "closed_30d" counts ``credit_received`` obligations with ``credit_date``
    within the last 30 days. "written_off" is lifetime.
    """
    from datetime import date as _date, timedelta as _td

    # Re-use the unified list (cheap on current dataset sizes).
    rows = list_unified_cores(include_closed=True, limit=10_000)

    today = _date.today()
    cutoff = (today - _td(days=30)).isoformat()

    out = {
        "overdue":         {"count": 0, "dollars": 0.0},
        "received_ready":  {"count": 0, "dollars": 0.0},
        "awaiting_credit": {"count": 0, "dollars": 0.0},
        "closed_30d":      {"count": 0, "dollars": 0.0},
        "written_off":     {"count": 0, "dollars": 0.0},
    }

    for r in rows:
        amt = float(r.get("expected_credit") or 0)
        if r["is_overdue"]:
            out["overdue"]["count"] += 1
            out["overdue"]["dollars"] += amt
        if r["lifecycle_phase"] == "received_ready":
            out["received_ready"]["count"] += 1
            out["received_ready"]["dollars"] += amt
        elif r["lifecycle_phase"] == "awaiting_credit":
            out["awaiting_credit"]["count"] += 1
            out["awaiting_credit"]["dollars"] += amt
        elif r["lifecycle_phase"] == "closed":
            cd = str(r.get("credit_date") or "")[:10]
            if cd and cd >= cutoff:
                out["closed_30d"]["count"] += 1
                out["closed_30d"]["dollars"] += float(
                    r.get("credit_received") or amt
                )

    # Written-off requires its own query (not in unified list).
    with get_db() as conn:
        wo = conn.execute(
            """
            SELECT COUNT(*) AS c,
                   COALESCE(SUM(core_amount * COALESCE(quantity, 1)), 0) AS d
              FROM vendor_core_obligations
             WHERE status = 'write_off'
            """
        ).fetchone()
    if wo is not None:
        out["written_off"]["count"] = int(
            (wo["c"] if hasattr(wo, "keys") else wo[0]) or 0
        )
        out["written_off"]["dollars"] = float(
            (wo["d"] if hasattr(wo, "keys") else wo[1]) or 0
        )

    return out

