"""Migration 113: Purge phantom vendor_core_obligations.

Before migration 112 (and before the matching code change in
``_create_core_deposit_child_line`` / ``receive_po_line``), the system was
auto-creating ``vendor_core_obligations`` rows at PO-creation time. Those
rows have ``customer_core_id IS NULL`` and ``rga_id IS NULL`` and were
never tied to a real customer-side return, so they are noise on the
Vendor Returns / Cores screen.

This migration deletes those orphans. The new path
(``create_vendor_core_obligation_from_customer_return``) is the only
authorized writer going forward.
"""
from __future__ import annotations


def migrate(conn) -> None:
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM vendor_core_obligations "
            " WHERE customer_core_id IS NULL "
            "   AND (rga_id IS NULL OR rga_id = 0) "
            "   AND status = 'pending'"
        ).fetchone()
        before_n = before[0] if not hasattr(before, "keys") else before[0]
    except Exception:
        before_n = 0

    try:
        conn.execute(
            "DELETE FROM vendor_core_obligations "
            " WHERE customer_core_id IS NULL "
            "   AND (rga_id IS NULL OR rga_id = 0) "
            "   AND status = 'pending'"
        )
        conn.commit()
    except Exception:
        pass

    if before_n:
        print(f"[mig 113] Purged {before_n} phantom vendor_core_obligations rows")
