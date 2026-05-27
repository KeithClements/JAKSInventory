"""Migration 108: Core Handling Overhaul (PO + Receiving + Accounting).

Establishes product-level core type, vendor override, parent/child linkage on
purchase order lines, and a dedicated vendor_core_obligations tracking table.

Schema additions
----------------
products
    core_type TEXT DEFAULT 'none'
        none | refundable_deposit | exchange_required | included_in_price
        Product-level default. Vendors may override via vendors.core_handling.

vendors
    core_handling TEXT (nullable)
        separate_line | included_in_price | conditional_exchange
        When set, overrides the product's core_type behavior on POs for that
        vendor. NULL means use the product default.

purchase_order_lines
    parent_line_id INTEGER
        Self-FK. Set on auto-generated "Refundable Core Deposit" lines that
        belong to a product line. NULL on top-level lines.
    line_kind   — existing column; we now use the value 'core' for the
                  auto-generated refundable-core line.

vendor_core_obligations (new table)
    Authoritative ledger of vendor core liabilities created at PO time.
    Links PO line → RGA → credit. Lifecycle:
        pending → awaiting_return → rga_created → shipped
                → vendor_received → credited | rejected | written_off
"""
from __future__ import annotations


def _existing_cols(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_col(conn, table: str, name: str, decl: str) -> None:
    if name in _existing_cols(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    except Exception:
        pass


def migrate(conn) -> None:
    # ── products.core_type ───────────────────────────────────────
    _add_col(conn, "products", "core_type", "TEXT DEFAULT 'none'")
    # Backfill: products with non-zero core_charge or has_core=1 default to
    # 'refundable_deposit' (matches historical behavior).
    try:
        conn.execute(
            """
            UPDATE products
               SET core_type = 'refundable_deposit'
             WHERE (core_type IS NULL OR core_type = '' OR core_type = 'none')
               AND (COALESCE(core_charge, 0) > 0 OR COALESCE(has_core, 0) = 1)
            """
        )
    except Exception:
        pass

    # ── vendors.core_handling (nullable override) ────────────────
    _add_col(conn, "vendors", "core_handling", "TEXT")

    # ── purchase_order_lines.parent_line_id ──────────────────────
    _add_col(conn, "purchase_order_lines", "parent_line_id", "INTEGER")
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pol_parent ON purchase_order_lines(parent_line_id)"
        )
    except Exception:
        pass

    # ── vendor_core_obligations (new) ────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_core_obligations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_id INTEGER REFERENCES purchase_orders(id) ON DELETE SET NULL,
            po_line_id INTEGER REFERENCES purchase_order_lines(id) ON DELETE SET NULL,
            vendor_id INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            core_amount REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
                -- pending | awaiting_return | rga_created | shipped
                -- | vendor_received | credited | rejected | written_off
            rga_id INTEGER REFERENCES vendor_returns(id) ON DELETE SET NULL,
            tracking_number TEXT,
            credit_received REAL DEFAULT 0,
            credit_date TIMESTAMP,
            vendor_credit_number TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_vco_po ON vendor_core_obligations(po_id)",
        "CREATE INDEX IF NOT EXISTS idx_vco_vendor ON vendor_core_obligations(vendor_id)",
        "CREATE INDEX IF NOT EXISTS idx_vco_status ON vendor_core_obligations(status)",
        "CREATE INDEX IF NOT EXISTS idx_vco_rga ON vendor_core_obligations(rga_id)",
    ):
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    conn.commit()
