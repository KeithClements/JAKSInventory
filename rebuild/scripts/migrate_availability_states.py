"""
scripts/migrate_availability_states.py
======================================
Stage the storefront onto the SOLD-OUT availability model (owner decision
2026-07-04): a part is a LIVE 'Sold out' page when it's out everywhere (tracked,
qty 0, unbuyable) instead of a 404 DRAFT, and buyable whenever we have it OR any
vendor stocks it. See app/services/availability_policy.py.

Two safe stages:

  1. DRY-RUN (default) — DB-only preview of what the whole catalog WOULD become:
     how many listings become in-stock / available-to-order / sold-out / hidden,
     how many currently-hidden pages get RE-LISTED, how many active listings get
     hidden. Nothing on the store changes. Add --refresh to first pull each
     listing's LIVE status so the hide/relist counts reflect the real store.

  2. --apply — flip the store's model to 'sold_out' and bring every linked listing
     into line (publish status, inventory policy, tracked qty, availability_state
     metafield) via ShopifyService.apply_listing_state. Resumable (a checkpoint
     records the last product id) and chunk-committing, so a kill/timeout re-runs
     only the remainder. Idempotent — re-running converges.

Run (preview):    .venv\\Scripts\\python.exe -m scripts.migrate_availability_states
Run (live prev.): .venv\\Scripts\\python.exe -m scripts.migrate_availability_states --refresh
Run (apply):      set JAKS_FERNET_KEY=... & .venv\\Scripts\\python.exe -m scripts.migrate_availability_states --apply
Options:
  --apply        convert the live store + set shopify_availability_mode=sold_out
  --refresh      read live status first (authoritative; ON by default for --apply)
  --no-refresh   force --apply to trust the cached status (faster, less safe)
  --limit N      only process N products this run (staged rollout / smoke test)
  --restart      ignore any saved checkpoint and start from the beginning
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_CHECKPOINT = pathlib.Path(__file__).resolve().parents[1] / "data" / ".migrate_states_resume.json"


def _load_checkpoint(restart: bool) -> int:
    if restart or not _CHECKPOINT.exists():
        return 0
    try:
        return int(json.loads(_CHECKPOINT.read_text()).get("last_id", 0))
    except Exception:
        return 0


def _save_checkpoint(last_id: int) -> None:
    try:
        _CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        _CHECKPOINT.write_text(json.dumps({"last_id": last_id}))
    except Exception:
        pass


def _arg_int(argv: list[str], flag: str, default: int | None) -> int | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                pass
    return default


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    no_refresh = "--no-refresh" in argv
    refresh_flag = "--refresh" in argv
    restart = "--restart" in argv
    limit = _arg_int(argv, "--limit", None)
    do_refresh = (apply and not no_refresh) or (not apply and refresh_flag)

    from app.database import SessionLocal
    from app.models.product import Product
    from app.services.availability_policy import desired_state
    from app.services.shopify_service import ShopifyService
    from app.settings_utils import set_setting_value_db

    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)   # system run (permission bypass)
        if not svc.is_configured():
            print("Shopify not configured — set the store URL + access token in "
                  "Settings → Shopify first.")
            return 2

        if do_refresh:
            print("Refreshing live Shopify status (read-only walk of the store)...")
            ref = svc.refresh_live_status()
            print(f"  linked_seen={ref.get('linked_seen')} "
                  f"status_updated={ref.get('status_updated')} ok={ref.get('ok')}")
            if not ref.get("ok") and apply:
                print("\nAborting: could not read live status. Re-run with "
                      "--no-refresh to trust the cache.")
                return 3

        # ── DRY-RUN preview (DB only, no store writes) ──────────────────────────
        if not apply:
            states: Counter = Counter()
            would_hide = would_relist = 0
            for (p,) in db.query(Product).filter(
                    Product.shopify_product_id.like("gid://%")).yield_per(500):
                ds = desired_state(p)
                states[ds.state] += 1
                live = (p.shopify_status or "").strip().upper()
                if ds.hidden and live == "ACTIVE":
                    would_hide += 1
                elif (not ds.hidden) and live == "DRAFT" and p.shopify_hidden_by_erp:
                    would_relist += 1
            total = sum(states.values())
            print(f"\n== PLAN (dry-run — nothing changed) — {total} linked listings ==")
            for st in ("in_stock", "available_to_order", "sold_out", "hidden"):
                print(f"  {st:<20}: {states.get(st, 0)}")
            print(f"  → would RE-LIST (sold-out pages the ERP hid): {would_relist}")
            print(f"  → would HIDE (newly discontinued still ACTIVE): {would_hide}")
            print("\nDry run. Re-run with --apply to convert the live store "
                  "(sets shopify_availability_mode=sold_out).")
            return 0

        # ── APPLY ──────────────────────────────────────────────────────────────
        # Flip the model FIRST so apply_listing_state's price update writes the
        # sold-out inventory policy + availability_state metafield.
        set_setting_value_db(db, "shopify_availability_mode", "sold_out")
        db.commit()
        print("shopify_availability_mode = sold_out (set)")

        last_id = _load_checkpoint(restart)
        if last_id:
            print(f"Resuming after product id {last_id} (use --restart to start over).")

        q = (db.query(Product)
             .filter(Product.shopify_product_id.like("gid://%"),
                     Product.id > last_id)
             .order_by(Product.id))
        if limit:
            q = q.limit(limit)
        rows = q.all()
        print(f"Applying to {len(rows)} listing(s)...")

        done = failed = 0
        states = Counter()
        for i, p in enumerate(rows, 1):
            res = svc.apply_listing_state(p)
            states[res.get("state", "?")] += 1
            if res.get("ok"):
                done += 1
            else:
                failed += 1
                if failed <= 15:
                    print(f"  FAIL {p.sku}: {res.get('errors') or res.get('error')}")
            _save_checkpoint(p.id)
            if i % 200 == 0:
                print(f"  ...{i}/{len(rows)} (ok={done} failed={failed})")

        print(f"\nDone. Applied ok={done} failed={failed}. "
              f"States: {dict(states)}")
        if not limit and failed == 0:
            try:
                _CHECKPOINT.unlink()
            except Exception:
                pass
        return 0 if failed == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
