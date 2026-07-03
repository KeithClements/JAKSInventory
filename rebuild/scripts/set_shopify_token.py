"""
scripts/set_shopify_token.py
============================
Securely move a Shopify Admin API token from the environment into the ERP's
settings, then verify it with one live API call. The token is read from
$env:SHOPTOKEN (never a command-line literal) and is NEVER printed — only its
length/suffix and the verification result are shown.

Usage (PowerShell):
    $env:SHOPTOKEN = Get-Clipboard
    .venv\\Scripts\\python.exe scripts\\set_shopify_token.py
"""
from __future__ import annotations

import os
import sqlite3
import sys


def main() -> int:
    tok = (os.environ.get("SHOPTOKEN") or "").strip()
    if not tok:
        print("ERROR: SHOPTOKEN env var is empty (clipboard copy failed?)")
        return 1
    if not tok.startswith("shpat_"):
        print(f"ERROR: value doesn't look like an Admin API token "
              f"(got prefix {tok[:6]!r}, len {len(tok)}) — expected shpat_…")
        return 1

    conn = sqlite3.connect("data/jaks.db")
    conn.execute("UPDATE settings SET value=? WHERE key='shopify_access_token'", (tok,))
    conn.execute("UPDATE settings SET value='jaks-diesel-3.myshopify.com' "
                 "WHERE key='shopify_store_url'")
    conn.commit()
    conn.close()
    print(f"saved token  …{tok[-4:]}  (len {len(tok)})")

    # Live verification through the ERP's own service — one page only.
    from app.database import SessionLocal
    from app.services.shopify_service import ShopifyService
    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)
        print("configured:", svc.is_configured(), "| host:", svc._store_domain())
        data = svc._graphql(svc._VARIANTS_QUERY, {"cursor": None})
        if data.get("errors"):
            print("SHOPIFY REJECTED:", str(data["errors"])[:300])
            return 2
        block = (data.get("data") or {}).get("productVariants") or {}
        nodes = block.get("nodes") or []
        print(f"OK — token works. First page returned {len(nodes)} variants.")
        if nodes:
            print("sample store SKU:", nodes[0].get("sku"))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
