"""
scripts/audit_storefront_gaps.py
================================
READ-ONLY audit of the LIVE production storefront (www.jaksdiesel.com =
uyuedd-gc.myshopify.com) for the SEO/CRO metafield gaps found in the 2026-07-02
market review. Touches nothing — reports coverage and writes a per-product gap
worklist CSV.

Measures, across every ACTIVE product:
  • custom.warranty                    (theme warranty tab — currently ~0%)
  • custom.cross_reference_part_numbers (theme xref grid + Product schema mpn)
  • jaks.cross_references              (shadow copy in the 'jaks' namespace)
  • custom.engine_make / brand / condition (should be ~100%)
  • judgeme review count               (trust)

IMPORTANT: targets uyuedd-gc directly with a PRODUCTION Admin API token you set
in the env — NOT the ERP's configured store (the ERP currently points at
jaks-diesel-3.myshopify.com, a different store; see the market-review notes).

Set the token first (Admin API access token for www.jaksdiesel.com):
    set SHOPIFY_ACCESS_TOKEN=shpat_xxx           (cmd)
    $env:SHOPIFY_ACCESS_TOKEN="shpat_xxx"        (PowerShell)

Run:  python scripts\\audit_storefront_gaps.py
      python scripts\\audit_storefront_gaps.py --limit 500      # quick sample
Outputs: scripts/storefront_gap_report.csv  +  scripts/storefront_gap_summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys
import time
import urllib.request

SHOP = os.environ.get("SHOPIFY_SHOP", "uyuedd-gc.myshopify.com")
API_VERSION = "2024-10"
TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")

HERE = pathlib.Path(__file__).resolve().parent
CSV_OUT = HERE / "storefront_gap_report.csv"
JSON_OUT = HERE / "storefront_gap_summary.json"

_QUERY = """
query($first:Int!, $after:String){
  products(first:$first, after:$after, query:"status:active"){
    edges{ node{
      id handle title vendor productType templateSuffix tags
      warranty: metafield(namespace:"custom", key:"warranty"){ value }
      xc: metafield(namespace:"custom", key:"cross_reference_part_numbers"){ value }
      xj: metafield(namespace:"jaks", key:"cross_references"){ value }
      make: metafield(namespace:"custom", key:"engine_make"){ value }
      brand: metafield(namespace:"custom", key:"brand"){ value }
      cond: metafield(namespace:"custom", key:"condition"){ value }
      jm: metafield(namespace:"judgeme", key:"review_widget_data"){ value }
    }}
    pageInfo{ hasNextPage endCursor }
  }
}
"""


def gql(query, variables=None):
    url = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": TOKEN,
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def _val(node, key):
    m = node.get(key)
    return (m or {}).get("value") if m else None


def _has(v):
    return bool(v and str(v).strip())


def _review_count(node):
    raw = _val(node, "jm")
    if not raw:
        return 0
    try:
        return int(json.loads(raw).get("number_of_reviews") or 0)
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="stop after N products (sampling)")
    args = ap.parse_args()

    if not TOKEN:
        print("ABORT: set SHOPIFY_ACCESS_TOKEN (production Admin API token for www.jaksdiesel.com).")
        return 1
    print(f"store: {SHOP}   (read-only audit)")

    tot = 0
    cov = {"warranty": 0, "xref_custom": 0, "xref_jaks": 0, "xref_either": 0,
           "engine_make": 0, "brand": 0, "condition": 0, "has_reviews": 0}
    tmpl = {}
    rows = []
    cursor = None
    while True:
        batch = min(250, (args.limit - tot) if args.limit else 250)
        if batch <= 0:
            break
        data = gql(_QUERY, {"first": batch, "after": cursor})
        if data.get("errors"):
            print("SHOPIFY ERROR:", str(data["errors"])[:300])
            return 2
        block = data["data"]["products"]
        for e in block["edges"]:
            n = e["node"]
            tot += 1
            t = n.get("templateSuffix") or "(default)"
            tmpl[t] = tmpl.get(t, 0) + 1
            w = _has(_val(n, "warranty"))
            xc = _has(_val(n, "xc"))
            xj = _has(_val(n, "xj"))
            mk = _has(_val(n, "make"))
            br = _has(_val(n, "brand"))
            cd = _has(_val(n, "cond"))
            rc = _review_count(n)
            cov["warranty"] += w
            cov["xref_custom"] += xc
            cov["xref_jaks"] += xj
            cov["xref_either"] += (xc or xj)
            cov["engine_make"] += mk
            cov["brand"] += br
            cov["condition"] += cd
            cov["has_reviews"] += (rc > 0)
            # worklist row: only flag products with a real gap worth fixing
            gaps = []
            if not w:
                gaps.append("no_warranty")
            if not xc and xj:
                gaps.append("xref_only_in_jaks")   # cheap fix: copy jaks->custom
            if not xc and not xj:
                gaps.append("xref_missing")         # needs ERP/source data
            if gaps:
                rows.append({
                    "handle": n.get("handle", ""),
                    "title": (n.get("title") or "")[:90],
                    "vendor": n.get("vendor", ""),
                    "template": t,
                    "gaps": "|".join(gaps),
                    "jaks_xref": (_val(n, "xj") or "")[:120],
                })
        pi = block["pageInfo"]
        if tot % 1000 == 0:
            print(f"  scanned {tot} …", flush=True)
        if not pi["hasNextPage"] or (args.limit and tot >= args.limit):
            break
        cursor = pi["endCursor"]
        time.sleep(0.3)

    def pct(x):
        return f"{x}/{tot} ({100*x//tot if tot else 0}%)"

    summary = {"store": SHOP, "active_scanned": tot,
               "coverage": {k: {"n": v, "pct": (100*v//tot if tot else 0)} for k, v in cov.items()},
               "templates": tmpl, "gap_rows": len(rows)}
    JSON_OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with CSV_OUT.open("w", newline="", encoding="utf-8") as fh:
        wtr = csv.DictWriter(fh, fieldnames=["handle", "title", "vendor", "template", "gaps", "jaks_xref"])
        wtr.writeheader()
        wtr.writerows(rows)

    print(f"\n=== STOREFRONT GAP AUDIT — {tot} active products ===")
    print(f"  custom.warranty                     {pct(cov['warranty'])}")
    print(f"  custom.cross_reference_part_numbers {pct(cov['xref_custom'])}   <- theme reads this")
    print(f"  jaks.cross_references (shadow)       {pct(cov['xref_jaks'])}")
    print(f"  xref in EITHER namespace            {pct(cov['xref_either'])}")
    print(f"  custom.engine_make                  {pct(cov['engine_make'])}")
    print(f"  custom.brand                        {pct(cov['brand'])}")
    print(f"  products with >=1 review            {pct(cov['has_reviews'])}")
    print(f"\n  worklist rows written: {len(rows)}  -> {CSV_OUT.name}")
    print(f"  summary               -> {JSON_OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
