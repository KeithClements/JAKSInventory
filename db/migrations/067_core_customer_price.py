"""Migration 067: Track customer-paid core deposit separately from vendor cost.

Adds:
- core_returns.customer_core_price REAL DEFAULT 0
    The amount the customer paid us as a core deposit (per unit).
    This is what we owe back to the customer when they return the core.
    Distinct from core_returns.core_charge, which historically stored the
    vendor deposit (cost we recover from the vendor).

Backfill: when the linked invoice line has a paired ``CORE-<sku>`` line
(``invoice_lines.notes`` contains ``"linked_to:<parent_id>"``), use that
core line's ``unit_price`` as the customer price. Falls back to the
existing ``core_charge`` value otherwise — preserves legacy behavior for
rows that predate the per-unit ``CORE-`` line approach.
"""
from __future__ import annotations


def migrate(conn) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(core_returns)").fetchall()}
    if "customer_core_price" not in cols:
        try:
            conn.execute(
                "ALTER TABLE core_returns ADD COLUMN customer_core_price REAL DEFAULT 0"
            )
        except Exception:
            pass

    # Backfill: prefer the paired CORE- invoice line's unit_price, else core_charge.
    try:
        conn.execute(
            """
            UPDATE core_returns
               SET customer_core_price = COALESCE((
                       SELECT il2.unit_price
                         FROM invoice_lines il2
                        WHERE il2.invoice_id = core_returns.invoice_id
                          AND il2.notes = 'linked_to:' || core_returns.invoice_line_id
                        LIMIT 1
                   ), core_charge)
             WHERE COALESCE(customer_core_price, 0) = 0
            """
        )
    except Exception:
        # Best-effort; if the join fails for any reason, leave column at 0.
        pass

    conn.commit()
