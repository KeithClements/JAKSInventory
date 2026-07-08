"""
scripts/shopify_sync_now.py
===========================
The "Sync now" button as a long-running script. Walks every active product
that's already linked to a Shopify listing and re-pushes the safe partial
update (tags + SEO + VENDOR + variant price) plus inventory level. Same
worker the nightly auto-sync calls — exposed here so the owner can kick a
refresh from the terminal and watch its progress in a log file.

Vendor moved into the partial update in 2026-06-15 alongside the
manufacturer-canonicalization fix, so this run carries the
Product.manufacturer refresh from the latest Pricing-Update import out to
Shopify's storefront vendor facet. Price + stock continue to refresh as
before.

Resumable + safe to interrupt:
  * publish_product is never called — no new listings ever appear.
  * Each product is one productUpdate + one inventory adjustment; nothing
    is staged in the DB before the call, so Ctrl-C never leaves a
    half-written row.

Progress + errors stream to scripts/shopify_sync_now_progress.log AND stdout.

Run:    .venv\\Scripts\\python.exe scripts\\shopify_sync_now.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

PACE_SECONDS = float(os.environ.get("PACE_SECONDS", "0.10"))
BATCH = int(os.environ.get("BATCH", "200"))

_LOG = pathlib.Path(__file__).resolve().parent / "shopify_sync_now_progress.log"


def _log(msg: str) -> None:
    line = msg.rstrip()
    print(line, flush=True)
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> int:
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.models.product import Product
    from app.services.shopify_service import ShopifyService

    db = SessionLocal()
    try:
        db.execute(text("PRAGMA journal_mode=WAL"))
        db.execute(text("PRAGMA busy_timeout=30000"))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _log(f"WARN: could not set WAL/busy_timeout: {exc}")

    svc = ShopifyService(db, None)
    if not svc.is_configured():
        _log("ABORT: Shopify not configured.")
        return 1

    ids = [pid for (pid,) in db.query(Product.id)
           .filter(Product.is_active == True,                       # noqa: E712
                   Product.shopify_product_id.like("gid://%"))
           .order_by(Product.id).all()]
    total = len(ids)
    _log(f"=== shopify sync_now: {total:,} linked products ===")

    total_updated = total_price_skipped = total_failed = 0
    total_stock_synced = total_stock_failed = 0
    for offset in range(0, total, BATCH):
        chunk = ids[offset:offset + BATCH]
        content = svc.update_batch(chunk)
        stock = svc.sync_inventory(chunk)
        total_updated += content.get("updated", 0)
        total_price_skipped += content.get("price_skipped", 0)
        total_failed += content.get("failed", 0)
        total_stock_synced += stock.get("synced", 0)
        total_stock_failed += stock.get("failed", 0)
        _log(f"  batch {offset + len(chunk):>6,}/{total:,}  "
             f"content_ok={total_updated:,}  price_skipped={total_price_skipped:,}  "
             f"stock_ok={total_stock_synced:,}  failed={total_failed + total_stock_failed:,}")
        for err in (content.get("errors") or [])[:3]:
            _log(f"    content err: {err}")
        for err in (stock.get("errors") or [])[:3]:
            _log(f"    stock err:   {err}")
        if PACE_SECONDS > 0:
            time.sleep(PACE_SECONDS)

    _log(f"=== done: content_updated={total_updated:,}  "
         f"price_skipped={total_price_skipped:,}  "
         f"stock_synced={total_stock_synced:,}  "
         f"failed={total_failed + total_stock_failed:,} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
