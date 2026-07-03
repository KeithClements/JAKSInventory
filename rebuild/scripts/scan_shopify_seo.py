"""
scripts/scan_shopify_seo.py
===========================
READ-ONLY scan of the LIVE Shopify store (via the ERP's configured token) to find
products whose native SEO meta-description is empty. Writes the gap list to
scripts/shopify_seo_gap.json so a follow-up writer can fill them. Touches nothing.

Run:  .venv\\Scripts\\python.exe -m scripts.scan_shopify_seo
"""
from __future__ import annotations

import json
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

OUT = pathlib.Path(__file__).resolve().parent / "shopify_seo_gap.json"

_SCAN_QUERY = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      handle
      title
      status
      vendor
      productType
      tags
      descriptionPlainText: description(truncateAt: 600)
      seo { title description }
    }
  }
}
"""


def main() -> int:
    from app.database import SessionLocal
    from app.services.shopify_service import ShopifyService

    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)
        if not svc.is_configured():
            print("ABORT: Shopify not configured (token/store url missing).")
            return 1
        print(f"host: {svc._store_domain()}")

        scanned = 0
        by_status: dict[str, int] = {}
        missing: list[dict] = []
        cursor = None
        page = 0
        while True:
            data = svc._graphql(_SCAN_QUERY, {"cursor": cursor})
            if data.get("errors") or "data" not in data:
                print("SHOPIFY ERROR:", str(data.get("errors") or data)[:300])
                return 2
            block = (data["data"] or {}).get("products") or {}
            for n in block.get("nodes") or []:
                scanned += 1
                st = n.get("status") or "?"
                by_status[st] = by_status.get(st, 0) + 1
                seo = n.get("seo") or {}
                if not (seo.get("description") or "").strip():
                    missing.append({
                        "id": n.get("id"),
                        "handle": n.get("handle"),
                        "title": n.get("title") or "",
                        "status": st,
                        "vendor": n.get("vendor") or "",
                        "product_type": n.get("productType") or "",
                        "tags": n.get("tags") or [],
                        "description": (n.get("descriptionPlainText") or "").strip(),
                        "seo_title": (seo.get("title") or "").strip(),
                    })
            page += 1
            pi = block.get("pageInfo") or {}
            if page % 4 == 0:
                print(f"  scanned {scanned}  (missing so far {len(missing)})", flush=True)
            if not pi.get("hasNextPage"):
                break
            cursor = pi.get("endCursor")

        OUT.write_text(json.dumps(missing, indent=2), encoding="utf-8")
        print("\n=== Live Shopify SEO scan ===")
        print(f"  products scanned     : {scanned}")
        print(f"  by status            : {by_status}")
        print(f"  MISSING seo.description: {len(missing)}")
        # status breakdown of the gap
        gap_status: dict[str, int] = {}
        for m in missing:
            gap_status[m["status"]] = gap_status.get(m["status"], 0) + 1
        print(f"  gap by status        : {gap_status}")
        print(f"  gap list written to  : {OUT}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
