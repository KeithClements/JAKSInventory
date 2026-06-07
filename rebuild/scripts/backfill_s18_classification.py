"""
scripts/backfill_s18_classification.py
======================================
§18.8 one-shot backfill — bring the EXISTING catalog onto the new classification
model. Conservative + idempotent; safe to re-run.

For every product it:
  1. De-conflates Brand/Manufacturer — clears the legacy manufacturer value
     "PAI Industries" (the vendor name the old importer wrongly stored). Brand
     ("PAI") is left intact.
  2. Populates engine_manufacturer / engine_model (= Manufacturer / Engine Make,
     §18 A1) from the product's Applications when those fields are blank.
  3. Deepens the category when a Category-Maintenance import-keyword rule
     confidently matches (no rules set yet → no-op, by design).
  4. Flags needs_review ONLY for products with no category at all (keeps the
     Import Review queue from flooding with already-broad-categorised items;
     pass --flag-shallow to also flag level-1-only products once keyword rules
     exist).

Usage (from the rebuild/ dir):
  .venv/Scripts/python.exe scripts/backfill_s18_classification.py            # DRY-RUN
  .venv/Scripts/python.exe scripts/backfill_s18_classification.py --apply    # write
  .venv/Scripts/python.exe scripts/backfill_s18_classification.py --apply --flag-shallow
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_VENDOR_NAMES_AS_MANUFACTURER = {"pai industries"}  # legacy conflated values to clear


def main(apply: bool, flag_shallow: bool) -> dict:
    import app.database as d
    d.init_db()  # idempotent: ensures §18 columns/tables exist
    from app.database import SessionLocal
    from app.models.product import Product, ProductApplication, ProductCategory
    from app.services.classification_service import ClassificationService

    db = SessionLocal()
    try:
        classifier = ClassificationService(db)
        cat_level = dict(db.query(ProductCategory.id, ProductCategory.level).all())

        apps_by_pid: dict[int, list[tuple[str, str]]] = {}
        for pid, make, model in db.query(
            ProductApplication.product_id,
            ProductApplication.engine_make,
            ProductApplication.engine_model,
        ).all():
            apps_by_pid.setdefault(pid, []).append((make or "", model or ""))

        stats = {
            "scanned": 0, "mfr_cleared": 0, "engine_make_set": 0,
            "engine_model_set": 0, "category_deepened": 0,
            "flagged_review": 0, "changed": 0,
        }
        samples: list[str] = []

        for p in db.query(Product).all():
            stats["scanned"] += 1
            changed = False

            # 1) de-conflate manufacturer
            if (p.manufacturer or "").strip().lower() in _VENDOR_NAMES_AS_MANUFACTURER:
                p.manufacturer = ""
                stats["mfr_cleared"] += 1
                changed = True

            apps = apps_by_pid.get(p.id, [])
            cls = classifier.classify(
                title=p.title or "", tags=p.search_keywords or "",
                app_makes=[mk for mk, _ in apps],
                app_models=[mo for _, mo in apps],
            )

            # 2) engine make / model (fill blanks only — never overwrite)
            if not (p.engine_manufacturer or "").strip() and cls["engine_manufacturer"]:
                p.engine_manufacturer = cls["engine_manufacturer"]
                stats["engine_make_set"] += 1
                changed = True
            if not (p.engine_model or "").strip() and cls["engine_model"]:
                p.engine_model = cls["engine_model"]
                stats["engine_model_set"] += 1
                changed = True

            # 3) deepen category on a confident keyword match
            if cls["category_id"] and cls["category_id"] != p.category_id:
                p.category_id = cls["category_id"]
                stats["category_deepened"] += 1
                changed = True

            # 4) needs_review — conservative by default
            final_level = cat_level.get(p.category_id) if p.category_id else None
            should_flag = (p.category_id is None) or (flag_shallow and (final_level or 0) < 2)
            if should_flag and not p.needs_review:
                p.needs_review = True
                stats["flagged_review"] += 1
                changed = True

            if changed:
                stats["changed"] += 1
                if len(samples) < 8:
                    samples.append(
                        f"  {p.sku}: brand={p.brand!r} mfr={p.manufacturer!r} "
                        f"engine={p.engine_manufacturer!r} cat={p.category_id} review={p.needs_review}"
                    )

        if apply:
            db.commit()
        else:
            db.rollback()

        print(("APPLIED" if apply else "DRY-RUN") + " — " + str(stats))
        print("samples:")
        print("\n".join(samples) if samples else "  (no changes)")
        return stats
    finally:
        db.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv, flag_shallow="--flag-shallow" in sys.argv)
