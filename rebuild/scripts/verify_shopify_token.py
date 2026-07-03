"""Verify the saved Shopify token with one live Admin API call. Reads the token
from the ERP settings (never prints it). Run from the repo root."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main() -> int:
    from app.database import SessionLocal
    from app.services.shopify_service import ShopifyService
    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)
        print("configured:", svc.is_configured(), "| host:", svc._store_domain())
        if not svc.is_configured():
            print("Not configured — token or store URL missing.")
            return 1
        data = svc._graphql(svc._VARIANTS_QUERY, {"cursor": None})
        if data.get("errors"):
            print("SHOPIFY REJECTED:", str(data["errors"])[:300])
            return 2
        block = (data.get("data") or {}).get("productVariants") or {}
        nodes = block.get("nodes") or []
        print(f"OK - token works. First page returned {len(nodes)} variants.")
        if nodes:
            print("sample store SKU:", nodes[0].get("sku"))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
