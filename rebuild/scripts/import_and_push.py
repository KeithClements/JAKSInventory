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
  --json             on a DRY RUN, after the human-readable report, print ONE extra
                     machine-readable line ("RESULT_JSON: {…}") with the full
                     CROSS-REPO CONTRACT metric set (matched / *_updated counts plus
                     the price/cost blast-radius magnitudes) so the scraper can gate
                     the push on the size of the change. No effect under --apply.
  --new-products-out <path>
                     on a DRY RUN, write the SKUs that matched no ERP product (the
                     skipped_no_product rows) to <path> as a one-column CSV
                     ("sku" header), so the scraper can route them to onboarding.

NOTE: the Shopify token is Fernet-encrypted, so JAKS_FERNET_KEY must be set in the
environment (the .bat sets it) or the push step reports "not configured".
"""
from __future__ import annotations

import pathlib
import sys

# Force UTF-8 stdout so a legacy .bat console (cp1252) never crashes on output.
for _s in (sys.stdout, sys.stderr):
    try:
        # line_buffering=True flushes each line as it is printed so the AxleForge
        # job bar can stream progress live (the launcher also sets PYTHONUNBUFFERED).
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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


def _make_progress():
    """A throttled progress printer for the long push loops. The push services call
    it as ``cb(stage, done, total)`` on every item; we emit at most ~50 ``[done/total]``
    lines per stage (plus the first and the last) so the live AxleForge job bar — which
    advances on the ``[n/m]`` marker — moves without flooding the run log."""
    state = {"stage": None}

    def cb(stage, done, total):
        if not total or total <= 0:
            return
        new_stage = stage != state["stage"]
        if new_stage:
            state["stage"] = stage
        step = max(1, total // 50)
        if new_stage or done >= total or done % step == 0:
            print(f"  [{done}/{total}] {stage}", flush=True)

    return cb


def _result_json(res: dict) -> str:
    """Build the single CROSS-REPO CONTRACT RESULT_JSON line from a dry-run
    pricing_update_sell summary. Every key in the contract is emitted; any
    missing numeric defaults to 0 so the scraper never sees an absent key.
    total_rows falls back to the summary's "rows" (same value, contract name)."""
    import json
    int_keys = ("matched", "prices_updated", "costs_updated",
                "availability_updated", "out_of_stock_flagged",
                "discontinued_deactivated", "reactivation_suggested",
                "skipped_no_product", "reprice_rows", "cost_anomaly_rows",
                "skipped_price_locked")
    float_keys = ("price_drop_max_pct", "price_rise_max_pct", "cost_rise_max_pct")
    payload: dict = {k: int(res.get(k, 0) or 0) for k in int_keys}
    payload["total_rows"] = int(res.get("total_rows", res.get("rows", 0)) or 0)
    for k in float_keys:
        payload[k] = float(res.get(k, 0.0) or 0.0)
    # Pin the contract's exact key ORDER for a stable, greppable line.
    ordered = {
        "matched": payload["matched"],
        "prices_updated": payload["prices_updated"],
        "costs_updated": payload["costs_updated"],
        "availability_updated": payload["availability_updated"],
        "out_of_stock_flagged": payload["out_of_stock_flagged"],
        "discontinued_deactivated": payload["discontinued_deactivated"],
        "reactivation_suggested": payload["reactivation_suggested"],
        "skipped_no_product": payload["skipped_no_product"],
        "total_rows": payload["total_rows"],
        "price_drop_max_pct": payload["price_drop_max_pct"],
        "price_rise_max_pct": payload["price_rise_max_pct"],
        "reprice_rows": payload["reprice_rows"],
        "cost_rise_max_pct": payload["cost_rise_max_pct"],
        "cost_anomaly_rows": payload["cost_anomaly_rows"],
        # Additive (scraper tolerates extra keys; existing keys NOT reordered):
        # rows whose sell price was skipped because an ERP operator hand-set it
        # (products.price_override_locked).
        "skipped_price_locked": payload["skipped_price_locked"],
    }
    return "RESULT_JSON: " + json.dumps(ordered, separators=(",", ":"))


def _write_new_products(path: str, skus: list[str]) -> None:
    """Write the no-match SKUs (skipped_no_product rows) to a one-column CSV so
    the scraper can route them to onboarding. Header 'sku' + one row per SKU."""
    import csv as _csv
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["sku"])
        for sku in skus:
            w.writerow([sku])


def _print(title: str, res: dict, keys: list[str]) -> None:
    print(f"\n== {title} ==")
    for k in keys:
        if k in res:
            print(f"  {k}: {res[k]}")
    if res.get("errors"):
        print("  errors:")
        for e in res["errors"][:8]:
            print(f"    - {e}")


def _opt_value(argv: list[str], name: str) -> str | None:
    """Pull the value for a ``--name <value>`` option (space form only — matches
    the simple flag style this script already uses). Returns None if absent."""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    reconcile_only = "--reconcile-only" in argv
    do_seo = "--seo" in argv
    want_json = "--json" in argv
    new_products_out = _opt_value(argv, "--new-products-out")
    # Bare positionals are the CSV path — but the value that FOLLOWS
    # --new-products-out is its argument, not the CSV, so exclude it.
    paths = [a for a in argv if not a.startswith("-") and a != new_products_out]
    if not paths:
        print("usage: python -m scripts.import_and_push <csv> [--apply] "
              "[--reconcile-only] [--json] [--new-products-out <path>]")
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
        print("Importing CSV (price + cost + availability)…", flush=True)
        res_imp = imp.pricing_update_sell(text, dry_run=not apply)
        _print("IMPORT (pricing_update_sell)" + ("" if apply else " - DRY RUN"), res_imp,
               ["matched", "prices_updated", "costs_updated", "availability_updated",
                "out_of_stock_flagged", "discontinued_deactivated",
                "reactivation_suggested", "skipped_no_product",
                "skipped_price_locked"])

        ids = _csv_product_ids(text, imp)
        print(f"\nCSV touches {len(ids)} existing ERP products.")

        if not apply:
            print("\nDRY RUN - nothing imported or pushed. The counts above are what "
                  "--apply WOULD write; it would then hide the now-OOS parts that are "
                  "currently live on Shopify and re-list any we previously hid.")
            # --new-products-out: hand the no-match SKUs to the scraper for onboarding.
            if new_products_out:
                skus = res_imp.get("skipped_skus") or []
                _write_new_products(new_products_out, skus)
                print(f"\nWrote {len(skus)} new-product SKU(s) to {new_products_out}")
            # --json: one machine-readable contract line for the scraper's push gate.
            if want_json:
                print(_result_json(res_imp))
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
        #    Pass a throttled progress printer so the live job bar moves during the
        #    long per-product loops (otherwise the run is silent until it finishes).
        prog = _make_progress()
        if reconcile_only:
            res = shop.reconcile_availability(ids, progress=prog)
            _print("RECONCILE (hide / re-list)", res,
                   ["considered", "hidden", "relisted", "failed"])
        else:
            res = shop.sync_linked(ids, progress=prog)
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
