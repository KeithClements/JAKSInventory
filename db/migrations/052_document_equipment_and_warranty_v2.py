"""Migration 052: Document-level equipment (ESN + Engine OR Truck) and
per-line warranty v2 (months, type, mileage limit).

Adds to quotes / sales_orders / invoices:
  - equipment_type      TEXT   ('engine' | 'truck' | NULL)
  - equipment_make      TEXT
  - equipment_model     TEXT
  - esn                 TEXT   (already exists on quotes; added to SO + invoice)
  - vin                 TEXT   (already exists on quotes; added to SO + invoice)

Adds to quote_lines / sales_order_lines / invoice_lines:
  - warranty_months         INTEGER DEFAULT 0
  - warranty_type           TEXT    DEFAULT 'parts_only'   ('parts_only' | 'parts_labor')
  - warranty_mileage_limit  INTEGER                         (NULL = Unlimited)

Legacy warranty_years / warranty_price columns are preserved.
"""


def _cols(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add(conn, table, col, typedef):
    if col not in _cols(conn, table):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except Exception:
            pass


def migrate(conn):
    # --- document headers -------------------------------------------------
    header_cols = [
        ("equipment_type", "TEXT"),
        ("equipment_make", "TEXT"),
        ("equipment_model", "TEXT"),
        ("esn", "TEXT"),
        ("vin", "TEXT"),
    ]
    for table in ("quotes", "sales_orders", "invoices"):
        existing = [t[0] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchall()]
        if not existing:
            continue
        for col, typedef in header_cols:
            _add(conn, table, col, typedef)

    # --- line warranty v2 -------------------------------------------------
    line_cols = [
        ("warranty_months", "INTEGER DEFAULT 0"),
        ("warranty_type", "TEXT DEFAULT 'parts_only'"),
        ("warranty_mileage_limit", "INTEGER"),
    ]
    for table in ("quote_lines", "sales_order_lines", "invoice_lines"):
        existing = [t[0] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchall()]
        if not existing:
            continue
        for col, typedef in line_cols:
            _add(conn, table, col, typedef)

    # Back-fill warranty_months from legacy warranty_years where possible.
    for table in ("quote_lines", "invoice_lines"):
        cols = _cols(conn, table)
        if "warranty_months" in cols and "warranty_years" in cols:
            conn.execute(
                f"UPDATE {table} "
                f"SET warranty_months = COALESCE(warranty_years, 0) * 12 "
                f"WHERE (warranty_months IS NULL OR warranty_months = 0) "
                f"  AND warranty_years IS NOT NULL AND warranty_years > 0"
            )

    conn.commit()
