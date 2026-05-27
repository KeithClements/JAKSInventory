"""Migration 077: ``tax_rates.qbo_tax_code`` column.

Adds an optional QBO-Online tax code per nexus row. When an invoice is
exported to QBO, every taxable line carries the code that matches the
resolved jurisdiction. NULL/empty falls back to ``"TAX"`` (a generic
taxable marker QBO will accept on import).

Mapping the 7 chart-of-accounts references (Inventory Asset, Sales
Income, COGS, Core Liability, AR, AP, Tax Payable) is handled via
``app_settings`` keys read at write time — no schema change is required
for those.

The column is nullable. Existing rows simply get ``NULL`` until the user
edits them via Settings.
"""
from __future__ import annotations


def _has_column(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            name = r[1] if not hasattr(r, "keys") else r["name"]
            if str(name) == column:
                return True
        return False
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def migrate(conn) -> None:
    if not _has_column(conn, "tax_rates", "qbo_tax_code"):
        try:
            conn.execute(
                "ALTER TABLE tax_rates ADD COLUMN qbo_tax_code TEXT"
            )
        except Exception:
            conn.execute(
                "ALTER TABLE tax_rates ADD COLUMN IF NOT EXISTS "
                "qbo_tax_code TEXT"
            )
    conn.commit()
