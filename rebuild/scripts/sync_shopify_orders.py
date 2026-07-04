"""
scripts/sync_shopify_orders.py
==============================
Pull Shopify web orders into the ERP and decrement stock for each sold line (see
app/services/shopify_order_sync.py). Idempotent — each order is processed once.

Default is a DRY-RUN (matches + would-decrement counts; no stock change, no
watermark advance). Pass --apply to actually decrement.

Requires the Shopify token to have the read_orders scope. If it doesn't, the run
prints a clear scope error telling you to add it.

Run (dry-run): .venv\\Scripts\\python.exe -m scripts.sync_shopify_orders
Run (apply):   .venv\\Scripts\\python.exe -m scripts.sync_shopify_orders --apply
Options:
  --apply        actually decrement ERP stock + advance the watermark
  --max N        stop after N orders (smoke test / staged catch-up)
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    max_orders = None
    if "--max" in argv:
        i = argv.index("--max")
        if i + 1 < len(argv):
            try:
                max_orders = int(argv[i + 1])
            except ValueError:
                pass

    from app.database import SessionLocal
    from app.services.shopify_order_sync import ShopifyOrderSyncService

    db = SessionLocal()
    try:
        svc = ShopifyOrderSyncService(db, None)   # system run
        if not svc.is_configured():
            print("Shopify not configured — set the store URL + access token first.")
            return 2
        res = svc.poll(dry_run=not apply, max_orders=max_orders)
        mode = "APPLIED" if apply else "DRY-RUN (nothing changed)"
        print(f"\n== Shopify order sync — {mode} ==")
        for k in ("orders_seen", "orders_processed", "orders_skipped",
                  "lines_matched", "lines_unmatched", "pieces_decremented",
                  "watermark_in", "watermark_out"):
            if k in res:
                print(f"  {k}: {res[k]}")
        if res.get("scope_error"):
            print(f"\n  SCOPE ERROR: {res['scope_error']}")
        for e in res.get("errors", []):
            print(f"  error: {e}")
        if not res.get("ok"):
            print(f"\n  FAILED: {res.get('error', '')}")
            return 1
        if not apply:
            print("\nDry run. Re-run with --apply to decrement ERP stock.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
