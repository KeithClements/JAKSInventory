"""Migration 107: Vendor Core Processing on Vendor Returns (RGA) screen.

Adds the schema necessary for the Vendor Returns / Vendor Cores (RGA) screen
to process cores being shipped back to vendors for credit.

Schema additions
----------------
vendor_returns
    return_type TEXT DEFAULT 'general'
        warranty | damaged | wrong_part | vendor_core | other | general
    rejected_at TIMESTAMP
    written_off_at TIMESTAMP

vendor_return_lines
    vendor_core_return_id INTEGER
        FK → vendor_core_returns(id); set when a line is sourced from a
        pending vendor core.
    source_customer_core_id INTEGER
        FK → core_returns(id); preserves the original customer-side core
        return that produced this vendor obligation.
    source_invoice_id INTEGER
        Originating customer invoice (kept denormalised for audit).
    source_po_id INTEGER
        Originating PO that posted the vendor core deposit.
    vendor_core_deposit REAL DEFAULT 0
        Per-unit deposit JAK's owes back from vendor (was charged on PO).
    expected_credit REAL DEFAULT 0
        Expected credit from vendor for this line (qty * deposit).

vendor_core_returns
    rga_id INTEGER
        FK → vendor_returns(id); set when the row is attached to an RGA.
    customer_core_id INTEGER
        FK → core_returns(id) where applicable (customer source).
    rejected_at TIMESTAMP
    written_off_at TIMESTAMP

vendor_return_audit (new table)
    Audit log of every status transition + edit on an RGA / vendor core row.
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
    # ── vendor_returns extensions ────────────────────────────────
    _add_col(conn, "vendor_returns", "return_type", "TEXT DEFAULT 'general'")
    _add_col(conn, "vendor_returns", "rejected_at", "TIMESTAMP")
    _add_col(conn, "vendor_returns", "written_off_at", "TIMESTAMP")

    # ── vendor_return_lines extensions ───────────────────────────
    _add_col(conn, "vendor_return_lines", "vendor_core_return_id", "INTEGER")
    _add_col(conn, "vendor_return_lines", "source_customer_core_id", "INTEGER")
    _add_col(conn, "vendor_return_lines", "source_invoice_id", "INTEGER")
    _add_col(conn, "vendor_return_lines", "source_po_id", "INTEGER")
    _add_col(conn, "vendor_return_lines", "vendor_core_deposit", "REAL DEFAULT 0")
    _add_col(conn, "vendor_return_lines", "expected_credit", "REAL DEFAULT 0")

    # ── vendor_core_returns extensions ───────────────────────────
    _add_col(conn, "vendor_core_returns", "rga_id", "INTEGER")
    _add_col(conn, "vendor_core_returns", "customer_core_id", "INTEGER")
    _add_col(conn, "vendor_core_returns", "rejected_at", "TIMESTAMP")
    _add_col(conn, "vendor_core_returns", "written_off_at", "TIMESTAMP")

    # ── vendor_return_audit (new table) ──────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_return_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rga_id INTEGER REFERENCES vendor_returns(id) ON DELETE SET NULL,
            vendor_core_return_id INTEGER REFERENCES vendor_core_returns(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            tracking_number TEXT,
            credit_amount REAL,
            user TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vra_rga ON vendor_return_audit(rga_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vra_vcr ON vendor_return_audit(vendor_core_return_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vra_created ON vendor_return_audit(created_at DESC)"
        )
    except Exception:
        pass

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vrl_vcr ON vendor_return_lines(vendor_core_return_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vcr_rga ON vendor_core_returns(rga_id)"
        )
    except Exception:
        pass

    conn.commit()
