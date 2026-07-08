"""
scripts/backfill_storefront_metafields.py
=========================================
Surgical, metafield-ONLY backfill for the LIVE production storefront
(www.jaksdiesel.com = uyuedd-gc.myshopify.com) to close the two gaps found in
the 2026-07-02 market review:

  --fix warranty   Populate custom.warranty (theme warranty tab, currently ~0%)
                   from the warranty a product ALREADY claims via its tags/vendor.
                   Conservative: only writes where a tier is known (3-Year tag,
                   2-Year tag, or PAI Industries). Never invents a warranty.

  --fix xref       Populate custom.cross_reference_part_numbers (theme xref grid +
                   Product JSON-LD mpn, currently ~35%) by copying the SHADOW
                   metafield jaks.cross_references (comma string) into the key the
                   theme actually reads (list.single_line_text_field). Only where
                   custom.cross_reference_part_numbers is empty AND jaks has data.

Writes ONLY those two metafields via metafieldsSet — title, body, price, images,
tags, status, and every other metafield are never touched. Idempotent (skips
products already populated). DRY-RUN by default.

Targets uyuedd-gc directly with a PRODUCTION Admin API token (write_products
scope) you set in the env — NOT the ERP's store (the ERP points at
jaks-diesel-3.myshopify.com, a different store; see market-review notes).

    $env:SHOPIFY_ACCESS_TOKEN="shpat_xxx"     (PowerShell)

Preview (no writes):   python scripts\\backfill_storefront_metafields.py --fix warranty,xref
Apply:                 python scripts\\backfill_storefront_metafields.py --fix warranty,xref --apply
Options: --fix a,b   --apply   --limit N   --preview N   --warranty-default "text"
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.request

SHOP = os.environ.get("SHOPIFY_SHOP", "uyuedd-gc.myshopify.com")
API_VERSION = "2024-10"
TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
PHONE = "(720) 445-6249"

HERE = pathlib.Path(__file__).resolve().parent
LOG = HERE / "backfill_storefront_metafields.log"

# ── Shopify transport ─────────────────────────────────────────────────────────

def gql(query, variables=None):
    url = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": TOKEN,
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def _log(msg):
    print(msg, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(msg.rstrip() + "\n")


# ── warranty tier (mirrors the approved logic in fill_shopify_seo.py) ─────────

def _tags_lower(tags):
    return [str(t).lower() for t in (tags or [])]


def warranty_text(tags, vendor, default_text):
    low = _tags_lower(tags)
    if "3-year warranty" in low or "3 year warranty" in low:
        return f"Backed by a 3-year JAK's Diesel warranty. Questions? Call {PHONE}."
    if vendor == "PAI Industries" or "2-year warranty" in low or "2 year warranty" in low:
        return f"Backed by a 2-year warranty. Questions? Call {PHONE}."
    return default_text  # None unless --warranty-default supplied


# ── metafield type discovery (so metafieldsSet never type-mismatches) ─────────

_DEF_QUERY = """
query {
  metafieldDefinitions(first: 50, ownerType: PRODUCT, namespace: "custom") {
    nodes { key type { name } }
  }
}
"""

def resolve_types():
    types = {}
    try:
        data = gql(_DEF_QUERY)
        for n in ((data.get("data") or {}).get("metafieldDefinitions") or {}).get("nodes") or []:
            types[n["key"]] = n["type"]["name"]
    except Exception as exc:  # noqa: BLE001
        _log(f"WARN: could not read metafield definitions ({exc}); using defaults.")
    # sensible defaults if no definition exists
    types.setdefault("warranty", "single_line_text_field")
    types.setdefault("cross_reference_part_numbers", "list.single_line_text_field")
    return types


# ── product fetch ─────────────────────────────────────────────────────────────

_FETCH = """
query($first:Int!, $after:String){
  products(first:$first, after:$after, query:"status:active"){
    edges{ node{
      id title vendor tags
      war: metafield(namespace:"custom", key:"warranty"){ value }
      xc:  metafield(namespace:"custom", key:"cross_reference_part_numbers"){ value }
      xj:  metafield(namespace:"jaks",  key:"cross_references"){ value }
    }}
    pageInfo{ hasNextPage endCursor }
  }
}
"""

_SET = """
mutation($mf:[MetafieldsSetInput!]!){
  metafieldsSet(metafields:$mf){
    metafields{ id key }
    userErrors{ field message code }
  }
}
"""


def _mval(node, key):
    m = node.get(key)
    return ((m or {}).get("value") or "").strip() if m else ""


def _xref_list(comma_str):
    parts = [p.strip() for p in comma_str.replace("\n", ",").split(",")]
    seen, out = set(), []
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out[:128]  # Shopify list metafield ceiling


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", default="warranty,xref", help="comma list: warranty,xref")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--preview", type=int, default=12)
    ap.add_argument("--warranty-default", default=None,
                    help="text to use when no warranty tier is detectable (default: skip)")
    args = ap.parse_args()

    if not TOKEN:
        print("ABORT: set SHOPIFY_ACCESS_TOKEN (production Admin API token, write_products).")
        return 1
    fixes = {f.strip() for f in args.fix.split(",") if f.strip()}
    if not fixes <= {"warranty", "xref"}:
        print(f"ABORT: --fix must be warranty and/or xref (got {fixes}).")
        return 1

    types = resolve_types()
    _log(f"store: {SHOP}  fixes={sorted(fixes)}  "
         f"mode={'APPLY' if args.apply else f'PREVIEW(first {args.preview})'}")
    _log(f"types: warranty={types['warranty']}  "
         f"xref={types['cross_reference_part_numbers']}")

    planned = []          # (product_id, [metafield inputs], human note)
    scanned = 0
    n_war = n_xref = 0
    cursor = None
    while True:
        batch = min(200, (args.limit - scanned) if args.limit else 200)
        if batch <= 0:
            break
        data = gql(_FETCH, {"first": batch, "after": cursor})
        if data.get("errors"):
            _log("SHOPIFY ERROR: " + str(data["errors"])[:300])
            return 2
        block = data["data"]["products"]
        for e in block["edges"]:
            n = e["node"]
            scanned += 1
            mfs, notes = [], []
            if "warranty" in fixes and not _mval(n, "war"):
                wt = warranty_text(n.get("tags"), n.get("vendor"), args.warranty_default)
                if wt:
                    mfs.append({"ownerId": n["id"], "namespace": "custom", "key": "warranty",
                                "type": types["warranty"], "value": wt})
                    notes.append(f"WAR='{wt[:40]}…'")
                    n_war += 1
            if "xref" in fixes and not _mval(n, "xc"):
                shadow = _mval(n, "xj")
                if shadow:
                    lst = _xref_list(shadow)
                    if lst:
                        mfs.append({"ownerId": n["id"], "namespace": "custom",
                                    "key": "cross_reference_part_numbers",
                                    "type": types["cross_reference_part_numbers"],
                                    "value": json.dumps(lst)})
                        notes.append(f"XREF x{len(lst)}")
                        n_xref += 1
            if mfs:
                planned.append((n["id"], mfs, f"{n['title'][:48]} | " + " ".join(notes)))
        pi = block["pageInfo"]
        if scanned % 1000 == 0:
            _log(f"  scanned {scanned}  (planned {len(planned)})")
        if not pi["hasNextPage"] or (args.limit and scanned >= args.limit):
            break
        cursor = pi["endCursor"]
        time.sleep(0.25)

    _log(f"\nscanned={scanned}  products_to_change={len(planned)}  "
         f"(warranty writes={n_war}, xref writes={n_xref})")

    if not args.apply:
        for _pid, _mfs, note in planned[:args.preview]:
            _log(f"  WOULD SET  {note}")
        _log(f"\n=== PREVIEW only. Re-run with --apply to write {len(planned)} products. ===")
        return 0

    # APPLY — metafieldsSet accepts up to 25 metafields per call.
    flat = [mf for _pid, mfs, _n in planned for mf in mfs]
    ok = fail = 0
    for i in range(0, len(flat), 25):
        chunk = flat[i:i + 25]
        d = gql(_SET, {"mf": chunk})
        ue = (((d.get("data") or {}).get("metafieldsSet") or {}).get("userErrors")
              or d.get("errors"))
        if ue:
            fail += len(chunk)
            _log(f"  SET FAIL @{i}: {str(ue)[:200]}")
        else:
            ok += len(chunk)
        if (i // 25) % 10 == 0:
            _log(f"  ...{ok} metafields set (fail {fail})")
        time.sleep(0.2)
    _log(f"\n=== DONE === metafields written={ok}  failed={fail}  (log: {LOG.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
