"""
scripts/regenerate_skus_vendor.py
=================================
Rewrite existing product SKUs to the default vendor scheme
``{prefix}-{vendor_code}-{part#}`` (e.g. 040000 -> JAKS-PAI-040000).

Safe for the trial: a timestamped backup is written first, and there are no
invoices/quotes/SOs referencing SKUs after the clean-start. The old SKU (= the
raw part #) is already on the preferred vendor source (vendor_part_number /
vendor_sku) so it stays fully searchable; we also park it on vendor_sku if blank.

Scope:
  * Only products with a preferred ACTIVE vendor source whose vendor has a code.
  * SKIPS private-label (is_house_brand) products — those keep the owner's #.
  * SKIPS rows already in the target format, and any that would collide.
Two-phase write (stage temp -> final) so the UNIQUE(sku) index is never tripped
mid-update.

Run (server STOPPED):  .venv\\Scripts\\python.exe scripts\\regenerate_skus_vendor.py
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

from app.services.sku_service import build_vendor_sku  # single source of truth


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"jaks-preskuregen-{ts}.db"
    shutil.copy2(DB, backup)
    print(f"Backup written: {backup}")

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    prefix = (cur.execute("SELECT value FROM settings WHERE key='sku_prefix'").fetchone()
              or ["JAKS"])[0] or "JAKS"
    print(f"Prefix: {prefix}")

    # Candidates: preferred active source + vendor code; not private-label.
    rows = cur.execute("""
        SELECT p.id, p.sku, v.vendor_code, s.vendor_part_number, s.vendor_sku
        FROM products p
        JOIN product_vendor_sources s
          ON s.product_id = p.id AND s.is_preferred = 1 AND s.is_active = 1
        JOIN vendors v ON v.id = s.vendor_id
        WHERE COALESCE(p.is_house_brand, 0) = 0
    """).fetchall()

    # SKUs that will NOT change (everything not a candidate) — guard collisions.
    candidate_ids = {r[0] for r in rows}
    untouched_skus = {
        (s or "").strip().upper()
        for (pid, s) in cur.execute("SELECT id, sku FROM products").fetchall()
        if pid not in candidate_ids
    }

    plan = []           # (id, old_sku, new_sku, vendor_sku)
    seen_new = {}
    counts = {"already": 0, "no_part": 0, "collision": 0}
    for pid, old_sku, vcode, part, vsku in rows:
        if not (vcode and (part or "").strip()):
            counts["no_part"] += 1
            continue
        new_sku = build_vendor_sku(prefix, vcode, part)
        if new_sku == (old_sku or ""):
            counts["already"] += 1
            continue
        if new_sku in seen_new or new_sku in untouched_skus:
            counts["collision"] += 1
            continue
        seen_new[new_sku] = pid
        plan.append((pid, old_sku, new_sku, vsku))

    print(f"Candidates: {len(rows)} | to change: {len(plan)} | "
          f"already-format: {counts['already']} | no part/code: {counts['no_part']} | "
          f"collisions skipped: {counts['collision']}")

    # Phase 1 — stage unique temps so finals can't trip UNIQUE(sku) mid-update.
    for pid, *_ in plan:
        cur.execute("UPDATE products SET sku = ? WHERE id = ?", (f"__SKUTMP__{pid}", pid))
    # Phase 2 — finals + keep the old SKU searchable on the source.
    for pid, old_sku, new_sku, vsku in plan:
        cur.execute("UPDATE products SET sku = ? WHERE id = ?", (new_sku, pid))
        if not (vsku or "").strip() and (old_sku or "").strip():
            cur.execute(
                "UPDATE product_vendor_sources SET vendor_sku = ? "
                "WHERE product_id = ? AND is_preferred = 1 AND is_active = 1",
                (old_sku, pid),
            )
    conn.commit()
    cur.execute("VACUUM")
    conn.commit()

    sample = cur.execute("SELECT sku FROM products WHERE sku LIKE ? LIMIT 5",
                         (f"{prefix}-%",)).fetchall()
    total_prefixed = cur.execute("SELECT COUNT(*) FROM products WHERE sku LIKE ?",
                                 (f"{prefix}-%",)).fetchone()[0]
    conn.close()
    print(f"\nChanged {len(plan)} SKUs. Now {total_prefixed} products carry the '{prefix}-' prefix.")
    print("Sample:", ", ".join(s[0] for s in sample))
    print(f"\nUndo: copy '{backup.name}' back over data/jaks.db (or use in-app Restore).")


if __name__ == "__main__":
    main()
