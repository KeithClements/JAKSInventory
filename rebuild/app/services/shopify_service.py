"""
app/services/shopify_service.py
===============================
ERP → Shopify Admin API publication (bulk listing of the catalog to the JAK's
Diesel storefront).

Design mirrors the QBO push: this NEVER touches the money / inventory path. It
maps an ERP ``Product`` onto Shopify's ``productSet`` mutation input — Shopify's
own recommended path for "an external system (ERP/PIM) mirroring product data"
(idempotent: re-publishing the same product updates it in place via the stored
``shopify_product_id``).

Safety model:
  • Products are created **DRAFT** by default — they do not go live on the
    storefront until someone publishes them in Shopify (or we pass status=ACTIVE).
  • The live push is **fail-soft** and **gated** (PUBLISH_SHOPIFY permission +
    configured store URL + access token). With no token, ``publish_*`` returns a
    structured "not configured" result and changes nothing.
  • ``build_listing`` / ``to_product_set_input`` / ``preview`` are **pure** — no
    network — so the mapping is fully unit-testable and a dry-run can show exactly
    what would be sent before anything goes to the live store.

Images self-host automatically: we hand Shopify the source image URL (the PAI
CDN link) as a media ``originalSource``; Shopify downloads it and re-hosts it on
its own CDN, so the storefront never hotlinks PAI.
"""
from __future__ import annotations

import json

from app.constants import Permission
from app.models.product import Product
from app.services.base import BaseService
from app.settings_utils import get_setting_value_db

_API_VERSION = "2025-01"          # pin the Admin API version
_METAFIELD_NS = "custom"
_VENDOR_FALLBACK = "JAK's Diesel"


class ShopifyService(BaseService):
    """Publish ERP products to the JAK's Diesel Shopify store via ``productSet``."""

    # ── configuration (Settings) ──────────────────────────────────────────────
    def _store_domain(self) -> str:
        raw = get_setting_value_db(self.db, "shopify_store_url", "").strip()
        return raw.replace("https://", "").replace("http://", "").strip().strip("/")

    def _token(self) -> str:
        return get_setting_value_db(self.db, "shopify_access_token", "").strip()

    def is_configured(self) -> bool:
        return bool(self._store_domain() and self._token())

    # ── mapping: ERP Product → normalized listing (PURE, testable) ────────────
    def build_listing(self, product: Product) -> dict:
        """Flatten an ERP product into a normalized, vendor-neutral listing dict.
        The raw PAI part number is exposed only as an internal metafield — never
        as the vendor or in the customer-facing title/SKU (the SKU is already the
        assembled JAKS scheme value)."""
        src = product.preferred_vendor_source
        price = round(float(product.selling_price or 0), 2)
        cost = round(float(src.vendor_cost), 2) if src and src.vendor_cost else 0.0

        # Tags power storefront search/filtering: engine make + every fitted model
        # + the part category. (PAI is deliberately NOT a tag — no vendor leak.)
        tags: list[str] = []
        if product.engine_manufacturer:
            tags.append(product.engine_manufacturer)
        if product.engine_model:
            tags.append(product.engine_model)
        for a in product.applications:
            if a.engine_model:
                tags.append(a.engine_model)
        if product.category and product.category.name:
            tags.append(product.category.name)
        _seen: set[str] = set()
        tags = [t for t in tags if t and not (t.lower() in _seen or _seen.add(t.lower()))]

        oem_refs = [
            f"{(c.brand or '').strip()} {c.ref_number}".strip()
            for c in product.cross_references if c.ref_number
        ]
        apps = [
            f"{a.engine_make} {a.engine_model}".strip()
            for a in product.applications if (a.engine_make or a.engine_model)
        ]
        return {
            "sku": product.sku,
            "title": product.title,
            "description_html": self._description_html(product, apps, oem_refs),
            # vendor = engine make (a useful storefront facet) or the store brand —
            # never the supplier name (PAI stays hidden).
            "vendor": (product.engine_manufacturer or _VENDOR_FALLBACK),
            "product_type": (product.category.name if product.category else "") or "Engine Parts",
            "status": "DRAFT",
            "tags": tags,
            "price": price,
            "cost": cost,
            "barcode": product.barcode or "",
            "weight_lbs": float(product.weight_lbs or 0),
            "images": [img.url for img in product.images if img.url],
            "metafields": {
                "pai_part_no": (src.vendor_part_number if src else ""),
                "oem_references": oem_refs,
                "engine_applications": apps,
                "warranty_months": int(product.supplier_warranty_months or 0),
            },
            "shopify_product_id": product.shopify_product_id or None,
        }

    @staticmethod
    def _description_html(product: Product, apps: list[str], oem_refs: list[str]) -> str:
        parts = [f"<p>{product.title}</p>"]
        if apps:
            parts.append("<p><strong>Fits:</strong></p><ul>"
                         + "".join(f"<li>{a}</li>" for a in apps[:25]) + "</ul>")
        if oem_refs:
            parts.append("<p><strong>OEM cross-references:</strong></p><ul>"
                         + "".join(f"<li>{r}</li>" for r in oem_refs[:40]) + "</ul>")
        if product.supplier_warranty_months:
            parts.append(f"<p><strong>Warranty:</strong> {product.supplier_warranty_months} months</p>")
        return "".join(parts)

    # ── shaping: normalized listing → Shopify productSet input (PURE) ─────────
    def to_product_set_input(self, listing: dict) -> dict:
        meta = listing["metafields"]
        metafields = []
        if meta.get("pai_part_no"):
            metafields.append({"namespace": _METAFIELD_NS, "key": "pai_part_no",
                               "type": "single_line_text_field", "value": str(meta["pai_part_no"])})
        if meta.get("oem_references"):
            metafields.append({"namespace": _METAFIELD_NS, "key": "oem_references",
                               "type": "list.single_line_text_field", "value": json.dumps(meta["oem_references"])})
        if meta.get("engine_applications"):
            metafields.append({"namespace": _METAFIELD_NS, "key": "engine_applications",
                               "type": "list.single_line_text_field", "value": json.dumps(meta["engine_applications"])})
        if meta.get("warranty_months"):
            metafields.append({"namespace": _METAFIELD_NS, "key": "warranty_months",
                               "type": "number_integer", "value": str(meta["warranty_months"])})

        variant = {
            "optionValues": [{"optionName": "Title", "name": "Default Title"}],
            "price": f'{listing["price"]:.2f}',
            # SKU + cost live on the variant's inventory item in productSet.
            "inventoryItem": {"sku": listing["sku"], "tracked": False,
                              "cost": f'{listing["cost"]:.2f}'},
        }
        if listing.get("barcode"):
            variant["barcode"] = listing["barcode"]

        inp = {
            "title": listing["title"],
            "descriptionHtml": listing["description_html"],
            "vendor": listing["vendor"],
            "productType": listing["product_type"],
            "status": listing["status"],
            "tags": listing["tags"],
            "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
            "variants": [variant],
            "metafields": metafields,
            # Shopify fetches each URL and re-hosts the image on its own CDN.
            "files": [{"originalSource": u, "contentType": "IMAGE", "alt": listing["title"]}
                      for u in listing["images"]],
        }
        if listing.get("shopify_product_id"):
            inp["id"] = listing["shopify_product_id"]   # update-in-place (idempotent)
        return inp

    # ── dry-run preview (no network) ──────────────────────────────────────────
    def preview(self, product_ids: list[int]) -> list[dict]:
        out = []
        for pid in product_ids:
            p = self.db.get(Product, pid)
            if p:
                out.append(self.to_product_set_input(self.build_listing(p)))
        return out

    # ── live publish (gated, fail-soft) ───────────────────────────────────────
    _MUTATION = (
        "mutation productSet($input: ProductSetInput!, $synchronous: Boolean!) {"
        "  productSet(input: $input, synchronous: $synchronous) {"
        "    product { id status handle }"
        "    userErrors { field message }"
        "  }"
        "}"
    )

    def publish_product(self, product: Product, *, status: str = "DRAFT") -> dict:
        """Create/update one product on Shopify. Idempotent via shopify_product_id.
        Fail-soft: returns {ok: False, error: ...} instead of raising when Shopify
        is unconfigured or the call fails — never blocks ERP work."""
        if self.current_user_id is not None:
            self.assert_can(Permission.PUBLISH_SHOPIFY)
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured — set shopify_store_url "
                    "and shopify_access_token in Settings."}
        import httpx
        listing = self.build_listing(product)
        listing["status"] = status
        inp = self.to_product_set_input(listing)
        url = f"https://{self._store_domain()}/admin/api/{_API_VERSION}/graphql.json"
        try:
            resp = httpx.post(
                url,
                json={"query": self._MUTATION, "variables": {"input": inp, "synchronous": True}},
                headers={"X-Shopify-Access-Token": self._token(),
                         "Content-Type": "application/json"},
                timeout=30.0,
            )
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 — fail-soft on any network/parse error
            return {"ok": False, "error": f"network/parse error: {exc}"}

        if data.get("errors"):
            return {"ok": False, "error": data["errors"]}
        result = (data.get("data") or {}).get("productSet") or {}
        if result.get("userErrors"):
            return {"ok": False, "error": result["userErrors"]}
        prod = result.get("product") or {}
        if prod.get("id"):
            product.shopify_product_id = prod["id"]
            product.shopify_status = prod.get("status", "") or ""
            self.db.commit()
            return {"ok": True, "product": prod}
        return {"ok": False, "error": "no product returned", "raw": data}

    def publish_batch(self, product_ids: list[int], *, status: str = "DRAFT") -> dict:
        """Publish many products one at a time (Shopify rate-limits bulk GraphQL).
        Returns a summary; partial-success safe."""
        summary = {"requested": len(product_ids), "published": 0, "failed": 0, "errors": []}
        for pid in product_ids:
            p = self.db.get(Product, pid)
            if not p:
                summary["failed"] += 1
                summary["errors"].append({"product_id": pid, "error": "not found"})
                continue
            res = self.publish_product(p, status=status)
            if res.get("ok"):
                summary["published"] += 1
            else:
                summary["failed"] += 1
                summary["errors"].append({"product_id": pid, "sku": p.sku, "error": res.get("error")})
        return summary
