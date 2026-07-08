"""
scripts/fix_stale_pack_links.py
==============================
Fix the stale-link stragglers from the pack backfill (2026-07-01). For 47 pack
products the ERP's shopify_product_id points at an ORPHAN/duplicate Shopify
product; the real live listing (matched by SKU) is a different product that the
backfill never touched — so it's still priced per-piece (still losing money).

Reads .shopify-work/pack_verify_report.csv (verdict == stale_link) and, against
each row's LIVE product (live_product_id):
  1. appends the pack marker to the live title/description (read-modify-write);
  2. sets the live variant price to the pack price;
  3. sets the live inventory item's cost + pack weight;
  4. REPOINTS the ERP row (shopify_product_id/variant_id/inventory_item_id) to the
     live product, so every future sync hits the right listing.
Also reports each orphan's status so the owner can decide on duplicate cleanup.

Default DRY-RUN. Pass --apply to write. READ-ONLY report otherwise.
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

_LIVE_PRODUCT_QUERY = (
    "query stalePack($id: ID!) { product(id: $id) {"
    "  id title descriptionHtml status"
    "  variants(first: 1) { nodes { id price inventoryItem { id } } } } }"
)
_ORPHAN_STATUS_QUERY = "query orphan($id: ID!) { product(id: $id) { status } }"
_INV_ITEM_UPDATE = (
    "mutation packInvItem($id: ID!, $input: InventoryItemInput!) {"
    "  inventoryItemUpdate(id: $id, input: $input) {"
    "    inventoryItem { id } userErrors { field message } } }"
)


def _gql(svc, q, v, *, tries=4):
    for attempt in range(tries):
        d = svc._graphql(q, v)
        if "THROTTLED" not in str(d.get("errors") or ""):
            return d
        time.sleep(2.0 * (attempt + 1))
    return d


def main(argv) -> int:
    apply = "--apply" in argv
    from app.database import SessionLocal
    from app.services.shopify_service import ShopifyService
    from app.models.product import Product

    rows = [r for r in csv.DictReader(
        open(".shopify-work/pack_verify_report.csv", encoding="utf-8"))
        if r["verdict"] == "stale_link"]
    print(f"stale-link rows to fix: {len(rows)}")

    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)
        if not svc.is_configured():
            print("Shopify not configured."); return 2
        ok = fail = 0
        for r in rows:
            sku = r["sku"]; live_pid = r["live_product_id"]; orphan_pid = r["erp_product_id"]
            p = db.query(Product).filter(Product.sku == sku).one_or_none()
            if not p:
                print(f"  {sku}: ERP row gone, skip"); fail += 1; continue
            pack = max(1, int(p.pack_qty or 1))
            L = svc.build_listing(p)
            d = _gql(svc, _LIVE_PRODUCT_QUERY, {"id": live_pid})
            live = (d.get("data") or {}).get("product")
            if not live:
                print(f"  {sku}: live product {live_pid} not found, skip"); fail += 1; continue
            var = ((live.get("variants") or {}).get("nodes") or [{}])[0]
            osd = _gql(svc, _ORPHAN_STATUS_QUERY, {"id": orphan_pid})
            ostatus = ((osd.get("data") or {}).get("product") or {}).get("status", "?")
            content = svc._pack_content_updates(live_pid, pack, live=live)
            plan = (f"  {sku}: pack={pack} -> live {live_pid.split('/')[-1]} "
                    f"price {var.get('price')}->{L['price']:.2f} "
                    f"(orphan {orphan_pid.split('/')[-1]} status={ostatus})")
            if not apply:
                print(plan); continue

            err = ""
            if content:
                dd = _gql(svc, svc._PRODUCT_UPDATE, {"input": {"id": live_pid, **content}})
                e = (((dd.get("data") or {}).get("productUpdate") or {}).get("userErrors")
                     or dd.get("errors"))
                if e:
                    err += f" content:{str(e)[:120]}"
            vid = var.get("id") or ""
            if vid.startswith("gid://"):
                dd = _gql(svc, svc._VARIANT_PRICE_UPDATE,
                          {"productId": live_pid,
                           "variants": [{"id": vid, "price": f'{L["price"]:.2f}'}]})
                e = (((dd.get("data") or {}).get("productVariantsBulkUpdate") or {})
                     .get("userErrors") or dd.get("errors"))
                if e:
                    err += f" price:{str(e)[:120]}"
            iid = ((var.get("inventoryItem") or {}).get("id")) or ""
            item_input = {}
            if L["cost"] > 0:
                item_input["cost"] = f'{L["cost"]:.2f}'
            if L["weight_lbs"] > 0:
                item_input["measurement"] = {"weight": {
                    "value": round(L["weight_lbs"], 3), "unit": "POUNDS"}}
            if iid.startswith("gid://") and item_input:
                dd = _gql(svc, _INV_ITEM_UPDATE, {"id": iid, "input": item_input})
                e = (((dd.get("data") or {}).get("inventoryItemUpdate") or {})
                     .get("userErrors") or dd.get("errors"))
                if e:
                    err += f" item:{str(e)[:120]}"
            # Repoint ERP linkage to the REAL live listing (fixes future syncs).
            p.shopify_product_id = live_pid
            if vid.startswith("gid://"):
                p.shopify_variant_id = vid
            if iid.startswith("gid://"):
                p.shopify_inventory_item_id = iid
            db.commit()
            if err:
                print(plan + " FAIL:" + err); fail += 1
            else:
                print(plan + " OK"); ok += 1
        if apply:
            print(f"\ndone: fixed={ok} failed={fail}")
        else:
            print("\nDRY-RUN — re-run with --apply to fix the live listings.")
        return 0 if fail == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
