"""
Meta description generator for jaksdiesel.com products.
Usage:
  python generate_meta_descriptions.py --dry-run        # preview first 20
  python generate_meta_descriptions.py --apply          # write to Shopify (batches of 50)
  python generate_meta_descriptions.py --apply --limit 100  # apply to first N products
"""
import json
import re
import sys
import os
import time
import argparse
import urllib.request
import urllib.error

SHOP = "uyuedd-gc.myshopify.com"
API_VERSION = "2024-10"
TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")

SUFFIX_PAI     = "2-yr warranty. Same-day shipping. JAK's Diesel — (720) 445-6249."
SUFFIX_3YR     = "3-yr warranty. Same-day shipping. JAK's Diesel — (720) 445-6249."
SUFFIX_JAKS    = "Brand-new, same-day shipping. JAK's Diesel — (720) 445-6249."
MAX_CHARS      = 160


# ── helpers ──────────────────────────────────────────────────────────────────

def first_sentence(text):
    """Return the first complete sentence (up to first period followed by space or end)."""
    text = text.strip()
    # strip trailing ellipsis from API truncation
    text = re.sub(r'\s*\.\.\.$', '', text)
    m = re.search(r'^(.+?\.)\s', text)
    if m:
        return m.group(1).strip()
    # fallback: up to first period
    if '.' in text:
        return text.split('.')[0].strip() + '.'
    return text[:120].strip()


def trim(s, max_chars=MAX_CHARS):
    if len(s) <= max_chars:
        return s
    # trim at last space before limit
    cut = s[:max_chars - 1].rsplit(' ', 1)[0]
    return cut.rstrip('.,;') + '…'


def has_tag(tags, *keywords):
    lower = [t.lower() for t in tags]
    return any(k.lower() in lower for k in keywords)


def warranty_suffix(tags, default_suffix):
    if has_tag(tags, "3-Year Warranty"):
        return SUFFIX_3YR
    return default_suffix


def clean_pai_title(title):
    """Return the human-readable part before any '| PAI' suffix block."""
    # Split on first ' | PAI' occurrence
    base = title.split(' | PAI')[0].strip()
    # Remove any stray trailing '| JAK's Diesel'
    base = re.sub(r"\s*\|\s*JAK'?s Diesel\s*$", '', base, flags=re.IGNORECASE).strip()
    return base


def generate(product):
    title       = product.get("title", "")
    description = product.get("description", "")
    tags        = product.get("tags", [])
    vendor      = product.get("vendor", "")

    # ── PAI Industries products ───────────────────────────────────────────────
    if vendor == "PAI Industries":
        sentence = first_sentence(description) if description else ""
        suffix   = warranty_suffix(tags, SUFFIX_PAI)

        if sentence:
            meta = f"{sentence} {suffix}"
        else:
            # Title-derived fallback for small parts (no body description)
            base = clean_pai_title(title)
            meta = f"{base}. {suffix}"

        return trim(meta)

    # ── JAK's Diesel branded products ────────────────────────────────────────
    # Remove leading OEM part numbers
    clean = re.sub(r'^[\dA-Z]{5,}\s+', '', title).strip()
    clean = re.sub(r"\s+from JAK'?s Diesel.*$", '', clean, flags=re.IGNORECASE).strip()

    suffix = warranty_suffix(tags, SUFFIX_JAKS)
    meta = f"{clean} — {suffix}"

    return trim(meta)


# ── Shopify GraphQL ───────────────────────────────────────────────────────────

PRODUCTS_QUERY = """
query FetchProducts($first: Int!, $after: String) {
  products(first: $first, after: $after, query: "status:active") {
    edges {
      node {
        id
        title
        vendor
        productType
        tags
        description
        seo { description }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

UPDATE_SEO_MUTATION = """
mutation UpdateSeo($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id seo { description } }
    userErrors { field message }
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
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_all_products(limit=None):
    products = []
    cursor = None
    while True:
        batch = min(50, (limit - len(products)) if limit else 50)
        data = gql(PRODUCTS_QUERY, {"first": batch, "after": cursor})
        edges = data["data"]["products"]["edges"]
        page_info = data["data"]["products"]["pageInfo"]
        products.extend(e["node"] for e in edges)
        print(f"  fetched {len(products)} products…", flush=True)
        if not page_info["hasNextPage"] or (limit and len(products) >= limit):
            break
        cursor = page_info["endCursor"]
        time.sleep(0.5)  # gentle rate-limit buffer
    return products[:limit] if limit else products


def apply_updates(products, dry_run=True):
    skipped = updated = errors = 0
    for i, p in enumerate(products):
        existing = (p.get("seo") or {}).get("description") or ""
        if existing.strip():
            skipped += 1
            continue

        meta = generate(p)
        if dry_run:
            print(f"  [{i+1}] {p['title'][:60]}")
            print(f"        → {meta}")
            print(f"        ({len(meta)} chars)")
        else:
            result = gql(UPDATE_SEO_MUTATION, {
                "input": {"id": p["id"], "seo": {"description": meta}}
            })
            ue = result.get("data", {}).get("productUpdate", {}).get("userErrors", [])
            if ue:
                print(f"  ERROR {p['id']}: {ue}")
                errors += 1
            else:
                updated += 1
            if (i + 1) % 50 == 0:
                time.sleep(1)  # respect rate limits

    return skipped, updated, errors


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply",   action="store_true")
    parser.add_argument("--limit",   type=int, default=None)
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Specify --dry-run or --apply")
        sys.exit(1)

    if args.apply and not TOKEN:
        print("Set SHOPIFY_ACCESS_TOKEN env var first")
        sys.exit(1)

    dry = not args.apply
    limit = args.limit if args.limit else (20 if dry else None)

    print(f"{'DRY RUN' if dry else 'APPLYING'} — fetching {'first ' + str(limit) if limit else 'all'} products…")
    products = fetch_all_products(limit=limit)
    print(f"Got {len(products)} products. Generating…\n")

    skipped, updated, errors = apply_updates(products, dry_run=dry)

    print(f"\n{'---'*20}")
    if dry:
        print(f"Dry run complete. {len(products) - skipped} would be written, {skipped} already have a meta description.")
    else:
        print(f"Done. Updated: {updated}, Skipped (already had meta): {skipped}, Errors: {errors}")


if __name__ == "__main__":
    main()
