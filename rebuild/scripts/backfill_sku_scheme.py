"""
scripts/backfill_sku_scheme.py
==============================
One-shot backfill — regenerate every product's CUSTOMER-FACING ``sku`` onto the
owner-locked JAKS SKU scheme (2026-06-06):

    JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]      e.g.  JAKS-ISX-INJ-90001

It reads each product's §18 classification (category + engine_model) and its
preferred vendor's 1-digit ``vendor_number``, allocates a 4-digit sequence per
(engine, category), and parks the old ``JAKS-PAI-…`` number on the vendor source's
``vendor_sku`` so it stays searchable. Vendor-INDEPENDENT: the SKU never contains
the vendor name or the vendor's real part number — only the opaque digit.

BEFORE you --apply:
  1. Set a 1-digit number on each vendor (Inventory → Vendors → edit, "SKU #").
     Products whose vendor has no number are SKIPPED and keep their old sku.
  2. (Optional) Set short codes on your categories (Inventory → Category
     Maintenance). Blank codes are auto-derived from the name (coarse but valid).

Usage (from the rebuild/ dir):
  .venv/Scripts/python.exe scripts/backfill_sku_scheme.py             # DRY-RUN (writes nothing)
  .venv/Scripts/python.exe scripts/backfill_sku_scheme.py --limit 50  # DRY-RUN, first 50 only
  .venv/Scripts/python.exe scripts/backfill_sku_scheme.py --apply     # WRITE (auto-backs up jaks.db first)

Idempotent: re-running --apply with the same data + vendor numbers yields the
same SKUs. data/jaks.db is throwaway/re-importable, but --apply still copies it to
a timestamped .bak first.
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


def main(apply: bool, limit: int | None = None) -> dict:
    import app.database as d
    d.init_db()  # idempotent: ensures the SKU-scheme columns exist on data/jaks.db
    from app.database import SessionLocal, engine
    from app.services.sku_service import SkuService

    if apply:
        db_path = getattr(engine.url, "database", None)
        if db_path and pathlib.Path(db_path).exists():
            bak = f"{db_path}.pre-sku-backfill-{datetime.now():%Y%m%d-%H%M%S}.bak"
            shutil.copy2(db_path, bak)
            print(f"backup → {bak}")

    db = SessionLocal()
    try:
        summary = SkuService(db).regenerate_catalog(apply=apply, limit=limit)
    finally:
        db.close()

    print(("APPLIED" if apply else "DRY-RUN") + " — JAKS SKU scheme backfill")
    print(f"  products scanned     : {summary['products']}")
    print(f"  regenerated          : {summary['regenerated']}")
    print(f"  skipped (no vendor)  : {summary['skipped_no_vendor']}")
    print(f"  skipped (no SKU #)   : {summary['skipped_no_vendor_number']}")
    print(f"  by vendor digit      : {summary['by_vendor_digit']}")
    if summary["skipped_no_vendor_number"]:
        print("  ⚠  Some vendors have no 1-digit SKU number set — their products were "
              "skipped. Set it on each vendor, then re-run.")
    print("  samples (old → new):")
    for s in summary["samples"]:
        print(f"    {s['old']:<22} → {s['new']:<22}  {s['title']}")
    if not apply:
        print("  (dry-run — nothing written. Re-run with --apply to commit.)")
    return summary


if __name__ == "__main__":
    main(apply="--apply" in sys.argv, limit=_arg_int("--limit"))
