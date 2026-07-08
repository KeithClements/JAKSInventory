"""Re-host CTT turbo product images onto Shopify's CDN.

Central Turbos serves its /PartImages photos with Content-Type
``application/octet-stream``; Shopify's media fetch (productSet ``files``
originalSource) only accepts a real image content-type, so those photos land as
media status FAILED and the listing shows no picture. This script downloads each
Central image and re-uploads it to Shopify Files as ``image/jpeg`` (the proven
placeholder pattern), then rewrites the ProductImage to the resulting
``cdn.shopify.com`` URL — which Shopify re-hosts cleanly on the next publish.

Idempotent: images already on cdn.shopify.com are skipped. Fail-soft per image.
Dry-run by default; pass --apply to write. Optionally re-publishes linked
products so the fixed image attaches immediately.

    python -m scripts.rehost_turbo_images            # dry-run, all CTT turbos
    python -m scripts.rehost_turbo_images --apply --limit 5 --republish
    python -m scripts.rehost_turbo_images --apply --ids 5940,5941 --republish
"""
from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_SHOPIFY_HOST = "cdn.shopify.com"


def _opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def rehost_one(httpx, gql, headers, src_url: str, alt: str) -> str | None:
    """Central image URL -> Shopify Files cdn URL, or None on any failure."""
    try:
        img = httpx.get(src_url, timeout=30.0, follow_redirects=True)
        if img.status_code != 200 or len(img.content) < 1024:
            return None
        data = img.content
        fname = src_url.rsplit("/", 1)[-1].split("?")[0] or "image.jpg"
        if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
            fname += ".jpg"

        staged_m = (
            "mutation($input:[StagedUploadInput!]!){ stagedUploadsCreate(input:$input){"
            " stagedTargets{ url resourceUrl parameters{name value} }"
            " userErrors{message} } }")
        r = httpx.post(gql, headers=headers, timeout=30.0, json={
            "query": staged_m, "variables": {"input": [{
                "filename": fname, "mimeType": "image/jpeg", "resource": "IMAGE",
                "httpMethod": "POST", "fileSize": str(len(data))}]}})
        su = r.json().get("data", {}).get("stagedUploadsCreate") or {}
        if su.get("userErrors") or not su.get("stagedTargets"):
            return None
        target = su["stagedTargets"][0]
        form = {p["name"]: p["value"] for p in target["parameters"]}
        up = httpx.post(target["url"], data=form,
                        files={"file": (fname, data, "image/jpeg")}, timeout=60.0)
        if up.status_code not in (200, 201, 204):
            return None

        fc_m = (
            "mutation($files:[FileCreateInput!]!){ fileCreate(files:$files){"
            " files{ id fileStatus ... on MediaImage{ image{url} } }"
            " userErrors{message} } }")
        r = httpx.post(gql, headers=headers, timeout=30.0, json={
            "query": fc_m, "variables": {"files": [{
                "originalSource": target["resourceUrl"], "contentType": "IMAGE",
                "alt": alt[:512]}]}})
        fc = r.json().get("data", {}).get("fileCreate") or {}
        if fc.get("userErrors") or not fc.get("files"):
            return None
        fid = fc["files"][0]["id"]

        poll_q = ("query($id:ID!){ node(id:$id){ ... on MediaImage{"
                  " fileStatus image{url} } } }")
        for _ in range(20):
            time.sleep(1.5)
            r = httpx.post(gql, headers=headers, timeout=30.0,
                           json={"query": poll_q, "variables": {"id": fid}})
            node = (r.json().get("data") or {}).get("node") or {}
            if node.get("fileStatus") == "READY" and node.get("image"):
                return node["image"]["url"]
        return None
    except Exception:  # noqa: BLE001 — fail-soft; a bad image never blocks the run
        return None


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    republish = "--republish" in argv
    limit = int(_opt(argv, "--limit") or 0) or None
    ids_raw = _opt(argv, "--ids")
    only_ids = {int(x) for x in ids_raw.split(",")} if ids_raw else None

    import httpx
    from app.database import SessionLocal
    from app.models.product import Product, ProductImage, ProductVendorSource
    from app.models.vendor import Vendor
    from app.services.shopify_service import ShopifyService, _API_VERSION

    db = SessionLocal()
    try:
        svc = ShopifyService(db, None)
        if not svc.is_configured():
            print("Shopify not configured."); return 2
        gql = f"https://{svc._store_domain()}/admin/api/{_API_VERSION}/graphql.json"
        headers = {"X-Shopify-Access-Token": svc._token(),
                   "Content-Type": "application/json"}

        ctt = db.query(Vendor).filter(Vendor.vendor_code == "CTT").first()
        if ctt is None:
            print("No CTT vendor."); return 2
        pids = [s.product_id for s in db.query(ProductVendorSource)
                .filter(ProductVendorSource.vendor_id == ctt.id).all()]
        q = (db.query(Product).filter(Product.id.in_(pids))
             .filter(Product.sku.like("JAKS-CTT-%")).order_by(Product.id))
        prods = [p for p in q.all() if only_ids is None or p.id in only_ids]

        done = fixed = skipped = failed = 0
        for p in prods:
            if limit and done >= limit:
                break
            img = (db.query(ProductImage)
                   .filter(ProductImage.product_id == p.id,
                           ProductImage.is_primary == True)  # noqa: E712
                   .first())
            if img is None or not img.file_path:
                continue
            if _SHOPIFY_HOST in img.file_path:
                skipped += 1
                continue
            done += 1
            if not apply:
                print(f"  WOULD rehost {p.sku:<20} {img.file_path[:70]}")
                continue
            cdn = rehost_one(httpx, gql, headers, img.file_path, p.title)
            if not cdn:
                failed += 1
                print(f"  FAIL  {p.sku:<20} {img.file_path[:60]}")
                continue
            img.file_path = cdn
            img.source = "shopify"
            db.commit()
            fixed += 1
            note = ""
            if republish and (p.shopify_product_id or "").startswith("gid://"):
                res = svc.publish_product(p, status=p.shopify_status or "DRAFT")
                note = "republished" if res.get("ok") else f"republish-err:{res.get('error')}"
            print(f"  OK    {p.sku:<20} -> shopify CDN  {note}")

        print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: rehosted={fixed} "
              f"skipped(already shopify)={skipped} failed={failed} "
              f"considered={done}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
