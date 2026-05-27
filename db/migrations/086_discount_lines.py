"""Migration 086: manual discount / fee line support across all documents.

Adds:
  purchase_order_lines.discount_amount  REAL DEFAULT 0
  purchase_order_lines.discount_pct     REAL DEFAULT 0
  purchase_order_lines.line_kind        TEXT DEFAULT 'product'
  purchase_order_lines.sku              TEXT
  purchase_order_lines.description      TEXT

  quote_lines.discount_amount           REAL DEFAULT 0
  quote_lines.discount_pct              REAL DEFAULT 0

  sales_order_lines.discount_amount     REAL DEFAULT 0
  sales_order_lines.discount_pct        REAL DEFAULT 0

  invoice_lines.discount_amount         REAL DEFAULT 0
  invoice_lines.discount_pct            REAL DEFAULT 0

Also: relax `purchase_order_lines.product_id` to NULLABLE so manual
discount / fee / freight lines can be added without a SKU.

SQLite cannot drop NOT NULL via ALTER, so we rebuild the PO lines table
(copy → drop old → rename). Foreign keys and indexes are preserved.

`line_kind` discriminator values in use:
  'product'  — default, real inventory line
  'core'     — core charge line (existing pattern)
  'warranty' — warranty add-on line (existing pattern)
  'fee'      — non-discount fee/freight line
  'discount' — NEW: negative-amount discount line (no product required)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
        return False


def _table_exists(conn, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _add_column(conn, table: str, col: str, decl: str) -> None:
    if not _table_exists(conn, table):
        return
    if _column_exists(conn, table, col):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    except Exception as exc:
        logger.warning("086: ADD COLUMN %s.%s failed: %s", table, col, exc)


def _po_lines_product_id_is_not_null(conn) -> bool:
    """Return True if purchase_order_lines.product_id still has NOT NULL."""
    if not _table_exists(conn, "purchase_order_lines"):
        return False
    try:
        rows = conn.execute(
            "PRAGMA table_info(purchase_order_lines)"
        ).fetchall()
        for r in rows:
            try:
                name = r[1]
                notnull = r[3]
            except Exception:
                name = r["name"]
                notnull = r["notnull"]
            if str(name).lower() == "product_id":
                return bool(notnull)
    except Exception:
        return False
    return False


def _relax_po_lines_product_id(conn) -> None:
    """Rebuild purchase_order_lines to drop NOT NULL on product_id.

    Idempotent: no-op if product_id is already nullable.
    Preserves all existing columns, data, FKs, and indexes.
    """
    if not _po_lines_product_id_is_not_null(conn):
        return

    logger.info("086: rebuilding purchase_order_lines to nullable product_id")

    # Collect existing columns to preserve unknown/added ones.
    info = conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()
    cols: list[tuple[str, str, int, object, int]] = []
    for r in info:
        try:
            name = r[1]; ctype = r[2]; notnull = r[3]; dflt = r[4]; pk = r[5]
        except Exception:
            name = r["name"]; ctype = r["type"]; notnull = r["notnull"]
            dflt = r["dflt_value"]; pk = r["pk"]
        cols.append((str(name), str(ctype or ""), int(notnull), dflt, int(pk)))

    col_names = [c[0] for c in cols]

    # Build CREATE TABLE for the new shape. Force product_id nullable.
    decl_parts: list[str] = []
    for name, ctype, notnull, dflt, pk in cols:
        if name == "id":
            decl_parts.append("id INTEGER PRIMARY KEY AUTOINCREMENT")
            continue
        if name == "po_id":
            decl_parts.append(
                "po_id INTEGER NOT NULL REFERENCES "
                "purchase_orders(id) ON DELETE CASCADE"
            )
            continue
        if name == "product_id":
            # Relaxed: no NOT NULL.
            decl_parts.append(
                "product_id INTEGER REFERENCES products(id)"
            )
            continue
        decl = f"{name} {ctype}".strip()
        if notnull:
            decl += " NOT NULL"
        if dflt is not None:
            decl += f" DEFAULT {dflt}"
        decl_parts.append(decl)

    new_ddl = (
        "CREATE TABLE purchase_order_lines_new (\n  "
        + ",\n  ".join(decl_parts)
        + "\n)"
    )
    conn.execute("DROP TABLE IF EXISTS purchase_order_lines_new")
    conn.execute(new_ddl)

    cols_csv = ", ".join(col_names)
    conn.execute(
        f"INSERT INTO purchase_order_lines_new ({cols_csv}) "
        f"SELECT {cols_csv} FROM purchase_order_lines"
    )
    conn.execute("DROP TABLE purchase_order_lines")
    conn.execute(
        "ALTER TABLE purchase_order_lines_new RENAME TO purchase_order_lines"
    )
    # Recreate indexes (idempotent CREATE IF NOT EXISTS).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_po_line_po "
        "ON purchase_order_lines(po_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_po_line_product "
        "ON purchase_order_lines(product_id)"
    )


def migrate(conn) -> None:
    # 1. Relax PO line product_id NOT NULL (no-op if already nullable).
    _relax_po_lines_product_id(conn)

    # 2. New columns on PO lines (discount + descriptive fields so manual
    #    lines without a product can carry a SKU label and description).
    _add_column(conn, "purchase_order_lines", "discount_amount", "REAL DEFAULT 0")
    _add_column(conn, "purchase_order_lines", "discount_pct", "REAL DEFAULT 0")
    _add_column(conn, "purchase_order_lines", "line_kind", "TEXT DEFAULT 'product'")
    _add_column(conn, "purchase_order_lines", "sku", "TEXT")
    _add_column(conn, "purchase_order_lines", "description", "TEXT")

    # 3. Discount columns on the other three line tables. `line_kind`
    #    was added in migration 070, so we don't need to re-add it.
    for tbl in ("quote_lines", "sales_order_lines", "invoice_lines"):
        _add_column(conn, tbl, "discount_amount", "REAL DEFAULT 0")
        _add_column(conn, tbl, "discount_pct", "REAL DEFAULT 0")
        # Safety net in case a fresh DB hasn't run mig 070 yet.
        _add_column(conn, tbl, "line_kind", "TEXT DEFAULT 'product'")

    conn.commit()
    logger.info("Migration 086 complete: discount-line support")
