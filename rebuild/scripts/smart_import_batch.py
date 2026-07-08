"""
scripts/smart_import_batch.py
==============================
Headless Smart Import — stage and apply a scraper new-products CSV without the
web UI. Same "in-process against data/jaks.db" pattern as
scripts/import_and_push.py, but for NEW PRODUCT ONBOARDING instead of
price/cost/availability refresh (import_and_push explicitly never creates
products; this script is that gap's answer for pre-vetted onboarding batches).

Default is a DRY RUN (parses + tallies the feed, stages/applies NOTHING).
Pass --apply to stage for real — auto-accepting "confident" rows (clean NEW or
UPDATE, not flagged for review; the SAME auto_accept_confident=True behavior a
real web upload's background staging applies) — then immediately apply every
ACCEPTED candidate to the catalog via ImportReviewService.apply_approved().

Flagged/uncertain rows (duplicate-in-feed, cross-ref-only match, low-confidence
classification) stay PENDING in the batch and are NOT applied — open
/import-review in the web UI to review batch <id> manually. This mirrors the
system's designed human-review gate exactly; nothing here bypasses it.

Does NOT touch Shopify. apply_approved() only writes the ERP's own catalog
(product/vendor-source/cross-ref/image rows) — a newly created product is
NOT published/visible/purchasable on the storefront until a separate,
explicit, later publish step (a human action in shopify_service.py).

Run (report):  .venv\\Scripts\\python.exe -m scripts.smart_import_batch <csv>
Run (apply):   .venv\\Scripts\\python.exe -m scripts.smart_import_batch <csv> --apply
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _read_csv(path: str) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8-sig")


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m scripts.smart_import_batch",
        description="Headless Smart Import for a scraper new-products CSV "
                    "(dry-run report by default).")
    ap.add_argument("csv_path")
    ap.add_argument("--apply", action="store_true",
                    help="stage (auto-accepting confident rows) + apply to the "
                         "catalog (default: dry-run report, nothing written)")
    ap.add_argument("--label", default="", help="batch label (default: filename)")
    ap.add_argument("--source-app", default="axleforge")
    args = ap.parse_args(argv)

    from app.database import SessionLocal
    from app.services.import_review_service import ImportReviewService

    text = _read_csv(args.csv_path)
    filename = pathlib.Path(args.csv_path).name
    db = SessionLocal()
    try:
        # user_id=None -> system/background run; apply_approved's own gate
        # (Permission.APPLY_IMPORT via assert_can) explicitly allows this.
        svc = ImportReviewService(db, None)

        if not args.apply:
            batch = svc.analyze_feed(text, source_app=args.source_app,
                                     filename=filename, label=args.label,
                                     dry_run=True)
            print(f"DRY RUN -- {filename}")
            print(f"  total={batch.total} new={batch.new_count} "
                  f"update={batch.update_count} cross_ref={batch.cross_ref_count} "
                  f"unchanged={batch.unchanged_count} "
                  f"needs_review={batch.needs_review_count}")
            print("\nNothing staged or written. Re-run with --apply to stage "
                  "(auto-accepting confident NEW/UPDATE rows; flagged rows stay "
                  "pending for manual review) and apply the accepted rows to "
                  "the catalog. Never touches Shopify.")
            return 0

        # Stage for real. auto_accept_confident=True mirrors exactly what a
        # real web upload's background staging does (run_background_staging) --
        # not a new/looser behavior invented for this script.
        rows = svc._parse_rows(text)
        batch = svc._new_batch(rows, label=args.label or filename,
                               source_app=args.source_app, filename=filename)
        svc._stage_rows(batch, rows, dry_run=False,
                        auto_accept_confident=True, skip_unchanged=True)
        print(f"STAGED batch {batch.id} ({filename}):")
        print(f"  total={batch.total} new={batch.new_count} "
              f"update={batch.update_count} cross_ref={batch.cross_ref_count} "
              f"unchanged={batch.unchanged_count} "
              f"needs_review={batch.needs_review_count} "
              f"auto_approved={batch.approved_count}")

        result = svc.apply_approved(batch.id)
        print(f"\nAPPLIED: applied={result.get('applied')} "
              f"created={result.get('created')} updated={result.get('updated')} "
              f"skipped_duplicates={result.get('skipped_duplicates')} "
              f"images_added={result.get('images_added')} "
              f"cross_refs_added={result.get('cross_refs_added')}")
        if result.get("errors"):
            print(f"  errors ({len(result['errors'])}):")
            for e in result["errors"][:10]:
                print(f"    - {e}")

        db.refresh(batch)
        if batch.needs_review_count:
            print(f"\n{batch.needs_review_count} row(s) still PENDING review "
                  f"(flagged -- NOT applied). Open /import-review to review "
                  f"batch {batch.id} manually.")
        print("\nNote: created/updated products are NOT published to Shopify -- "
              "that remains a separate, explicit, later step.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
