"""
scripts/seed_cost_from_vendor.py
================================
Q5 backfill — seed Product.cost from the preferred vendor source's vendor_cost
for UNRECEIPTED products, so margin reports have a cost basis before any PO
receipt. One-time companion to the import-time seed in product_import_service.py
(the catalog was already loaded with vendor_cost on the sources but cost=0 on the
products).

Safe + idempotent:
  * Only touches products with qty_on_hand = 0 AND cost in (NULL, 0) — i.e. never
    receipted, so Product.cost is just an estimate, not a real moving-average COGS.
  * Inventory valuation (= qty x cost) is unaffected because qty is 0.
  * The first real PO receipt overwrites this via moving-average cost.
A timestamped backup is written before any change.

Run with the venv python from the repo root, server STOPPED:
    .venv\\Scripts\\python.exe scripts\\seed_cost_from_vendor.py
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jaks.db"
BACKUP_DIR = ROOT / "backups"

_UPDATE = """
UPDATE products
SET cost = (
    SELECT pvs.vendor_cost FROM product_vendor_sources pvs
    WHERE pvs.product_id = products.id
      AND pvs.is_preferred = 1 AND pvs.is_active = 1 AND pvs.vendor_cost > 0
    LIMIT 1
)
WHERE (qty_on_hand IS NULL OR qty_on_hand = 0)
  AND (cost IS NULL OR cost = 0)
  AND EXISTS (
    SELECT 1 FROM product_vendor_sources pvs
    WHERE pvs.product_id = products.id
      AND pvs.is_preferred = 1 AND pvs.is_active = 1 AND pvs.vendor_cost > 0
  )
"""


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"jaks-precostseed-{ts}.db"
    shutil.copy2(DB, backup)
    print(f"Backup written: {backup}")

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    before = cur.execute("SELECT COUNT(*) FROM products WHERE cost > 0").fetchone()[0]
    eligible = cur.execute(
        "SELECT COUNT(*) FROM products p WHERE (p.qty_on_hand IS NULL OR p.qty_on_hand=0) "
        "AND (p.cost IS NULL OR p.cost=0) AND EXISTS (SELECT 1 FROM product_vendor_sources s "
        "WHERE s.product_id=p.id AND s.is_preferred=1 AND s.is_active=1 AND s.vendor_cost>0)"
    ).fetchone()[0]
    print(f"Products with cost>0 before: {before}")
    print(f"Eligible to seed:           {eligible}")

    cur.execute(_UPDATE)
    conn.commit()

    after = cur.execute("SELECT COUNT(*) FROM products WHERE cost > 0").fetchone()[0]
    # Sanity: no receipted product had its cost changed (qty>0 are excluded above).
    receipted_zero = cur.execute(
        "SELECT COUNT(*) FROM products WHERE qty_on_hand > 0 AND (cost IS NULL OR cost=0)"
    ).fetchone()[0]
    conn.close()

    print(f"Products with cost>0 after:  {after}  (+{after - before})")
    print(f"(receipted products with cost=0, untouched: {receipted_zero})")
    print(f"\nUndo: copy '{backup.name}' back over data/jaks.db (or use in-app Restore).")


if __name__ == "__main__":
    main()
