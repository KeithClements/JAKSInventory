"""Migration 114: Core Handling Simplification.

Phase A of the core-handling revitalization. Two jobs:

1. **Schema** — give ``invoice_lines`` the same parent/child structure as
   ``purchase_order_lines`` so a CORE child line can point at its parent
   product line.

2. **Status vocabulary** — normalize ``vendor_core_obligations.status`` to
   the new 5-state lifecycle (plus 2 side branches)::

       on_shelf  → awaiting_customer_core → ready_to_ship
                                          → shipped → closed
       (+ write_off, cancelled)

   Legacy values are mapped:
       awaiting_customer_core   →  on_shelf
       pending  (with customer) →  ready_to_ship
       pending  (without)       →  on_shelf   (treat as PO-receipt origin)
       awaiting_return          →  awaiting_customer_core
       rga_created              →  shipped
       received                 →  shipped
       vendor_received          →  shipped
       credited                 →  closed
       rejected                 →  write_off
       written_off              →  write_off

Note: ``products`` already has ``has_core``, ``core_charge``,
``core_sell_price``, ``core_return_days`` from earlier migrations, so no
column additions are needed there. ``products.core_type`` and
``vendors.core_handling`` are left in place but ignored by the new engine
(deprecated, not dropped).
"""
from __future__ import annotations


def _existing_cols(conn, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def migrate(conn) -> None:
    # ── 1) invoice_lines.parent_line_id ──────────────────────────
    if "parent_line_id" not in _existing_cols(conn, "invoice_lines"):
        try:
            conn.execute("ALTER TABLE invoice_lines ADD COLUMN parent_line_id INTEGER")
        except Exception:
            pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoice_line_parent "
            "ON invoice_lines(parent_line_id)"
        )
    except Exception:
        pass

    # ── 2) Status normalization ──────────────────────────────────
    # Order matters: do the customer/pending split before the bulk renames.
    try:
        # pending WITH a customer_core_id => customer returned core, we owe vendor
        conn.execute(
            """
            UPDATE vendor_core_obligations
               SET status = 'ready_to_ship'
             WHERE status = 'pending'
               AND customer_core_id IS NOT NULL
               AND customer_core_id <> 0
               AND (rga_id IS NULL OR rga_id = 0)
            """
        )
        # pending WITHOUT a customer => PO-receipt origin sitting on shelf
        conn.execute(
            """
            UPDATE vendor_core_obligations
               SET status = 'on_shelf'
             WHERE status = 'pending'
               AND (customer_core_id IS NULL OR customer_core_id = 0)
               AND (rga_id IS NULL OR rga_id = 0)
            """
        )
        # straightforward renames
        for old, new in (
            ("awaiting_customer_core", "on_shelf"),
            ("awaiting_return",        "awaiting_customer_core"),
            ("rga_created",            "shipped"),
            ("received",               "shipped"),
            ("vendor_received",        "shipped"),
            ("credited",               "closed"),
            ("rejected",               "write_off"),
            ("written_off",            "write_off"),
        ):
            conn.execute(
                "UPDATE vendor_core_obligations SET status = ? WHERE status = ?",
                (new, old),
            )
        conn.commit()
    except Exception as e:
        print(f"[mig 114] status normalization skipped: {e}")

    # ── 3) Quick audit log ───────────────────────────────────────
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM vendor_core_obligations "
            "GROUP BY status ORDER BY status"
        ).fetchall()
        if rows:
            summary = ", ".join(f"{r[0]}={r[1]}" for r in rows)
            print(f"[mig 114] vendor_core_obligations status counts: {summary}")
    except Exception:
        pass
