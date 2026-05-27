"""Migration 115: Packing slip + freight label + vendor returns address + status renames.

Phase G of the core-handling revitalization. Three jobs:

1. **VCO status remap** — finalize lifecycle vocabulary::
       ready_to_ship  → pending_vendor_ship
       closed         → credit_received

2. **vendor_returns** — add packing-slip + freight-label columns so the
   Mark-Shipped gate can require both PDFs to exist before any obligation
   transitions to ``shipped``.

3. **vendors** — add structured Returns/RGA address + contact columns so
   we can render the "Ship To" block on the packing slip + label.

Also seeds ``app_settings.next_packing_slip_number`` for sequential
CRP-XXXX numbering.

This migration uses the standard ``migrate(conn)`` entry point so the
glob loader in main_window.py picks it up automatically at GUI launch.
"""
from __future__ import annotations


def _existing_cols(conn, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _add_col(conn, table: str, col: str, decl: str) -> None:
    if col not in _existing_cols(conn, table):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except Exception as e:
            print(f"[mig 115] add {table}.{col} skipped: {e}")


def migrate(conn) -> None:
    # ── 1) VCO status renames ────────────────────────────────────
    try:
        conn.execute(
            "UPDATE vendor_core_obligations SET status='pending_vendor_ship' "
            "WHERE status='ready_to_ship'"
        )
        conn.execute(
            "UPDATE vendor_core_obligations SET status='credit_received' "
            "WHERE status='closed'"
        )
        conn.commit()
    except Exception as e:
        print(f"[mig 115] vco status rename skipped: {e}")

    # ── 2) vendor_returns: packing slip + freight label columns ──
    _add_col(conn, "vendor_returns", "packing_slip_doc_number", "TEXT")
    _add_col(conn, "vendor_returns", "packing_slip_path",       "TEXT")
    _add_col(conn, "vendor_returns", "packing_slip_version",    "INTEGER DEFAULT 0")
    _add_col(conn, "vendor_returns", "packing_slip_generated_at","DATETIME")
    _add_col(conn, "vendor_returns", "freight_label_path",      "TEXT")
    _add_col(conn, "vendor_returns", "freight_label_tracking",  "TEXT")
    _add_col(conn, "vendor_returns", "freight_label_carrier",   "TEXT")
    _add_col(conn, "vendor_returns", "freight_label_cost",      "REAL")
    _add_col(conn, "vendor_returns", "freight_label_generated_at","DATETIME")
    _add_col(conn, "vendor_returns", "weight_lbs",              "REAL")
    _add_col(conn, "vendor_returns", "box_count",               "INTEGER DEFAULT 1")

    # ── 3) vendors: returns / RGA address + contact ──────────────
    _add_col(conn, "vendors", "returns_address_line1", "TEXT")
    _add_col(conn, "vendors", "returns_address_line2", "TEXT")
    _add_col(conn, "vendors", "returns_address_city",  "TEXT")
    _add_col(conn, "vendors", "returns_address_state", "TEXT")
    _add_col(conn, "vendors", "returns_address_zip",   "TEXT")
    _add_col(conn, "vendors", "returns_contact_name",  "TEXT")
    _add_col(conn, "vendors", "returns_contact_phone", "TEXT")
    _add_col(conn, "vendors", "returns_contact_email", "TEXT")

    # ── 4) Packing slip sequence seed ────────────────────────────
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='next_packing_slip_number'"
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO app_settings(key, value) VALUES('next_packing_slip_number','1001')"
            )
        conn.commit()
    except Exception as e:
        print(f"[mig 115] packing-slip seq seed skipped: {e}")

    # ── 5) Audit ────────────────────────────────────────────────
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM vendor_core_obligations "
            "GROUP BY status ORDER BY status"
        ).fetchall()
        if rows:
            summary = ", ".join(f"{r[0]}={r[1]}" for r in rows)
            print(f"[mig 115] vendor_core_obligations status counts: {summary}")
    except Exception:
        pass
