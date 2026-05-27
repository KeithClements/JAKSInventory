"""Migration 112: Unify vendor core tracking on vendor_core_obligations.

Background
----------
Historically two parallel tables tracked vendor-side cores:

* ``vendor_core_returns`` (mig 055) — auto-created at PO-receipt time. One
  row per unit received with a core_charge. This produced a "phantom" pending
  count even when no customer had actually returned a core to us yet.
* ``vendor_core_obligations`` (mig 108) — richer status lifecycle, intended
  as the authoritative ledger but never fully wired into the UI.

Going forward the rule is:
    A vendor core obligation is created ONLY when a customer's core is marked
    received by us. We do not owe the vendor anything until the customer brings
    their core back.

This migration:
    1. Adds columns to ``vendor_core_obligations`` that mirror the auditing
       fields the RGA screen had been reading from ``vendor_core_returns``.
    2. Adds ``vendor_core_obligation_id`` to ``vendor_return_lines`` alongside
       the legacy ``vendor_core_return_id``.
    3. Backfills only the still-meaningful ``vendor_core_returns`` rows — the
       ones already attached to an RGA — into ``vendor_core_obligations``.
       Unattached pending rows (the phantom backlog) are NOT carried over.
    4. Updates ``vendor_return_lines.vendor_core_obligation_id`` for any
       backfilled rows so the RGA screen keeps showing the attached cores.

``vendor_core_returns`` itself is left in place as a legacy read-only safety
net (no DROP TABLE). New writes go to ``vendor_core_obligations``.
"""
from __future__ import annotations


def _existing_cols(conn, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _add_col(conn, table: str, name: str, decl: str) -> None:
    if name in _existing_cols(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    except Exception:
        pass


def migrate(conn) -> None:
    # ── vendor_core_obligations: mirror legacy audit columns ─────────────
    for col, decl in (
        ("customer_core_id",       "INTEGER"),
        ("due_date",               "DATE"),
        ("shipped_date",           "TIMESTAMP"),
        ("credit_received_date",   "TIMESTAMP"),
        ("rejected_at",            "TIMESTAMP"),
        ("written_off_at",         "TIMESTAMP"),
    ):
        _add_col(conn, "vendor_core_obligations", col, decl)

    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_vco_customer_core "
        "    ON vendor_core_obligations(customer_core_id)",
        "CREATE INDEX IF NOT EXISTS idx_vco_vendor_status "
        "    ON vendor_core_obligations(vendor_id, status)",
    ):
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    # ── vendor_return_lines: add obligation FK alongside legacy FK ───────
    _add_col(conn, "vendor_return_lines", "vendor_core_obligation_id", "INTEGER")
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vrl_vco "
            "    ON vendor_return_lines(vendor_core_obligation_id)"
        )
    except Exception:
        pass

    # ── Backfill: carry over RGA-attached vendor_core_returns ────────────
    #
    # Only rows that are already attached to an RGA (rga_id IS NOT NULL) are
    # real, in-flight obligations. Pending unattached rows are the phantom
    # backlog created by the old PO-receipt auto-create — they intentionally
    # do NOT get carried over.
    try:
        legacy_rows = conn.execute(
            """
            SELECT id, po_id, po_line_id, vendor_id, product_id,
                   COALESCE(qty, 1)          AS quantity,
                   COALESCE(core_charge, 0)  AS core_amount,
                   status,
                   rga_id,
                   customer_core_id,
                   due_date,
                   shipped_date,
                   credit_received_date,
                   tracking_number,
                   vendor_credit_number,
                   credit_amount,
                   notes,
                   created_at,
                   rejected_at,
                   written_off_at
              FROM vendor_core_returns
             WHERE rga_id IS NOT NULL
            """
        ).fetchall()
    except Exception:
        legacy_rows = []

    # Status remap: vendor_core_returns used 'added_to_rga' where the new
    # lifecycle uses 'rga_created'.
    _STATUS_MAP = {
        "pending":        "pending",
        "added_to_rga":   "rga_created",
        "rga_created":    "rga_created",
        "shipped":        "shipped",
        "received":       "vendor_received",
        "vendor_received":"vendor_received",
        "credited":       "credited",
        "rejected":       "rejected",
        "written_off":    "written_off",
        "voided":         "rejected",
    }

    migrated = 0
    for row in legacy_rows:
        d = dict(row)
        # Skip if already migrated (idempotent re-run protection).
        existing = conn.execute(
            "SELECT id FROM vendor_core_obligations "
            "WHERE rga_id IS NOT NULL AND rga_id = ? "
            "  AND COALESCE(po_line_id, -1) = COALESCE(?, -1) "
            "  AND COALESCE(customer_core_id, -1) = COALESCE(?, -1) "
            "LIMIT 1",
            (d["rga_id"], d["po_line_id"], d["customer_core_id"]),
        ).fetchone()
        if existing:
            new_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
        else:
            new_status = _STATUS_MAP.get(
                str(d.get("status") or "pending").strip().lower(), "pending"
            )
            cur = conn.execute(
                """
                INSERT INTO vendor_core_obligations
                    (po_id, po_line_id, vendor_id, product_id, quantity,
                     core_amount, status, rga_id, tracking_number,
                     credit_received, vendor_credit_number, notes,
                     customer_core_id, due_date, shipped_date,
                     credit_received_date, rejected_at, written_off_at,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d["po_id"], d["po_line_id"], d["vendor_id"], d["product_id"],
                    d["quantity"], d["core_amount"], new_status, d["rga_id"],
                    d.get("tracking_number"),
                    d.get("credit_amount") or 0,
                    d.get("vendor_credit_number"),
                    d.get("notes") or "Migrated from vendor_core_returns",
                    d.get("customer_core_id"),
                    d.get("due_date"),
                    d.get("shipped_date"),
                    d.get("credit_received_date"),
                    d.get("rejected_at"),
                    d.get("written_off_at"),
                    d.get("created_at"),
                ),
            )
            new_id = cur.lastrowid
            migrated += 1

        # Point vendor_return_lines.vendor_core_obligation_id at the new row
        try:
            conn.execute(
                "UPDATE vendor_return_lines "
                "   SET vendor_core_obligation_id = ? "
                " WHERE vendor_core_return_id = ?"
                "   AND (vendor_core_obligation_id IS NULL "
                "        OR vendor_core_obligation_id = 0)",
                (new_id, d["id"]),
            )
        except Exception:
            pass

    if migrated:
        print(f"[mig 112] Migrated {migrated} RGA-attached vendor_core_returns "
              f"→ vendor_core_obligations")

    conn.commit()
