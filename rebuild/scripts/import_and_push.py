"""
scripts/import_and_push.py
==========================
The ERP half of the one-click pipeline: take a scraper availability CSV, import it
(price + cost + vendor availability), then make the live Shopify storefront match —
hide parts the vendor can't supply, re-list parts we hid that are back, and refresh
price/stock for the parts in this file.

Runs IN-PROCESS against data/jaks.db (imports the ERP's own services), so it needs
no HTTP, session cookie, or CSRF token — it sidesteps the app's login wall entirely.
The scraper-side .bat calls this after writing the CSV.

Default is a READ-ONLY report (imports in dry-run, pushes nothing). Pass --apply to
write + push.

Run (report):        .venv\\Scripts\\python.exe -m scripts.import_and_push <csv>
Run (apply):         .venv\\Scripts\\python.exe -m scripts.import_and_push <csv> --apply
Options:
  --apply            actually import + push to the live store (default: dry-run report)
  --reconcile-only   on --apply, ONLY hide/re-list (availability) — skip the
                     price/stock refresh. Fast + targeted; the nightly sync still
                     refreshes price/stock catalog-wide. Use for quick availability
                     catch-ups or very large sections.
  --seo              on --apply, first generate AI SEO for this section's products
                     that lack it (costs Anthropic tokens), so they go live with
                     copy. Off by default to keep the daily run cheap.

NOTE: the Shopify token is Fernet-encrypted, so JAKS_FERNET_KEY must be set in the
environment (the .bat sets it) or the push step reports "not configured".
"""
from __future__ import annotations

import pathlib
import sys

# Force UTF-8 stdout so a legacy .bat console (cp1252) never crashes on output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _read_csv(path: str) -> str:
    # utf-8-sig: the scraper writes a BOM-free UTF-8, but the manual-upload path
    # decodes utf-8-sig, so be tolerant of either.
    return pathlib.Path(path).read_text(encoding="utf-8-sig")


def _csv_product_ids(text: str, imp) -> list[int]:
    """Map every SKU in the CSV to an ERP product id (the parts this file touches),
    so the push is SCOPED to this section — not the whole catalog."""
    import csv as _csv
    import io
    from app.services.product_import_service import _SKU_KEYS, _get, _norm
    id_map = imp._sku_to_id_map()
    ids: list[int] = []
    seen: set[int] = set()
    for raw in _csv.DictReader(io.StringIO(text)):
        row = {_norm(k): v for k, v in raw.items()}
        sku = _get(row, *_SKU_KEYS)
        if not sku:
            continue
        pid = id_map.get(_norm(sku))
        if pid is not None and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def _print(title: str, res: dict, keys: list[str]) -> None:
    print(f"\n== {title} ==")
    for k in keys:
        if k in res:
            print(f"  {k}: {res[k]}")
    if res.get("errors"):
        print("  errors:")
        for e in res["errors"][:8]:
            print(f"    - {e}")


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    reconcile_only = "--reconcile-only" in argv
    do_seo = "--seo" in argv
    paths = [a for a in argv if not a.startswith("-")]
    if not paths:
        print("usage: python -m scripts.import_and_push <csv> [--apply] [--reconcile-only]")
        return 2
    csv_path = paths[0]
    if not pathlib.Path(csv_path).exists():
        print(f"CSV not found: {csv_path}")
        return 2

    from app.database import SessionLocal
    from app.services.product_import_service import ProductImportService
    from app.services.shopify_service import ShopifyService

    text = _read_csv(csv_path)
    db = SessionLocal()
    try:
        imp = ProductImportService(db, None)   # user_id=None → system run
        shop = ShopifyService(db, None)

        # 1) IMPORT (price + cost + vendor availability). Never creates products
        #    (pricing_update_sell refreshes existing only) — new parts go to review.
        res_imp = imp.pricing_update_sell(text, dry_run=not apply)
        _print("IMPORT (pricing_update_sell)" + ("" if apply else " - DRY RUN"), res_imp,
               ["matched", "prices_updated", "costs_updated", "availability_updated",
                "out_of_stock_flagged", "discontinued_deactivated",
                "reactivation_suggested", "skipped_no_product"])

        ids = _csv_product_ids(text, imp)
        print(f"\nCSV touches {len(ids)} existing ERP products.")

        if not apply:
            print("\nDRY RUN - nothing imported or pushed. The counts above are what "
                  "--apply WOULD write; it would then hide the now-OOS parts that are "
                  "currently live on Shopify and re-list any we previously hid.")
            return 0

        if not shop.is_configured():
            print("\nIMPORT applied, but Shopify is NOT configured (store URL or token "
                  "missing) - storefront NOT updated.")
            return 1
        # is_configured() can be True even with no key: the token is Fernet-encrypted
        # and _token() returns the raw 'enc:...' ciphertext when JAKS_FERNET_KEY is
        # unset, which reads as "configured". Catch that explicitly so we never sit in
        # an imported-but-not-pushed half-state behind a misleading success.
        if shop._token().startswith("enc:"):
            print("\nIMPORT applied, but the Shopify token is still ENCRYPTED - "
                  "JAKS_FERNET_KEY is not set in this environment so it can't be "
                  "decrypted. Storefront NOT updated. Set the key (the .bat does this "
                  "automatically) and re-run: scripts.audit_shopify_availability --apply.")
            return 1

        # 1b) Optional: fill missing SEO for this section BEFORE the push, so the
        #     parts go live with copy. Off by default (it costs Anthropic tokens).
        if do_seo:
            from app.services.ai_description_service import AIDescriptionService
            ai = AIDescriptionService(db, None)
            if not ai.is_configured():
                print("\n--seo requested, but no Anthropic key is configured (or "
                      "JAKS_FERNET_KEY is unset) - skipping SEO.")
            else:
                seo_res = ai.backfill_seo(ids)
                _print("SEO BACKFILL", seo_res,
                       ["considered", "generated", "skipped_have_seo", "failed",
                        "tokens_in", "tokens_out"])

        # 2) PUSH — make the storefront match. reconcile-only = availability only
        #    (fast); otherwise also refresh price/stock for this section's parts.
        if reconcile_only:
            res = shop.reconcile_availability(ids)
            _print("RECONCILE (hide / re-list)", res,
                   ["considered", "hidden", "relisted", "failed"])
        else:
            res = shop.sync_linked(ids)
            _print("PUSH (reconcile + price/SEO/stock)", res,
                   ["products", "hidden", "relisted", "content_updated",
                    "stock_synced", "price_skipped", "failed"])
        print(f"\nDone. Storefront updated: {res.get('hidden', 0)} hidden, "
              f"{res.get('relisted', 0)} re-listed, {res.get('failed', 0)} failed.")
        return 0 if res.get("ok", True) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
