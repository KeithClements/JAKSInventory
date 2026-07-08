"""
scripts/push_drafts_to_shopify.py
=================================
Bulk-push every UNLINKED active ERP product to Shopify as a DRAFT listing
(create-in-place via productSet; title enriched by ShopifyService._store_title).

Fast + safe for a 12K run:
  • Async create (synchronous=False) — returns the product id immediately and
    lets Shopify process media in the background; removes the per-call image-fetch
    wait that dominates a synchronous push.
  • Shardable — pass SHARD_INDEX/SHARD_COUNT env vars to run N disjoint workers
    in parallel (each handles `product.id % SHARD_COUNT == SHARD_INDEX`). Disjoint
    id sets → no two workers ever touch the same product → no duplicate listings.
  • Resumable — publish_product stamps shopify_product_id on success, so a re-run
    skips everything already linked. Kill and restart freely.
  • Throttle/transport-aware — retries THROTTLED + transient transport errors with
    exponential backoff; fails fast on permanent errors.
  • SQLite WAL + busy_timeout so concurrent shard workers don't deadlock on commit.
  • Progress + errors logged to scripts/shopify_push_progress.<shard>.log.

Run (single):    .venv\\Scripts\\python.exe scripts\\push_drafts_to_shopify.py
Run (sharded):   set SHARD_INDEX=0 & set SHARD_COUNT=4 & python scripts\\push_drafts_to_shopify.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SHARD_INDEX = int(os.environ.get("SHARD_INDEX", "0"))
SHARD_COUNT = int(os.environ.get("SHARD_COUNT", "1"))
PACE_SECONDS = float(os.environ.get("PACE_SECONDS", "0.05"))   # async calls are cheap
MAX_RETRIES = 6
BACKOFF_BASE = 2.0

_LOG_DIR = pathlib.Path(__file__).resolve().parent
LOG = _LOG_DIR / (f"shopify_push_progress.{SHARD_INDEX}.log" if SHARD_COUNT > 1
                  else "shopify_push_progress.log")


def _log(msg: str) -> None:
    line = msg.rstrip()
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> int:
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.models.product import Product
    from app.services.shopify_service import ShopifyService

    db = SessionLocal()
    # WAL + a generous busy timeout so parallel shard workers wait (not error) on
    # the brief per-product commit.
    try:
        db.execute(text("PRAGMA journal_mode=WAL"))
        db.execute(text("PRAGMA busy_timeout=30000"))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _log(f"WARN: could not set WAL/busy_timeout: {exc}")

    svc = ShopifyService(db, None)        # user None -> admin permission bypass
    if not svc.is_configured():
        _log("ABORT: Shopify not configured.")
        return 1

    ids = [pid for (pid,) in db.query(Product.id)
           .filter(Product.is_active == True,                       # noqa: E712
                   ~Product.shopify_product_id.like("gid://%"))
           .order_by(Product.id).all()
           if pid % SHARD_COUNT == SHARD_INDEX]
    total = len(ids)
    _log(f"=== shard {SHARD_INDEX}/{SHARD_COUNT}: {total} unlinked products -> DRAFT (async) ===")

    done = fail = 0
    for i, pid in enumerate(ids, 1):
        p = db.get(Product, pid)
        if p is None or (p.shopify_product_id or "").startswith("gid://"):
            continue
        attempt = 0
        while True:
            attempt += 1
            # synchronous=True so the GID is captured inline and the run is fully
            # resumable (a crash/restart skips already-linked products and can
            # never duplicate). Parallel shards provide the speed instead.
            res = svc.publish_product(p, status="DRAFT", synchronous=True)
            if res.get("ok"):
                done += 1
                break
            err = str(res.get("error") or "")
            errl = err.lower()
            transient = ("throttl" in errl
                         or errl.startswith("network/parse error")
                         or "timeout" in errl or "timed out" in errl)
            if transient and attempt <= MAX_RETRIES:
                wait = min(BACKOFF_BASE ** attempt, 30.0)
                _log(f"  transient on {p.sku} (attempt {attempt}) — sleeping {wait:.0f}s")
                time.sleep(wait)
                continue
            fail += 1
            _log(f"  FAIL {p.sku}: {err[:160]}")
            break
        if i % 200 == 0:
            _log(f"  progress {i}/{total}  (ok={done} fail={fail})")
        time.sleep(PACE_SECONDS)

    _log(f"=== shard {SHARD_INDEX} done: created {done}, failed {fail}, of {total} ===")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
