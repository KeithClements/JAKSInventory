"""
scripts/fix_house_brand_sku_doubling.py
========================================
One-time cleanup for the house-brand SKU-doubling bug (fixed in
product_service.py / product_import_service.py / regenerate_skus_vendor.py):
the vendor-code segment was being masked with a SECOND copy of the house
prefix instead of omitted, minting ``JAKS-JAKS-{part#}`` instead of the
intended ``JAKS-{part#}``.

This rewrites every existing ``{prefix}-{prefix}-...`` SKU to the single-
prefix form by stripping exactly one leading ``{prefix}-`` occurrence.

Dry-run by default (prints the rename plan, writes nothing). ``--apply``
copies data/jaks.db to a timestamped backup first, then writes.

Use ``--exclude-id ID [ID ...]`` to skip specific products that are being
handled separately (e.g. as part of a manual merge with a hand-picked SKU).

Run (server STOPPED):
  .venv\\Scripts\\python.exe scripts\\fix_house_brand_sku_doubling.py               # dry-run
  .venv\\Scripts\\python.exe scripts\\fix_house_brand_sku_doubling.py --apply       # write
  .venv\\Scripts\\python.exe scripts\\fix_house_brand_sku_doubling.py --apply --exclude-id 30936
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "jaks.db"
BACKUP_DIR = ROOT / "backups"


def _arg_ints(flag: str) -> set[int]:
    if flag not in sys.argv:
        return set()
    out = set()
    for tok in sys.argv[sys.argv.index(flag) + 1:]:
        if tok.startswith("--"):
            break
        out.add(int(tok))
    return out


def main(apply: bool, exclude_ids: set[int]) -> dict:
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    prefix = (cur.execute("SELECT value FROM settings WHERE key='sku_prefix'").fetchone()
              or ["JAKS"])[0] or "JAKS"
    doubled = f"{prefix}-{prefix}-"

    rows = cur.execute(
        "SELECT id, sku FROM products WHERE sku LIKE ? ORDER BY id", (f"{doubled}%",)
    ).fetchall()

    all_skus = {(s or "").strip().upper() for (_id, s) in
                cur.execute("SELECT id, sku FROM products").fetchall()}

    plan, skipped_excluded, skipped_collision = [], [], []
    for pid, old_sku in rows:
        if pid in exclude_ids:
            skipped_excluded.append((pid, old_sku))
            continue
        new_sku = f"{prefix}-{old_sku[len(doubled):]}"  # strip ONE leading "{prefix}-"
        if new_sku.strip().upper() in all_skus - {old_sku.strip().upper()}:
            skipped_collision.append((pid, old_sku, new_sku))
            continue
        plan.append((pid, old_sku, new_sku))

    print(f"{'APPLYING' if apply else 'DRY-RUN'} — house-brand SKU de-dupe (prefix={prefix})")
    print(f"Doubled-prefix candidates: {len(rows)} | to rename: {len(plan)} | "
          f"excluded: {len(skipped_excluded)} | collisions skipped: {len(skipped_collision)}")
    for pid, old_sku, new_sku in plan:
        print(f"    {pid}: {old_sku}  ->  {new_sku}")
    if skipped_excluded:
        print("Excluded (handled separately):")
        for pid, old_sku in skipped_excluded:
            print(f"    {pid}: {old_sku}")
    if skipped_collision:
        print("Collisions (left untouched — needs a distinct JAKS Product #):")
        for pid, old_sku, new_sku in skipped_collision:
            print(f"    {pid}: {old_sku}  (wanted {new_sku})")

    if not apply:
        print("\n(dry-run - nothing written. Re-run with --apply to commit.)")
        conn.close()
        return {"renamed": len(plan), "excluded": len(skipped_excluded),
                "collisions": len(skipped_collision)}

    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"jaks-pre-sku-dedupe-{ts}.db"
    shutil.copy2(DB, backup)
    print(f"\nBackup written: {backup}")

    # Two-phase (stage temp -> final) so the UNIQUE(sku) index is never tripped.
    for pid, *_ in plan:
        cur.execute("UPDATE products SET sku = ? WHERE id = ?", (f"__SKUTMP__{pid}", pid))
    for pid, _old, new_sku in plan:
        cur.execute("UPDATE products SET sku = ? WHERE id = ?", (new_sku, pid))
    conn.commit()
    conn.close()
    print(f"Renamed {len(plan)} SKUs.")
    print(f"Undo: copy '{backup.name}' back over data/jaks.db (or use in-app Restore).")
    return {"renamed": len(plan), "excluded": len(skipped_excluded),
            "collisions": len(skipped_collision)}


if __name__ == "__main__":
    main(apply="--apply" in sys.argv, exclude_ids=_arg_ints("--exclude-id"))
