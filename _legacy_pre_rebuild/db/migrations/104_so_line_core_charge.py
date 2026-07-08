"""Migration 104: Add ``core_charge`` to ``sales_order_lines``.

Mirrors the per-line core column that already exists on ``quote_lines``
(mig 015) and ``invoice_lines`` (mig 036). This closes the snapshot gap
identified in the core architecture review: prior to this migration the
SO row carried no core value, so an SO→Invoice conversion had to re-read
the live product master.

After this migration runs, ``convert_quote_to_so`` snapshots the quote
line's ``core_charge`` onto the SO line, and ``convert_so_to_invoice``
copies that snapshot straight onto the invoice line — guaranteeing that
Quote → SO → Invoice preserves the originally quoted core values even
if ``products.core_sell_price`` is edited later.

The existing ``_add_core_line_for_product`` fallback (which reads the
product master) is preserved as a last-resort path for SOs created
before this migration.
"""
from __future__ import annotations


def migrate(conn) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sales_order_lines)").fetchall()}
    if "core_charge" not in cols:
        try:
            conn.execute(
                "ALTER TABLE sales_order_lines ADD COLUMN core_charge REAL DEFAULT 0"
            )
        except Exception:
            pass
    conn.commit()
