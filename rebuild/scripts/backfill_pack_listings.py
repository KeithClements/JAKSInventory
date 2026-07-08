"""
scripts/backfill_pack_listings.py
=================================
One-time backfill for vendor sell packs (2026-07-01). ~3,700 live listings for
pack_qty > 1 parts were published PER-PIECE — priced and buyable below the
vendor's minimum sell pack (order #1007-F1: sold 2 x $3.26 of a part PAI only
sells as a $12.10 5-pack). ShopifyService.build_listing is now pack-aware; this
script converts the affected LINKED listings on the live store.

The apply is SURGICAL — live titles/descriptions are SEO-curated on Shopify, so
a full re-publish (productSet) would clobber them. Instead, per product:
  1. bulk-prefetched live title/description get the pack marker APPENDED
     (ShopifyService._pack_content_updates — idempotent, self-healing);
  2. variant price -> the PACK price (unit x pack_qty);
  3. inventory item -> pack cost + pack weight (weight drives the storefront's
     weight-based shipping brackets);
  4. tracked listings get stock re-synced in whole packs.
Missing variant / inventory-item GIDs found during the prefetch are cached back
onto the ERP rows (same linkage backfill match_and_link performs).

Default is a READ-ONLY report + CSV (nothing pushed). Pass --apply to act.

Run (report):  .venv\\Scripts\\python.exe -m scripts.backfill_pack_listings
Run (apply):   .venv\\Scripts\\python.exe -m scripts.backfill_pack_listings --apply
Options:
  --apply        actually convert the live listings (default: dry-run report)
  --sku SKU      limit to one or more SKUs (repeatable) — e.g. verify one part
                 end-to-end before the fleet: --apply --sku JAKS-PAI-331351
  --limit N      cap how many products are pushed this run (apply in slices)
  --csv PATH     where to write the report CSV (default .shopify-work/pack_backfill_report.csv)

Suspect flag: price/cost ratio >= 2.5 usually means the UNIT price is a cheap-part
minimum-price floor ($0.59 washer floored to $3.04). Multiplying a floored unit
price by a 50-pack can OVERPRICE the pack (never underprice — the floor is above
cost). Those rows still convert, but review them for a fairer pack price.
"""
from __future__ import annotations

import csv
import pathlib
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_SUSPECT_RATIO = 2.5

_BULK_CONTENT_QUERY = (
    "query packBulk($ids: [ID!]!) { nodes(ids: $ids) { ... on Product {"
    "  id title descriptionHtml"
    "  variants(first: 1) { nodes { id inventoryItem { id } } }"
    "} } }"
)
_INV_ITEM_UPDATE = (
    "mutation packInvItem($id: ID!, $input: InventoryItemInput!) {"
    "  inventoryItemUpdate(id: $id, input: $input) {"
    "    inventoryItem { id } userErrors { field message } } }"
)


def _gql(svc, query: str, variables: dict, *, tries: int = 4) -> dict:
    """_graphql with a THROTTLED backoff — the bulk conversion sustains ~3 calls
    per product and must ride out Shopify's leaky bucket, not die on it."""
    for attempt in range(tries):
        d = svc._graphql(query, variables)
        if "THROTTLED" not in str(d.get("errors") or ""):
            return d
        time.sleep(2.0 * (attempt + 1))
    return d


def _rows(db):
    from app.models.product import Product
    q = (db.query(Product)
           .filter(Product.pack_qty > 1,
                   Product.is_active == True,             # noqa: E712
                   Product.shopify_product_id.like("gid://%"))
           .order_by(Product.pack_qty.desc(), Product.sku))
    return q.all()


def _prefetch_live(svc, products) -> dict:
    """gid -> {title, descriptionHtml, variant_id, inventory_item_id} for every
    linked pack product, 100 ids per call."""
    out: dict = {}
    ids = [p.shopify_product_id for p in products]
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        d = _gql(svc, _BULK_CONTENT_QUERY, {"ids": chunk})
        for node in ((d.get("data") or {}).get("nodes") or []):
            if not node or not node.get("id"):
                continue
            v = ((node.get("variants") or {}).get("nodes") or [{}])[0] or {}
            out[node["id"]] = {
                "title": node.get("title") or "",
                "descriptionHtml": node.get("descriptionHtml") or "",
                "variant_id": v.get("id") or "",
                "inventory_item_id": ((v.get("inventoryItem") or {}).get("id")) or "",
            }
        print(f"  prefetched {min(start + 100, len(ids))}/{len(ids)} live listings",
              flush=True)
    return out


def _convert_one(svc, p, live: dict) -> dict:
    """Convert ONE live listing to its pack form. Returns per-step ok flags."""
    pack = max(1, int(p.pack_qty or 1))
    L = svc.build_listing(p)          # pack price/cost/weight
    pid = p.shopify_product_id
    res = {"sku": p.sku, "content": True, "price": False, "item": True, "error": ""}

    # 1) pack marker appended to the LIVE (curated) title/description
    upd = svc._pack_content_updates(pid, pack, live=live)
    if upd:
        d = _gql(svc, svc._PRODUCT_UPDATE, {"input": {"id": pid, **upd}})
        errs = (((d.get("data") or {}).get("productUpdate") or {}).get("userErrors")
                or d.get("errors"))
        if errs:
            res["content"] = False
            res["error"] = f"content: {str(errs)[:160]}"

    # 2) variant price -> pack price (cache a variant GID found by the prefetch)
    vid = (p.shopify_variant_id or "").strip()
    if not vid.startswith("gid://") and live.get("variant_id"):
        vid = p.shopify_variant_id = live["variant_id"]
    if vid.startswith("gid://"):
        d = _gql(svc, svc._VARIANT_PRICE_UPDATE,
                 {"productId": pid,
                  "variants": [{"id": vid, "price": f'{L["price"]:.2f}'}]})
        verrs = (((d.get("data") or {}).get("productVariantsBulkUpdate") or {})
                 .get("userErrors") or d.get("errors"))
        if verrs:
            res["error"] = (res["error"] + f" price: {str(verrs)[:160]}").strip()
        else:
            res["price"] = True

    # 3) inventory item -> pack cost + pack weight (shipping brackets).
    # The prefetched GID is used TRANSIENTLY — deliberately NOT cached onto the
    # ERP row: the nightly sync_inventory_all_linked keys off that column, and
    # newly caching ~4k special-order items would let its self-heal enable
    # inventory tracking + set on-hand 0 on listings whose inventory policy we
    # haven't audited (could block sales). Cost/weight need no tracking.
    iid = (p.shopify_inventory_item_id or "").strip()
    if not iid.startswith("gid://"):
        iid = live.get("inventory_item_id") or ""
    item_input: dict = {}
    if L["cost"] > 0:
        item_input["cost"] = f'{L["cost"]:.2f}'
    if L["weight_lbs"] > 0:
        item_input["measurement"] = {
            "weight": {"value": round(L["weight_lbs"], 3), "unit": "POUNDS"}}
    if iid.startswith("gid://") and item_input:
        d = _gql(svc, _INV_ITEM_UPDATE, {"id": iid, "input": item_input})
        ierrs = (((d.get("data") or {}).get("inventoryItemUpdate") or {})
                 .get("userErrors") or d.get("errors"))
        if ierrs:
            res["item"] = False
            res["error"] = (res["error"] + f" item: {str(ierrs)[:160]}").strip()
    return res


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    skus: list[str] = []
    limit = 0
    csv_path = ".shopify-work/pack_backfill_report.csv"
    it = iter(range(len(argv)))
    for i in it:
        if argv[i] == "--sku" and i + 1 < len(argv):
            skus.append(argv[i + 1].strip()); next(it, None)
        elif argv[i] == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1]); next(it, None)
        elif argv[i] == "--csv" and i + 1 < len(argv):
            csv_path = argv[i + 1]; next(it, None)

    from app.database import SessionLocal
    from app.services.shopify_service import ShopifyService

    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)   # user_id=None → system run (permission bypass)
        products = _rows(db)
        if skus:
            want = {s.upper() for s in skus}
            products = [p for p in products if (p.sku or "").upper() in want]
        print(f"pack products linked to Shopify: {len(products)}")

        # ── report ──────────────────────────────────────────────────────────
        out = pathlib.Path(csv_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        n_suspect = n_active = 0
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["sku", "pack_qty", "unit_cost", "unit_price", "pack_price",
                        "pack_cost", "price_cost_ratio", "suspect_floor_price",
                        "unit_weight_lbs", "pack_weight_lbs", "shopify_status",
                        "qty_on_hand", "new_title"])
            for p in products:
                L = svc.build_listing(p)
                pack = max(1, int(p.pack_qty or 1))
                unit_price = round(float(p.selling_price or 0), 2)
                unit_cost = round(L["cost"] / pack, 2) if L["cost"] else 0.0
                ratio = (unit_price / unit_cost) if unit_cost > 0 else 0.0
                suspect = ratio >= _SUSPECT_RATIO
                n_suspect += suspect
                n_active += (p.shopify_status or "") == "ACTIVE"
                w.writerow([p.sku, pack, unit_cost, unit_price, L["price"], L["cost"],
                            round(ratio, 2), "YES" if suspect else "",
                            round(float(p.weight_lbs or 0), 3), round(L["weight_lbs"], 3),
                            p.shopify_status or "", int(p.qty_on_hand or 0), L["title"]])
        print(f"report written: {out}")
        print(f"  ACTIVE on store: {n_active}")
        print(f"  suspect floor-price rows (ratio >= {_SUSPECT_RATIO}): {n_suspect}")

        if not apply:
            print("\nDRY-RUN ONLY — nothing pushed. Re-run with --apply to convert "
                  "the live listings.")
            return 0

        # ── apply (surgical — see module docstring) ─────────────────────────
        if not svc.is_configured():
            print("Shopify not configured — set shopify_store_url + "
                  "shopify_access_token in Settings first.")
            return 2
        todo = products[:limit] if limit else products
        # Stock re-sync stays scoped to items the ERP ALREADY tracked before this
        # run (see the transient-GID note in _convert_one).
        pre_tracked = [p.id for p in todo
                       if (p.shopify_inventory_item_id or "").startswith("gid://")]
        print(f"\nAPPLY: prefetching live content for {len(todo)} listings...")
        live_map = _prefetch_live(svc, todo)

        print(f"converting {len(todo)} listings (marker + pack price + cost/weight)...")
        ok = fail = 0
        errors: list = []
        for i, p in enumerate(todo, 1):
            live = live_map.get(p.shopify_product_id)
            if live is None:
                fail += 1
                errors.append({"sku": p.sku, "error": "listing not found on store"})
                continue
            r = _convert_one(svc, p, live)
            if r["content"] and r["price"] and r["item"]:
                ok += 1
            else:
                fail += 1
                errors.append({"sku": r["sku"], "error": r["error"] or "price not synced"})
            if i % 25 == 0 or i == len(todo):
                db.commit()          # persist GID linkage backfills as we go
                print(f"  [{i}/{len(todo)}] converted={ok} failed={fail}", flush=True)
        db.commit()
        print(f"conversion done: converted={ok} failed={fail}")
        for e in errors[:12]:
            print(f"  error: {e}")

        if pre_tracked:
            print(f"stock re-sync (whole packs) for {len(pre_tracked)} "
                  f"already-tracked items...")
            inv = svc.sync_inventory(pre_tracked)
            print(f"  synced={inv.get('synced')} skipped={inv.get('skipped')} "
                  f"failed={inv.get('failed')}")
        return 0 if fail == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
