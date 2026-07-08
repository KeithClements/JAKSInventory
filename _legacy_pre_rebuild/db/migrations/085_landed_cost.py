"""Migration 085: landed-cost columns for POs (T1.5 / G4).

Adds:
  purchase_orders.duty_amount               REAL DEFAULT 0
  purchase_orders.other_landed_fees         REAL DEFAULT 0
  purchase_orders.freight_allocation_basis  TEXT DEFAULT 'value'
        — one of 'value' | 'weight' | 'qty'
  purchase_orders.landed_cost_allocated_at  TIMESTAMP

  purchase_order_lines.landed_unit_cost     REAL
        — per-unit cost AFTER allocating freight + duty + other fees
  purchase_order_lines.landed_allocation    REAL
        — allocated overhead (freight+duty+other) for this line in total

Landed cost = unit_cost + (allocated overhead / qty_received).
"""
from __future__ import annotations


def _column_exists(conn, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            try:
                name = r[1] if not hasattr(r, "keys") else r["name"]
            except Exception:
                name = r[1]
            if str(name).lower() == col.lower():
                return True
        return False
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, col),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def _add_column(conn, table: str, col: str, decl: str) -> None:
    if _column_exists(conn, table, col):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    except Exception:
        pass


def migrate(conn) -> None:
    _add_column(conn, "purchase_orders", "duty_amount", "REAL DEFAULT 0")
    _add_column(conn, "purchase_orders", "other_landed_fees", "REAL DEFAULT 0")
    _add_column(
        conn, "purchase_orders", "freight_allocation_basis",
        "TEXT DEFAULT 'value'",
    )
    _add_column(
        conn, "purchase_orders", "landed_cost_allocated_at", "TIMESTAMP",
    )
    _add_column(
        conn, "purchase_order_lines", "landed_unit_cost", "REAL",
    )
    _add_column(
        conn, "purchase_order_lines", "landed_allocation", "REAL",
    )
    conn.commit()
