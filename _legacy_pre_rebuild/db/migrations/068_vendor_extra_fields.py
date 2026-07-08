"""Migration 068: Add vendor extension fields + vendor_contacts table.

Adds to ``vendors``:
- ``website TEXT``
- ``account_number TEXT``
- ``tax_id TEXT``
- ``is_1099 BOOLEAN DEFAULT FALSE``
- ``ship_from_address TEXT``
- ``min_order_amount REAL DEFAULT 0``
- ``freight_paid_threshold REAL DEFAULT 0``
- ``return_policy TEXT``
- ``tracking_url_template TEXT``

Creates ``vendor_contacts`` (relational multi-contact storage):
- id, vendor_id, role, name, email, phone, is_primary, notes, created_at

All additions are nullable / defaulted, so existing rows load unchanged.
Idempotent: each ALTER is wrapped in a try/except to skip on dialect errors
(e.g. duplicate-column on re-run).
"""
from __future__ import annotations


_NEW_COLUMNS = [
    ("website", "TEXT"),
    ("account_number", "TEXT"),
    ("tax_id", "TEXT"),
    ("is_1099", "BOOLEAN DEFAULT FALSE"),
    ("ship_from_address", "TEXT"),
    ("min_order_amount", "REAL DEFAULT 0"),
    ("freight_paid_threshold", "REAL DEFAULT 0"),
    ("return_policy", "TEXT"),
    ("tracking_url_template", "TEXT"),
]


def _existing_columns(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if rows:
            return {r[1] for r in rows}
    except Exception:
        pass
    # Postgres fallback
    try:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        ).fetchall()
        return {(r[0] if not hasattr(r, "keys") else r["column_name"]) for r in rows}
    except Exception:
        return set()


def migrate(conn) -> None:
    cols = _existing_columns(conn, "vendors")
    for name, ddl in _NEW_COLUMNS:
        if name in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE vendors ADD COLUMN {name} {ddl}")
        except Exception:
            # Column likely already exists under a different dialect's
            # casing, or this DB doesn't support the type. Skip.
            pass

    # vendor_contacts table.
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vendor_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
                role TEXT,
                name TEXT,
                email TEXT,
                phone TEXT,
                is_primary BOOLEAN DEFAULT FALSE,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    except Exception:
        # Postgres dialect — try its variant.
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vendor_contacts (
                    id SERIAL PRIMARY KEY,
                    vendor_id INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
                    role TEXT,
                    name TEXT,
                    email TEXT,
                    phone TEXT,
                    is_primary BOOLEAN DEFAULT FALSE,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
        except Exception:
            pass

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vendor_contacts_vendor "
            "ON vendor_contacts(vendor_id)"
        )
    except Exception:
        pass

    try:
        conn.commit()
    except Exception:
        pass
