"""
scripts/revert_sku_to_vendor_part.py
====================================
One-shot REVERT — set every STANDARD product's customer-facing ``sku`` back to its
preferred vendor's real part number, undoing the 2026-06-06 opaque JAKS SKU scheme
(``JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]``). See MASTER_PLAN.md §20.

    product.sku  <-  preferred ProductVendorSource.vendor_part_number   (e.g. PAI 040000)

Private-label products (``products.is_house_brand = True``) are SKIPPED — they keep
their owner-typed JAKS Product # (e.g. ``2239250S3``); the vendor's number stays on the
source and prints on the PO. (None exist yet as of 2026-06-16, so this is a safety no-op.)

Write is TWO-PHASE (stage temp skus -> finals) so the ``UNIQUE(sku)`` index never trips
mid-update. ``--apply`` copies data/jaks.db to a timestamped .bak first. ASCII-only output
(the Windows cp1252 console chokes on unicode arrows).

Usage (from the rebuild/ dir):
  .venv/Scripts/python.exe scripts/revert_sku_to_vendor_part.py             # DRY-RUN (writes nothing)
  .venv/Scripts/python.exe scripts/revert_sku_to_vendor_part.py --limit 50  # DRY-RUN, first 50 only
  .venv/Scripts/python.exe scripts/revert_sku_to_vendor_part.py --apply     # WRITE (auto-backs up jaks.db first)

Idempotent: re-running yields the same skus. The catalog is throwaway/re-importable, but
--apply still backs jaks.db up first.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _arg_int(flag: str) -> int | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass
    return None


def _part_no(src) -> str:
    """A source's real vendor part number (vendor_part_number, then vendor_sku)."""
    if src is None:
        return ""
    return ((getattr(src, "vendor_part_number", "") or "")
            or (getattr(src, "vendor_sku", "") or "")).strip()


def _best_part_no(product) -> str:
    """The vendor part number to revert this product's sku to:
    the active+preferred source first, then any active source, then any source."""
    pref = product.preferred_vendor_source
    if _part_no(pref):
        return _part_no(pref)
    for s in product.vendor_sources:
        if getattr(s, "is_active", True) and _part_no(s):
            return _part_no(s)
    for s in product.vendor_sources:
        if _part_no(s):
            return _part_no(s)
    return ""


def plan_revert(db, limit: int | None = None):
    from app.models.product import Product

    summary = {
        "products": 0, "reverted": 0, "unchanged": 0,
        "skipped_house_brand": 0, "skipped_no_part_number": 0,
        "skipped_collision": 0, "samples": [], "collisions": [],
    }
    q = db.query(Product).order_by(Product.id)
    if limit:
        q = q.limit(limit)
    products = q.all()
    summary["products"] = len(products)

    # Decide each product's target sku; products we won't touch reserve their sku.
    decided = []                 # (product, new_sku)
    reserved: set[str] = set()   # skus held by skipped products (targets must not collide)
    for p in products:
        if getattr(p, "is_house_brand", False):
            summary["skipped_house_brand"] += 1
            reserved.add((p.sku or "").strip())
            continue
        new_sku = _best_part_no(p)
        if not new_sku:
            summary["skipped_no_part_number"] += 1
            reserved.add((p.sku or "").strip())
            continue
        decided.append((p, new_sku))

    # Collision guard: a target must be unique across all targets AND not clash with a
    # reserved (skipped) product's sku. Two changing products swapping skus is fine — the
    # two-phase write stages temps first — so a changing product's OLD sku is never reserved.
    plan = []                    # (product, old_sku, new_sku)
    targets: dict[str, int] = {}
    for p, new_sku in decided:
        if new_sku in targets or new_sku in reserved:
            summary["skipped_collision"] += 1
            summary["collisions"].append(
                {"id": p.id, "old": p.sku, "wanted": new_sku, "title": (p.title or "")[:48]}
            )
            continue
        targets[new_sku] = p.id
        if (p.sku or "").strip() == new_sku:
            summary["unchanged"] += 1
            continue
        plan.append((p, p.sku, new_sku))
        if len(summary["samples"]) < 25:
            summary["samples"].append(
                {"old": p.sku, "new": new_sku, "title": (p.title or "")[:48]}
            )
    summary["reverted"] = len(plan)
    return summary, plan


def apply_revert(db, plan) -> None:
    for p, _old, _new in plan:                 # phase 1 - stage unique temps
        p.sku = f"__SKUREVTMP__{p.id}"
    db.flush()
    for p, _old, new_sku in plan:              # phase 2 - finals
        p.sku = new_sku
    db.commit()


def main(apply: bool, limit: int | None = None) -> dict:
    import app.database as d
    d.init_db()  # idempotent
    from app.database import SessionLocal, engine

    if apply:
        db_path = getattr(engine.url, "database", None)
        if db_path and pathlib.Path(db_path).exists():
            bak = f"{db_path}.pre-sku-revert-{datetime.now():%Y%m%d-%H%M%S}.bak"
            shutil.copy2(db_path, bak)
            print(f"backup -> {bak}")

    db = SessionLocal()
    try:
        summary, plan = plan_revert(db, limit=limit)
        if apply and plan:
            apply_revert(db, plan)
    finally:
        db.close()

    print(("APPLIED" if apply else "DRY-RUN") + " - revert sku to vendor part number")
    print(f"  products scanned         : {summary['products']}")
    print(f"  reverted                 : {summary['reverted']}")
    print(f"  already correct          : {summary['unchanged']}")
    print(f"  skipped (private label)  : {summary['skipped_house_brand']}")
    print(f"  skipped (no part number) : {summary['skipped_no_part_number']}")
    print(f"  skipped (collision)      : {summary['skipped_collision']}")
    if summary["collisions"]:
        print("  [!] collisions (kept old sku):")
        for c in summary["collisions"][:10]:
            print(f"      #{c['id']} {c['old']} wanted {c['wanted']}  {c['title']}")
    print("  samples (old -> new):")
    for s in summary["samples"]:
        print(f"    {s['old']:<24} -> {s['new']:<16}  {s['title']}")
    if not apply:
        print("  (dry-run - nothing written. Re-run with --apply to commit.)")
    return summary


if __name__ == "__main__":
    main(apply="--apply" in sys.argv, limit=_arg_int("--limit"))
