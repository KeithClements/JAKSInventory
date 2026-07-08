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

from app.services.sku_service import build_vendor_sku, bare_part_number  # single source of truth


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

    # Candidates: preferred active source + vendor code. Skip products that are
    # ALREADY house brand with their own owner-typed number (is_house_brand=1) —
    # except private-label VENDORS, whose products we (re)mask to the house prefix.
    has_pl = any(c[1] == "private_label"
                 for c in cur.execute("PRAGMA table_info(vendors)").fetchall())
    pl_col = "COALESCE(v.private_label, 0)" if has_pl else "0"
    rows = cur.execute(f"""
        SELECT p.id, p.sku, v.vendor_code, s.vendor_part_number, s.vendor_sku,
               {pl_col} AS private_label
        FROM products p
        JOIN product_vendor_sources s
          ON s.product_id = p.id AND s.is_preferred = 1 AND s.is_active = 1
        JOIN vendors v ON v.id = s.vendor_id
        WHERE COALESCE(p.is_house_brand, 0) = 0 OR {pl_col} = 1
    """).fetchall()

    # SKUs that will NOT change (everything not a candidate) — guard collisions.
    candidate_ids = {r[0] for r in rows}
    untouched_skus = {
        (s or "").strip().upper()
        for (pid, s) in cur.execute("SELECT id, sku FROM products").fetchall()
        if pid not in candidate_ids
    }

    plan = []           # (id, old_sku, new_sku, vendor_sku, private_label)
    seen_new = {}
    counts = {"already": 0, "no_part": 0, "collision": 0}
    pl_collisions = []  # private-label losers of a masked-SKU collision (id, old, would-be)
    for pid, old_sku, vcode, part, vsku, private_label in rows:
        # Private-label vendor → omit the vendor code segment entirely (never
        # double the prefix) → {prefix}-{part#}, e.g. JAKS-10R1273. Strip any
        # stray prefix the source part # already carries (shared helper) so we
        # never double it on re-runs.
        code = "" if private_label else vcode
        bare = bare_part_number(part or "")
        if not bare.strip() or (not private_label and not code):
            counts["no_part"] += 1
            continue
        new_sku = build_vendor_sku(prefix, code, bare)
        if new_sku == (old_sku or ""):
            counts["already"] += 1
            continue
        if new_sku in seen_new or new_sku in untouched_skus:
            counts["collision"] += 1
            # A private-label loser would otherwise SILENTLY keep its vendor-code-
            # leaking old SKU. Surface it so the owner can assign a distinct number.
            if private_label:
                pl_collisions.append((pid, old_sku, new_sku))
            continue
        seen_new[new_sku] = pid
        plan.append((pid, old_sku, new_sku, vsku, bool(private_label)))

    print(f"Candidates: {len(rows)} | to change: {len(plan)} | "
          f"already-format: {counts['already']} | no part/code: {counts['no_part']} | "
          f"collisions skipped: {counts['collision']}")

    # Phase 1 — stage unique temps so finals can't trip UNIQUE(sku) mid-update.
    for pid, *_ in plan:
        cur.execute("UPDATE products SET sku = ? WHERE id = ?", (f"__SKUTMP__{pid}", pid))
    # Phase 2 — finals + keep the old SKU searchable on the source. Private-label
    # vendor products are flagged house brand so the masked SKU is consistent.
    for pid, old_sku, new_sku, vsku, private_label in plan:
        cur.execute("UPDATE products SET sku = ? WHERE id = ?", (new_sku, pid))
        if private_label:
            cur.execute("UPDATE products SET is_house_brand = 1 WHERE id = ?", (pid,))
        if not (vsku or "").strip() and (old_sku or "").strip():
            cur.execute(
                "UPDATE product_vendor_sources SET vendor_sku = ? "
                "WHERE product_id = ? AND is_preferred = 1 AND is_active = 1",
                (old_sku, pid),
            )
    # A private-label product that collided keeps its old (leaking) SKU but is at
    # least flagged house brand; the owner must give it a distinct JAKS Product #.
    for pid, _old, _new in pl_collisions:
        cur.execute("UPDATE products SET is_house_brand = 1 WHERE id = ?", (pid,))
    conn.commit()
    cur.execute("VACUUM")
    conn.commit()

    if pl_collisions:
        print(f"\n⚠ {len(pl_collisions)} private-label product(s) collided on the "
              f"masked SKU and STILL SHOW THE VENDOR CODE — assign each a distinct "
              f"JAKS Product #:")
        for pid, old_sku, new_sku in pl_collisions:
            print(f"    product {pid}: keeps {old_sku}  (wanted {new_sku})")

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
