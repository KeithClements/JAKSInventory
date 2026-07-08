"""
scripts/audit_shopify_availability.py
=====================================
Reconcile the LIVE Shopify storefront with vendor supply: hide listings whose
vendor went out_of_stock/discontinued but are still ACTIVE (and buyable), and
re-list parts the ERP itself hid that are back in stock.

This is the one-time backfill / on-demand audit for ShopifyService.reconcile_
availability. The recurring "Sync now" + nightly + section-trigger paths run the
same reconcile automatically; this script is the manual, reviewable entrypoint.

Default is a READ-ONLY report (nothing is changed). Pass --apply to act.

Run (report):        .venv\\Scripts\\python.exe -m scripts.audit_shopify_availability
Run (live report):   .venv\\Scripts\\python.exe -m scripts.audit_shopify_availability --refresh
Run (apply):         .venv\\Scripts\\python.exe -m scripts.audit_shopify_availability --apply
Options:
  --apply        actually hide / re-list on the live store (default: dry-run report)
  --refresh      re-read each linked listing's LIVE status before building the plan,
                 so it reflects the real store, not the ERP's (possibly stale) cache.
                 ON by default for --apply; use this to get a trustworthy DRY-RUN too.
  --no-refresh   force --apply to trust the cache and skip the live-status read
                 (faster; less authoritative).
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


def _print(title: str, res: dict) -> None:
    print(f"\n== {title} ==")
    for k, v in res.items():
        if k in ("sample", "errors"):
            continue
        print(f"  {k}: {v}")
    if res.get("sample"):
        print("  sample:")
        for s in res["sample"]:
            print(f"    - {s.get('action'):<7} {s.get('sku')}")
    if res.get("errors"):
        print("  errors:")
        for e in res["errors"]:
            print(f"    - {e}")


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    no_refresh = "--no-refresh" in argv
    refresh_flag = "--refresh" in argv
    # Refresh live status before building the plan when: applying (unless explicitly
    # opted out), OR the operator asked for a live dry-run with --refresh.
    do_refresh = (apply and not no_refresh) or (not apply and refresh_flag)

    from app.database import SessionLocal
    from app.services.shopify_service import ShopifyService

    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)   # user_id=None → system run (permission bypass)
        if not svc.is_configured():
            print("Shopify not configured — set shopify_store_url + shopify_access_token "
                  "in Settings first.")
            return 2

        if do_refresh:
            print("Refreshing live Shopify status for linked products "
                  "(read-only walk of the store)...")
            ref = svc.refresh_live_status()
            _print("live status refresh", ref)
            if not ref.get("ok"):
                if apply:
                    print("\nAborting: could not read live status. "
                          "Re-run with --no-refresh to trust the cache.")
                    return 3
                print("\nLive refresh failed — falling back to the CACHED plan below.")
                do_refresh = False

        basis = "LIVE (refreshed from the store)" if do_refresh else \
                "CACHED shopify_status — may be stale; add --refresh for a live plan"

        if not apply:
            plan = svc.reconcile_availability(dry_run=True)
            _print(f"PLAN (dry-run — nothing changed) — basis: {basis}", plan)
            print("\nThis was a dry run. Re-run with --apply to hide / re-list on the "
                  "live store.")
            return 0

        res = svc.reconcile_availability(dry_run=False)
        _print(f"APPLIED — basis: {basis}", res)
        print(f"\nDone. Hidden {res.get('hidden', 0)}, re-listed {res.get('relisted', 0)}, "
              f"failed {res.get('failed', 0)}.")
        return 0 if res.get("ok") else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
