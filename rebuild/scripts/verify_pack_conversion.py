"""
scripts/verify_pack_conversion.py
=================================
Post-backfill verification (2026-07-01). Walks EVERY live Shopify variant once
(read-only, by SKU) and, for each pack_qty>1 ERP product, checks the REAL live
listing (matched by SKU, not by the ERP's cached GID):
  - stale_link : ERP shopify_product_id != the live product that actually holds
                 this SKU (the 131852 case — conversion hit an orphan, the live
                 listing was left per-piece);
  - not_converted : live price != expected pack price (still per-piece);
  - ok : live listing carries the pack price.
Writes .shopify-work/pack_verify_report.csv. READ-ONLY — changes nothing.
"""
from __future__ import annotations

import csv
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_VARIANTS_WITH_PRICE = (
    "query($cursor: String) {"
    "  productVariants(first: 250, after: $cursor) {"
    "    pageInfo { hasNextPage endCursor }"
    "    nodes { id sku price product { id status } }"
    "  }"
    "}"
)


def _walk_live(svc) -> dict:
    """sku(upper) -> {product_id, variant_id, price, status} for every live variant."""
    out: dict = {}
    cursor = None
    pages = 0
    while True:
        d = svc._graphql(_VARIANTS_WITH_PRICE, {"cursor": cursor})
        conn = (d.get("data") or {}).get("productVariants") or {}
        for n in conn.get("nodes") or []:
            sku = (n.get("sku") or "").strip().upper()
            if not sku:
                continue
            out[sku] = {"product_id": (n.get("product") or {}).get("id") or "",
                        "variant_id": n.get("id") or "",
                        "price": n.get("price") or "",
                        "status": (n.get("product") or {}).get("status") or ""}
        pages += 1
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
        if pages % 10 == 0:
            print(f"  walked {len(out)} live variants...", flush=True)
    return out


def main() -> int:
    from app.database import SessionLocal
    from app.services.shopify_service import ShopifyService
    from app.models.product import Product

    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)
        if not svc.is_configured():
            print("Shopify not configured.")
            return 2
        print("walking live variants (read-only)...")
        live = _walk_live(svc)
        print(f"live variants indexed by SKU: {len(live)}")

        products = (db.query(Product)
                      .filter(Product.pack_qty > 1, Product.is_active == True,   # noqa: E712
                              Product.shopify_product_id.like("gid://%"))
                      .order_by(Product.sku).all())

        out = pathlib.Path(".shopify-work/pack_verify_report.csv")
        n_ok = n_stale = n_notconv = n_missing = 0
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["sku", "pack_qty", "expected_pack_price", "live_price",
                        "erp_product_id", "live_product_id", "verdict"])
            for p in products:
                L = svc.build_listing(p)
                exp = f'{L["price"]:.2f}'
                lv = live.get((p.sku or "").upper())
                if not lv:
                    verdict = "missing_on_store"; n_missing += 1
                    lp = ""; lpid = ""
                else:
                    lp = lv["price"]; lpid = lv["product_id"]
                    stale = lpid and lpid != (p.shopify_product_id or "")
                    if stale:
                        verdict = "stale_link"; n_stale += 1
                    elif lp != exp:
                        verdict = "not_converted"; n_notconv += 1
                    else:
                        verdict = "ok"; n_ok += 1
                w.writerow([p.sku, p.pack_qty, exp, lp,
                            p.shopify_product_id or "", lpid, verdict])
        print(f"\nreport: {out}")
        print(f"  ok (pack price live):        {n_ok}")
        print(f"  stale_link (wrong product):  {n_stale}")
        print(f"  not_converted (still/other): {n_notconv}")
        print(f"  missing_on_store:            {n_missing}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
