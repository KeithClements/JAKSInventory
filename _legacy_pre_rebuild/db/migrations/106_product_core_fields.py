"""Migration 106: Extend ``products`` with full core-charge metadata.

Backs the new left-panel "CORE CHARGE" card in the Product Edit dialog.
Adds explicit flags + metadata so the UI is no longer inferring core
state from ``core_charge > 0``.

Columns added (all nullable, safe defaults):
    - has_core                              INTEGER DEFAULT 0
    - core_return_days                      INTEGER DEFAULT 30
    - track_customer_core_return            INTEGER DEFAULT 1
    - track_vendor_core_credit              INTEGER DEFAULT 1
    - include_core_in_advertised_price      INTEGER DEFAULT 0
    - core_notes                            TEXT DEFAULT ''

Backfills ``has_core = 1`` for any existing product whose
``core_charge`` or ``core_sell_price`` is greater than zero, so that
historical rows light up the new card on first edit.
"""
from __future__ import annotations


_NEW_COLUMNS = [
    ("has_core", "INTEGER DEFAULT 0"),
    ("core_return_days", "INTEGER DEFAULT 30"),
    ("track_customer_core_return", "INTEGER DEFAULT 1"),
    ("track_vendor_core_credit", "INTEGER DEFAULT 1"),
    ("include_core_in_advertised_price", "INTEGER DEFAULT 0"),
    ("core_notes", "TEXT DEFAULT ''"),
]


def migrate(conn) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
    for name, decl in _NEW_COLUMNS:
        if name in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE products ADD COLUMN {name} {decl}")
        except Exception:
            # Best-effort: another runner may have raced us, or the column
            # may have been added out-of-band. Continue with the rest.
            pass

    # Backfill has_core for legacy rows that already had a core charge.
    try:
        conn.execute(
            "UPDATE products SET has_core = 1 "
            "WHERE COALESCE(has_core, 0) = 0 "
            "AND (COALESCE(core_charge, 0) > 0 OR COALESCE(core_sell_price, 0) > 0)"
        )
    except Exception:
        pass

    conn.commit()
