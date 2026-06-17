"""
scripts/clean_start_trial.py
============================
Reset the live DB to a CLEAN TRIAL STATE: wipe all transactional + customer data
so the shop starts the trial from scratch, while KEEPING the full product
catalog (products, vendor sources, cross-refs, applications, images, categories),
the vendor/brand/manufacturer master lists, settings, and user logins.

It is fully reversible: a timestamped backup of data/jaks.db is written to
backups/ before anything is deleted. To undo, copy that file back over
data/jaks.db (or use the in-app Restore).

Run with the venv python from the repo root, with the server STOPPED:
    .venv\\Scripts\\python.exe scripts\\clean_start_trial.py
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jaks.db"
BACKUP_DIR = ROOT / "backups"

# Transactional + customer tables to EMPTY. (Master/catalog/config tables are
# intentionally absent from this list and are left untouched.)
CLEAR_TABLES = [
    # Sales documents
    "quotes", "quote_lines", "quote_followups",
    "sales_orders", "so_lines",
    "invoices", "invoice_lines",
    "payments", "payment_allocations",
    "credit_memos", "credit_memo_lines", "credit_memo_allocations",
    # Purchasing / receiving / AP
    "purchase_orders", "po_lines", "po_receipts", "po_receipt_lines",
    "vendor_bills", "vendor_bill_lines",
    "vendor_credits", "vendor_credit_memos", "vendor_credit_memo_allocations",
    # Cores
    "core_charges", "core_return_events", "core_slips", "core_location_movements",
    "vendor_core_returns", "vendor_core_return_lines",
    # Returns / warranty
    "return_authorizations", "return_lines",
    "warranty_claims", "warranty_claim_lines",
    # Inventory movement (cache is zeroed below)
    "inventory_transactions", "inventory_transfers", "shipments",
    # Sales intelligence / research
    "lost_sales_log", "research_items", "research_activity_log",
    # Customers + their child records
    "customers", "customer_addresses", "customer_contacts",
    "customer_call_logs", "customer_statements",
    "communication_log", "communication_attachments", "document_attachments",
    # Misc operational noise
    "notifications", "audit_log",
    "import_batches", "import_candidates", "import_pending_uploads",
]

# Document-number counters → restart numbering at 0001 (bump_counter stores NEXT).
COUNTER_KEYS = [
    "next_invoice_number", "next_quote_number", "next_so_number", "next_po_number",
    "next_ra_number", "next_wc_number", "next_ri_number", "next_core_slip_number",
    "next_vcr_number", "next_cm_number", "next_vcm_number", "next_vr_number",
    "next_statement_number",
]


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")

    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"jaks-precleanstart-{ts}.db"
    shutil.copy2(DB, backup)
    print(f"Backup written: {backup}")

    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA foreign_keys=OFF")
    cur = conn.cursor()

    existing = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    print("\nClearing transactional tables:")
    total_deleted = 0
    for t in CLEAR_TABLES:
        if t not in existing:
            print(f"  (skip, no table) {t}")
            continue
        before = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        cur.execute(f"DELETE FROM {t}")
        total_deleted += before
        if before:
            print(f"  {t:<32} {before} -> 0")

    # Zero the product on-hand/committed/on-order caches (their ledger is gone).
    cur.execute(
        "UPDATE products SET qty_on_hand=0, qty_committed=0, qty_on_order=0"
    )
    print(f"\nZeroed product inventory caches on {cur.rowcount} products.")

    # Restart document numbering.
    reset = cur.execute(
        "UPDATE settings SET value='1' WHERE key IN (%s)"
        % ",".join("?" for _ in COUNTER_KEYS),
        COUNTER_KEYS,
    ).rowcount
    print(f"Reset {reset} document-number counters to 0001.")

    conn.commit()
    cur.execute("VACUUM")
    conn.commit()

    # Sanity: catalog kept?
    products = cur.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    customers = cur.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    invoices = cur.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    print("\n--- Clean-start complete ---")
    print(f"  rows deleted:   {total_deleted}")
    print(f"  products kept:  {products}")
    print(f"  users kept:     {users}")
    print(f"  customers now:  {customers}")
    print(f"  invoices now:   {invoices}")
    print(f"\n  Undo: copy '{backup.name}' back over data/jaks.db (or use in-app Restore).")


if __name__ == "__main__":
    main()
