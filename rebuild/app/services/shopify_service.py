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
import re

from app.constants import BRANDS, Permission, ProductStatus, VendorAvailability
from app.models.product import Product
from app.services.base import BaseService
from app.settings_utils import get_setting_value_db

_API_VERSION = "2025-01"          # pin the Admin API version
_METAFIELD_NS = "custom"
_VENDOR_FALLBACK = "JAK's Diesel"

# Shopify per-product limits (enforced before publish to avoid userError fail-soft).
_MAX_TAGS = 250
_MAX_METAFIELD_LIST = 128       # list.* metafields cap at 128 entries
_SEO_TITLE_MAX = 255
_SEO_DESC_MAX = 320
_PRODUCT_TITLE_MAX = 255

# Customer-facing parts brands (seeded constants.BRANDS) MINUS the supplier
# identities we never print on the storefront. A brand is shown only if it is a
# recognized real brand and not a hidden supplier — so PAI / a vendor's legal
# name (HHP Parts, ATL Diesel Supply) never leak into a listing title.
_HIDDEN_SUPPLIER_BRANDS = {"pai", "pai industries"}
_CUSTOMER_FACING_BRANDS = {b.strip().lower() for b in BRANDS} - _HIDDEN_SUPPLIER_BRANDS


def _brand_is_customer_facing(brand: str) -> bool:
    return (brand or "").strip().lower() in _CUSTOMER_FACING_BRANDS


def _word_present(needle: str, haystack_low: str) -> bool:
    """True when `needle` appears as a WHOLE token in an already-lowercased
    string — not as a substring of a larger token. Mirrors the \\b discipline in
    classification_service ('cat' must not match inside 'locator'/'C70'). Treats
    the needle literally so engine codes (C7, N14, Series 60) match exactly."""
    n = (needle or "").strip().lower()
    if not n:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", haystack_low) is not None


class ShopifyService(BaseService):
    """Publish ERP products to the JAK's Diesel Shopify store via ``productSet``."""

    # ── configuration (Settings) ──────────────────────────────────────────────
    def _store_domain(self) -> str:
        """Normalize whatever the owner pasted into the API host.

        Accepts: a bare slug (jaks-diesel-3), the myshopify domain (with or
        without protocol/path), or a pasted ADMIN URL
        (https://admin.shopify.com/store/jaks-diesel-3/settings/…) — owners
        paste browser addresses; the Admin API only answers on
        {slug}.myshopify.com."""
        raw = get_setting_value_db(self.db, "shopify_store_url", "").strip().lower()
        raw = raw.replace("https://", "").replace("http://", "").strip().strip("/")
        if not raw:
            return ""
        # Pasted admin URL → extract the store slug
        if raw.startswith("admin.shopify.com/store/"):
            slug = raw.split("admin.shopify.com/store/", 1)[1].split("/", 1)[0].strip()
            return f"{slug}.myshopify.com" if slug else ""
        # Reduce to the bare host: drop any path, query string, or fragment.
        raw = re.split(r"[/?#]", raw, 1)[0]
        # Bare slug (no dot) → assume myshopify
        if "." not in raw:
            return f"{raw}.myshopify.com"
        return raw

    def _token(self) -> str:
        # C7 — the Shopify Admin token is Fernet-encrypted at rest (settings save
        # encrypts it; see _ENCRYPTED_KEYS). _decrypt passes legacy plaintext
        # through unchanged, so an existing token keeps working until re-saved.
        from app.services.qbo_client import _decrypt as _secret_decrypt
        return _secret_decrypt(
            get_setting_value_db(self.db, "shopify_access_token", "").strip()
        )

    def is_configured(self) -> bool:
        return bool(self._store_domain() and self._token())

    # ── mapping: ERP Product → normalized listing (PURE, testable) ────────────
    def build_listing(self, product: Product) -> dict:
        """Flatten an ERP product into a normalized, vendor-neutral listing dict.
        The raw PAI part number is exposed only as an internal metafield — never
        as the vendor or in the customer-facing title/SKU (the SKU is already the
        assembled JAKS scheme value)."""
        src = product.preferred_vendor_source
        # Vendor sell packs (PAI "SELL PACK: 5 PIECE"): the ERP stores UNIT price/
        # cost/weight, but the vendor only sells in pack multiples — a storefront
        # order below the pack forces us to buy more than we sell (a guaranteed
        # loss, e.g. 2 × $3.26 sold vs a $12.10 5-pack bought). So the LISTING is
        # the pack: price/cost/weight × pack_qty, with the pack called out in the
        # title + description. One storefront unit == one vendor sell pack.
        pack = max(1, int(product.pack_qty or 1))
        price = round(round(float(product.selling_price or 0), 2) * pack, 2)
        cost = round((round(float(src.vendor_cost), 2) if src and src.vendor_cost
                      else 0.0) * pack, 2)
        # PAI *bulk part numbers* (SKU suffix == pack size: 900069HP-040 is the
        # 40-pack of 900069HP) price per PIECE but weigh the WHOLE PACK (base
        # gear 8.6 lb vs -040's 355 lb ≈ 40 × 8.875). Multiplying their weight
        # again would list a 14,200 lb parcel — keep it; every other pack part's
        # vendor weight is per-piece (verified: 331351 @ 0.07 lb/gasket).
        pack_for_weight = 1 if (pack > 1 and (product.sku or "")
                                .endswith(f"-{pack:03d}")) else pack

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

        # Owner-curated tags (SEO card "Search Keywords") join the derived tags.
        for kw in (product.search_keywords or "").split(","):
            kw = kw.strip()
            if kw:
                tags.append(kw)
        _seen2: set[str] = set()
        tags = [t for t in tags if not (t.lower() in _seen2 or _seen2.add(t.lower()))]
        tags = tags[:_MAX_TAGS]   # Shopify hard limit — derived facets kept first

        # Shopify list metafields cap at 128 entries — a part with a huge OEM /
        # application list would otherwise fail the whole productSet. Cap both.
        oem_refs = [
            f"{(c.brand or '').strip()} {c.ref_number}".strip()
            for c in product.cross_references if c.ref_number
        ][:_MAX_METAFIELD_LIST]
        apps = [
            f"{a.engine_make} {a.engine_model}".strip()
            for a in product.applications if (a.engine_make or a.engine_model)
        ][:_MAX_METAFIELD_LIST]
        # Storefront images: prefer CLEAN (unwatermarked, Shopify-CDN) photos. When
        # a product has one, drop the watermarked PAI-CDN images from the push (they
        # stay in the ERP as a fallback, just never shown on the storefront), and put
        # the primary first so it becomes Shopify's featured image.
        _imgs = [i for i in product.images if i.url]
        if any("cdn.shopify.com" in (i.url or "").lower() for i in _imgs):
            _imgs = [i for i in _imgs if "paiindustries.com" not in (i.url or "").lower()]
        _imgs.sort(key=lambda i: (not i.is_primary, i.id))
        listing_images = [i.url for i in _imgs]
        return {
            "sku": product.sku,
            "title": self._pack_title(self._store_title(product, product.title), pack),
            "description_html": self._description_html(product, apps, oem_refs,
                                                       pack=pack),
            # SEO card fields → Shopify listing SEO (blank = Shopify derives its
            # own). Clamped to Shopify's hard limits so a long imported meta
            # description never trips a publish userError.
            "seo_title": (product.seo_title or "").strip()[:_SEO_TITLE_MAX],
            "seo_description": (product.seo_description or "").strip()[:_SEO_DESC_MAX],
            # vendor = the canonical product manufacturer (Cummins / Caterpillar /
            # International / …). Prefers Product.manufacturer (the curated value
            # backed by app/routers/products.py MANUFACTURERS) and falls back to
            # the legacy engine_manufacturer field — never the supplier name
            # (PAI stays hidden).
            "vendor": (product.manufacturer or product.engine_manufacturer
                       or _VENDOR_FALLBACK),
            "product_type": (product.category.name if product.category else "") or "Engine Parts",
            "status": "DRAFT",
            "tags": tags,
            "price": price,
            "cost": cost,
            "barcode": product.barcode or "",
            # Pack weight too — the listing IS the pack, and understating weight
            # ×pack would undercharge Shopify's weight-based shipping brackets.
            "weight_lbs": float(product.weight_lbs or 0) * pack_for_weight,
            "images": listing_images,
            "metafields": {
                "pai_part_no": (src.vendor_part_number if src else ""),
                "oem_references": oem_refs,
                "engine_applications": apps,
                "warranty_months": int(product.supplier_warranty_months or 0),
                "pack_qty": pack if pack > 1 else 0,
            },
            "shopify_product_id": product.shopify_product_id or None,
        }

    @staticmethod
    def _store_title(product: Product, base_title: str) -> str:
        """Build a storefront-quality title for thin scraper titles.

        Interstate-McBee parts arrive with bare type names ("Turbocharger",
        "Follower"). Prepend the customer-facing brand + engine make + model we
        already hold so the listing reads "Interstate-McBee Cummins N14
        Turbocharger". Each token is added only if it isn't already in the base
        title (case-insensitive), so rich PAI titles are never doubled up, and
        the supplier name (PAI) is never shown — only real parts brands."""
        base = (base_title or "").strip()
        low = base.lower()
        out: list[str] = []

        # Brand: show only recognized customer-facing brands (supplier names like
        # PAI / HHP Parts never leak); skip if already a whole word in the title.
        brand = (product.brand or "").strip()
        if brand and _brand_is_customer_facing(brand) and not _word_present(brand, low):
            out.append(brand)

        make = (product.engine_manufacturer or "").strip()
        if not make:
            for a in product.applications:
                if (a.engine_make or "").strip():
                    make = a.engine_make.strip()
                    break
        if make:
            mk = make.title()  # CUMMINS -> Cummins, DETROIT DIESEL -> Detroit Diesel
            # Whole-word check (not substring) so 'CAT' isn't dropped inside
            # 'Locator', and so short models add when genuinely absent.
            if not _word_present(mk, low) and not _word_present(make, low):
                out.append(mk)

        model = (product.engine_model or "").strip()
        if model and not _word_present(model, low):
            out.append(model)

        out.append(base)
        title = " ".join(t for t in out if t).strip()
        if len(title) > _PRODUCT_TITLE_MAX:
            # Trim on a word boundary so the title never ends mid-token.
            title = title[:_PRODUCT_TITLE_MAX].rsplit(" ", 1)[0].rstrip()
        # Never emit a blank title (malformed thin row) — fall back to the SKU.
        return title or base or (product.sku or "").strip()

    @staticmethod
    def _pack_title(title: str, pack: int) -> str:
        """Suffix a sell-pack listing's title with '(Pack of N)'. The suffix must
        SURVIVE the length cap (a pack price without the pack callout reads as a
        5x overcharge), so the base is re-trimmed on a word boundary to make room.
        A previous '(Pack of X)' marker is stripped first (self-heals a changed
        pack size); a title that already states the pack in some other wording
        ("5 PACK", "5-pack") is left alone — never doubled up."""
        if pack <= 1:
            return title
        title = re.sub(r"\s*\(Pack of \d+\)", "", title, flags=re.I).rstrip()
        low = title.lower()
        if f"pack of {pack}" in low or f"{pack} pack" in low or f"{pack}-pack" in low:
            return title
        suffix = f" (Pack of {pack})"
        if len(title) + len(suffix) > _PRODUCT_TITLE_MAX:
            title = (title[:_PRODUCT_TITLE_MAX - len(suffix)]
                     .rsplit(" ", 1)[0].rstrip())
        return f"{title}{suffix}"

    _PRODUCT_CONTENT_QUERY = (
        "query packContent($id: ID!) { product(id: $id) { title descriptionHtml } }"
    )
    _PACK_NOTE_RE = re.compile(
        r"<p><strong>Sold in a pack of \d+\.</strong>[^<]*</p>", re.I)

    def _pack_content_updates(self, pid: str, pack: int,
                              live: dict | None = None) -> dict:
        """Title/descriptionHtml changes needed so the LIVE listing calls out its
        sell pack. Live titles/descriptions are SEO-curated ON Shopify (the ERP's
        thin catalog titles must never clobber them), so this reads the current
        content and only APPENDS/refreshes the pack marker: '(Pack of N)' on the
        title, a 'Sold in a pack of N' lead paragraph on the description.
        Idempotent — returns {} when the listing is already marked. Fail-soft —
        returns {} when the live read fails (the next sync heals it). ``live``
        lets a bulk caller pass prefetched {title, descriptionHtml}."""
        if pack <= 1:
            return {}
        if live is None:
            d = self._graphql(self._PRODUCT_CONTENT_QUERY, {"id": pid})
            live = (d.get("data") or {}).get("product")
        if not live:
            # Read failed / listing gone: touch NOTHING (prepending the pack note
            # to a desc we couldn't read would WIPE the curated description).
            return {}
        out: dict = {}
        lt = (live.get("title") or "").strip()
        if lt:
            nt = self._pack_title(lt, pack)
            if nt != lt:
                out["title"] = nt
        ld = live.get("descriptionHtml") or ""
        if f"sold in a pack of {pack}" not in ld.lower():
            base = self._PACK_NOTE_RE.sub("", ld)   # drop a stale pack-size note
            out["descriptionHtml"] = (
                f"<p><strong>Sold in a pack of {pack}.</strong> "
                f"Price shown is for {pack} pieces.</p>" + base)
        return out

    @staticmethod
    def _description_html(product: Product, apps: list[str], oem_refs: list[str],
                          *, pack: int = 1) -> str:
        parts = [f"<p>{product.title}</p>"]
        if pack > 1:
            parts.append(f"<p><strong>Sold in a pack of {pack}.</strong> "
                         f"Price shown is for {pack} pieces.</p>")
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
        if meta.get("pack_qty"):
            metafields.append({"namespace": _METAFIELD_NS, "key": "pack_qty",
                               "type": "number_integer", "value": str(meta["pack_qty"])})

        variant = {
            "optionValues": [{"optionName": "Title", "name": "Default Title"}],
            "price": f'{listing["price"]:.2f}',
            # SKU + cost live on the variant's inventory item in productSet.
            "inventoryItem": {"sku": listing["sku"], "tracked": False,
                              "cost": f'{listing["cost"]:.2f}'},
        }
        # Variant weight drives Shopify's weight-based shipping rates (the storefront
        # Freight/Economy/Standard brackets). Lives on the inventory item's
        # measurement. Only sent when we hold a real weight (> 0) so an unweighted
        # ERP product never CLOBBERS a weight a merchant set by hand on Shopify —
        # same don't-overwrite-with-blank discipline as vendor/tags above.
        _wt = float(listing.get("weight_lbs") or 0)
        if _wt > 0:
            variant["inventoryItem"]["measurement"] = {
                "weight": {"value": _wt, "unit": "POUNDS"}}
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
        # SEO title/meta from the product's SEO card — only sent when set, so
        # Shopify keeps auto-deriving for products the owner hasn't curated.
        if listing.get("seo_title") or listing.get("seo_description"):
            inp["seo"] = {}
            if listing.get("seo_title"):
                inp["seo"]["title"] = listing["seo_title"]
            if listing.get("seo_description"):
                inp["seo"]["description"] = listing["seo_description"]
        # Only a real GID is an update target — imports park the HANDLE in
        # shopify_product_id, and productSet would reject it as an invalid id.
        spid = listing.get("shopify_product_id") or ""
        if spid.startswith("gid://"):
            inp["id"] = spid   # update-in-place (idempotent)
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

    def publish_product(self, product: Product, *, status: str = "DRAFT",
                        synchronous: bool = True) -> dict:
        """Create/update one product on Shopify. Idempotent via shopify_product_id.
        Fail-soft: returns {ok: False, error: ...} instead of raising when Shopify
        is unconfigured or the call fails — never blocks ERP work.

        synchronous=False returns the product id immediately and lets Shopify
        process media/inventory in the background — far faster for a bulk draft
        push (the per-call image fetch is the main cost). The single-product UI
        push keeps synchronous=True so the caller sees the finished listing."""
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
                json={"query": self._MUTATION, "variables": {"input": inp, "synchronous": synchronous}},
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
        # synchronous=False: productSet queues the create and returns a null
        # `product` inline — the listing IS created in the background. With no
        # errors/userErrors that is a SUCCESS (queued); the caller captures the
        # real GID afterward via a bulk re-link by SKU. Only treat a null product
        # as failure on a synchronous call (where the id should have come back).
        if not synchronous:
            return {"ok": True, "queued": True, "product": None}
        return {"ok": False, "error": "no product returned", "raw": data}

    # ── Partial update — price + SEO + tags ONLY (existing linked listings) ───
    # Respects the owner's "Price + SEO/tags" choice: uses targeted mutations so
    # title / description / images / publish-status are NEVER overwritten (a full
    # productSet would clobber manual Shopify edits). Requires a linked product
    # (real product GID); variant price only applied when the variant GID is known.
    _PRODUCT_UPDATE = (
        "mutation($input: ProductInput!) {"
        "  productUpdate(input: $input) {"
        "    product { id } userErrors { field message } } }"
    )
    _VARIANT_PRICE_UPDATE = (
        "mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {"
        "  productVariantsBulkUpdate(productId: $productId, variants: $variants) {"
        "    productVariants { id price } userErrors { field message } } }"
    )

    def update_listing_fields(self, product: Product) -> dict:
        """Push ONLY price + SEO + tags to an existing linked listing. Fail-soft.

        ONE exception to the partial-update contract: a sell-pack product
        (pack_qty > 1) also ensures the LIVE title/description carry the pack
        marker ("(Pack of N)" + a sold-in-a-pack note). Its price is the PACK
        price (build_listing × pack_qty), and a pack price must never land on a
        listing whose copy still reads per-piece — a customer would just see the
        price jump 5x with no explanation. The marker is APPENDED to the live
        content via read-modify-write (titles are SEO-curated on Shopify — never
        clobbered) and is a no-op once present."""
        if self.current_user_id is not None:
            self.assert_can(Permission.PUBLISH_SHOPIFY)
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured."}
        pid = (product.shopify_product_id or "").strip()
        if not pid.startswith("gid://"):
            return {"ok": False, "error": "product is not linked to a Shopify listing"}
        listing = self.build_listing(product)

        # 1) tags + SEO + vendor (productUpdate leaves every other field
        # untouched). Vendor rides on the partial update so a manufacturer
        # refresh in the ERP flows to Shopify's storefront facet without a
        # full re-publish.
        prod_input: dict = {"id": pid}
        # ProductInput.tags REPLACES (not merges) — sending [] would WIPE every
        # merchant-curated tag. Only send tags when we actually derived some, so
        # a product with no ERP tags never clobbers hand-added Shopify tags.
        if listing["tags"]:
            prod_input["tags"] = listing["tags"]
        # Vendor: only push when the ERP has a real value (skip the
        # _VENDOR_FALLBACK case, so a product without a manufacturer doesn't
        # clobber a Shopify-side vendor a merchant set by hand).
        if listing.get("vendor") and listing["vendor"] != _VENDOR_FALLBACK:
            prod_input["vendor"] = listing["vendor"]
        if listing.get("seo_title") or listing.get("seo_description"):
            seo: dict = {}
            if listing.get("seo_title"):
                seo["title"] = listing["seo_title"]
            if listing.get("seo_description"):
                seo["description"] = listing["seo_description"]
            prod_input["seo"] = seo
        # Sell-pack listings: the pack marker rides WITH the pack price (see
        # docstring). Read-modify-write against the LIVE title/description —
        # they're SEO-curated on Shopify, so we append the marker to them, never
        # overwrite them with the ERP's thin catalog title. No-op once marked.
        prod_input.update(
            self._pack_content_updates(pid, max(1, int(product.pack_qty or 1))))
        # Nothing to set beyond the id (no tags, no SEO) → skip the call entirely.
        if len(prod_input) > 1:
            d1 = self._graphql(self._PRODUCT_UPDATE, {"input": prod_input})
            errs = (((d1.get("data") or {}).get("productUpdate") or {}).get("userErrors")
                    or d1.get("errors"))
            if errs:
                return {"ok": False, "error": str(errs)[:300]}

        # 2) variant price (only when we know the variant GID). When the product
        # is linked but its variant GID was never captured, the PRICE — the
        # headline of this update — cannot be pushed; surface that honestly via
        # price_synced rather than reporting an unqualified success.
        vid = (product.shopify_variant_id or "").strip()
        price_synced = False
        if vid.startswith("gid://"):
            variants = [{"id": vid, "price": f'{listing["price"]:.2f}'}]
            d2 = self._graphql(self._VARIANT_PRICE_UPDATE,
                               {"productId": pid, "variants": variants})
            verrs = (((d2.get("data") or {}).get("productVariantsBulkUpdate") or {})
                     .get("userErrors") or d2.get("errors"))
            if verrs:
                return {"ok": False, "error": f"price update failed: {str(verrs)[:250]}"}
            price_synced = True
        return {"ok": True, "price_synced": price_synced, "product": {"id": pid}}

    @staticmethod
    def _tick(progress, stage: str, done: int, total: int) -> None:
        """Fire an OPTIONAL progress callback fail-soft. The callback is purely
        observational (it feeds the live job bar with ``[done/total]`` lines); a
        buggy or slow one must never break or stall a live Shopify push, so every
        call is wrapped and swallowed on error."""
        if progress is None:
            return
        try:
            progress(stage, done, total)
        except Exception:  # noqa: BLE001
            pass

    def update_batch(self, product_ids: list[int], *, progress=None) -> dict:
        """Partial-update many linked listings (price + SEO + tags). Fail-soft.
        ``price_skipped`` counts rows that updated tags/SEO but had no variant GID
        so the price could not be pushed — surfaced so a run never silently
        under-syncs the headline field. ``progress`` (optional) is called as
        ``progress("price/SEO", done, total)`` after each listing for live feedback."""
        summary = {"requested": len(product_ids), "updated": 0,
                   "price_skipped": 0, "failed": 0, "errors": []}
        total = len(product_ids)
        for i, pid in enumerate(product_ids, 1):
            p = self.db.get(Product, pid)
            if not p:
                summary["failed"] += 1
                summary["errors"].append({"product_id": pid, "error": "not found"})
                self._tick(progress, "price/SEO", i, total)
                continue
            res = self.update_listing_fields(p)
            if res.get("ok"):
                summary["updated"] += 1
                if not res.get("price_synced"):
                    summary["price_skipped"] += 1
            else:
                summary["failed"] += 1
                summary["errors"].append({"product_id": pid, "sku": p.sku, "error": res.get("error")})
            self._tick(progress, "price/SEO", i, total)
        return summary

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

    def republish_batch(self, product_ids: list[int]) -> dict:
        """Full re-publish that PRESERVES each product's current status — the SAFE
        way to push image/content changes to already-live listings. A linked
        ACTIVE listing stays ACTIVE (updating its image never unpublishes it), a
        linked DRAFT stays DRAFT, and an unlinked product is created as DRAFT (new
        listings never auto-go-live). Needed because the partial update doesn't
        touch images and a plain publish defaults to DRAFT (which would unpublish
        actives). Fail-soft; partial-success safe."""
        summary = {"requested": len(product_ids), "published": 0, "failed": 0, "errors": []}
        for pid in product_ids:
            p = self.db.get(Product, pid)
            if not p:
                summary["failed"] += 1
                summary["errors"].append({"product_id": pid, "error": "not found"})
                continue
            linked = (p.shopify_product_id or "").startswith("gid://")
            status = ((p.shopify_status or "ACTIVE").upper() if linked else "DRAFT")
            res = self.publish_product(p, status=status)
            if res.get("ok"):
                summary["published"] += 1
            else:
                summary["failed"] += 1
                summary["errors"].append({"product_id": pid, "sku": p.sku,
                                          "error": res.get("error")})
        return summary

    # ══ Inventory sync — push real stock to Shopify (ERP is master) ════════════
    # ERP-is-master means OVERWRITE Shopify's number with our sellable
    # qty_available (= on_hand - committed), via inventorySetOnHandQuantities (an
    # absolute SET, never a relative adjust). Quantities live on the InventoryItem
    # (cached as product.shopify_inventory_item_id) at one location. Fail-soft +
    # admin-gated, mirroring the publish path.
    _LOCATIONS_QUERY = (
        "query { locations(first: 20) { nodes { id name isActive fulfillsOnlineOrders } } }"
    )
    _SET_ON_HAND = (
        "mutation set($input: InventorySetOnHandQuantitiesInput!) {"
        "  inventorySetOnHandQuantities(input: $input) {"
        "    userErrors { field message } } }"
    )
    _ITEM_TRACK = (
        "mutation track($id: ID!) {"
        "  inventoryItemUpdate(id: $id, input: { tracked: true }) {"
        "    inventoryItem { id tracked } userErrors { field message } } }"
    )
    _ACTIVATE = (
        "mutation act($itemId: ID!, $locId: ID!) {"
        "  inventoryActivate(inventoryItemId: $itemId, locationId: $locId) {"
        "    inventoryLevel { id } userErrors { field message } } }"
    )
    _SYNC_REF = "gid://jaks-erp/InventorySync/1"   # shows JAKS in Shopify's stock history

    _LOCATIONS_ID_ONLY = "query { locations(first: 10) { nodes { id } } }"

    def _location_id(self) -> str:
        """The Shopify location GID we sync stock to (single warehouse). Cached in
        the shopify_location_id setting; resolved from the locations query on first
        use (prefer an active, online-order-fulfilling location).

        Falls back to an ID-ONLY query when the token lacks the read_locations
        scope — the name/isActive/fulfillsOnlineOrders fields require it, so the
        rich query is denied, but `id` alone is allowed and is all the inventory
        writes need. A single-warehouse store resolves correctly either way."""
        cached = get_setting_value_db(self.db, "shopify_location_id", "").strip()
        if cached.startswith("gid://"):
            return cached
        loc = ""
        data = self._graphql(self._LOCATIONS_QUERY, {})
        nodes = (((data.get("data") or {}).get("locations") or {}).get("nodes") or [])
        if nodes:
            pick = (next((n for n in nodes if n.get("isActive") and n.get("fulfillsOnlineOrders")), None)
                    or next((n for n in nodes if n.get("isActive")), None) or nodes[0])
            loc = pick.get("id") or ""
        if not loc.startswith("gid://"):
            # read_locations not granted → the rich query is denied. Retry with the
            # id-only query (allowed) and take the first warehouse.
            d2 = self._graphql(self._LOCATIONS_ID_ONLY, {})
            n2 = (((d2.get("data") or {}).get("locations") or {}).get("nodes") or [])
            loc = (n2[0].get("id") if n2 else "") or ""
        if loc.startswith("gid://"):
            from app.settings_utils import set_setting_value_db
            set_setting_value_db(self.db, "shopify_location_id", loc)
        return loc

    @staticmethod
    def _sellable_qty(product: Product) -> int:
        """Sellable quantity for the storefront — clamp to >=0 (on-hand may go
        negative on admin corrections; we never publish a negative). A sell-pack
        listing (pack_qty > 1) is sold BY THE PACK, so its storefront stock is
        whole packs: floor(available / pack_qty) — 12 pieces of a 5-pack part is
        2 sellable packs, never 12."""
        qty = max(0, int(product.qty_available or 0))
        return qty // max(1, int(product.pack_qty or 1))

    def _set_errs(self, data: dict):
        return (((data.get("data") or {}).get("inventorySetOnHandQuantities") or {})
                .get("userErrors") or data.get("errors"))

    def _enable_and_set_one(self, item_id: str, loc_id: str, qty: int) -> tuple[bool, str]:
        """Self-heal one item: enable tracking + activate at the location + set the
        absolute on-hand. Used when a batch SET reports the item isn't yet tracked/
        activated. Idempotent (track/activate no-op if already so). Returns (ok, err)."""
        self._graphql(self._ITEM_TRACK, {"id": item_id})
        self._graphql(self._ACTIVATE, {"itemId": item_id, "locId": loc_id})
        d = self._graphql(self._SET_ON_HAND, {"input": {
            "reason": "correction", "referenceDocumentUri": self._SYNC_REF,
            "setQuantities": [{"inventoryItemId": item_id, "locationId": loc_id,
                               "quantity": qty}]}})
        errs = self._set_errs(d)
        return (not errs, str(errs)[:200] if errs else "")

    def sync_inventory(self, product_ids: list[int], *, progress=None) -> dict:
        """Overwrite Shopify's on-hand with each product's sellable qty_available.
        Admin-gated, fail-soft. Batches the absolute SET (<=250/call); any item the
        store hasn't tracked/activated yet is self-healed per-item then retried.
        Products with no cached InventoryItem GID are skipped (link them first).
        Returns {ok, requested, synced, skipped, failed, errors}."""
        if self.current_user_id is not None:
            self.assert_can(Permission.PUBLISH_SHOPIFY)
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured — set shopify_store_url "
                    "and shopify_access_token in Settings."}
        loc = self._location_id()
        if not loc:
            return {"ok": False, "error": "No Shopify location found to sync stock to."}

        summary = {"ok": True, "requested": len(product_ids), "synced": 0,
                   "skipped": 0, "failed": 0, "errors": []}
        items: list[tuple] = []
        for pid in product_ids:
            p = self.db.get(Product, pid)
            if not p:
                summary["failed"] += 1
                continue
            iid = (p.shopify_inventory_item_id or "").strip()
            if not iid.startswith("gid://"):
                summary["skipped"] += 1          # not linked / no inventory item yet
                continue
            items.append((p, iid, self._sellable_qty(p)))

        total_items = len(items)
        for start in range(0, total_items, 250):
            chunk = items[start:start + 250]
            self._tick(progress, "stock", min(start + len(chunk), total_items), total_items)
            d = self._graphql(self._SET_ON_HAND, {"input": {
                "reason": "correction", "referenceDocumentUri": self._SYNC_REF,
                "setQuantities": [{"inventoryItemId": iid, "locationId": loc,
                                   "quantity": q} for (_p, iid, q) in chunk]}})
            if not self._set_errs(d):
                summary["synced"] += len(chunk)
                continue
            # Batch failed — some items aren't tracked/activated. Heal each in turn.
            for (p, iid, q) in chunk:
                ok, err = self._enable_and_set_one(iid, loc, q)
                if ok:
                    summary["synced"] += 1
                else:
                    summary["failed"] += 1
                    if len(summary["errors"]) < 10:
                        summary["errors"].append({"sku": p.sku, "error": err})
        return summary

    def sync_inventory_all_linked(self) -> dict:
        """Convenience: push stock for every active product that has an InventoryItem
        GID (i.e. is linked). Used by the scheduled/nightly sync."""
        ids = [r[0] for r in self.db.query(Product.id).filter(
            Product.is_active == True,  # noqa: E712
            # Vendor full-automation policy: a part the vendor can't supply
            # (out_of_stock / discontinued) is excluded from the storefront feed.
            # Mirror _desired_hidden exactly (a DISCONTINUED-status product is hidden
            # by the reconcile, so it must not get a stock push to its hidden listing).
            Product.vendor_availability.notin_(
                (VendorAvailability.OUT_OF_STOCK, VendorAvailability.DISCONTINUED)),
            Product.status != ProductStatus.DISCONTINUED,
            Product.shopify_inventory_item_id.like("gid://%")).all()]
        return self.sync_inventory(ids)

    # ══ Unified recurring sync (the "Sync now" + nightly action) ══════════════
    def sync_linked(self, product_ids: list[int] | None = None, *, progress=None) -> dict:
        """Keep ALREADY-LINKED listings fresh: re-push price + SEO + tags (the safe
        partial update) AND overwrite stock. Does NOT publish new products or touch
        title/description/images/publish-status. product_ids=None → every active
        linked product. Fail-soft; the called methods enforce the admin gate.
        ``progress`` (optional) is threaded to each sub-step for live job-bar feedback."""
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured — set shopify_store_url "
                    "and shopify_access_token in Settings."}
        # 0) Make storefront VISIBILITY match vendor supply FIRST: hide listings for
        #    parts the vendor can't supply (still-ACTIVE → DRAFT) and re-list parts
        #    we previously hid that are back in stock. This runs over ALL linked
        #    products (an explicit id list still scopes it) — independent of the
        #    availability exclusion the price/stock refresh below uses, because the
        #    parts to HIDE are exactly the ones that exclusion filters out.
        reconcile = self.reconcile_availability(product_ids, progress=progress)
        if product_ids is None:
            product_ids = [r[0] for r in self.db.query(Product.id).filter(
                Product.is_active == True,  # noqa: E712
                # Vendor full-automation policy: skip parts the vendor lists as
                # out_of_stock / discontinued (an explicit id list still wins).
                # Mirrors _desired_hidden — a DISCONTINUED-status product is hidden
                # by the reconcile above, so it must not also get a price/stock push.
                Product.vendor_availability.notin_(
                    (VendorAvailability.OUT_OF_STOCK, VendorAvailability.DISCONTINUED)),
                Product.status != ProductStatus.DISCONTINUED,
                Product.shopify_product_id.like("gid://%")).all()]
        content = self.update_batch(product_ids, progress=progress)
        stock = self.sync_inventory(product_ids, progress=progress)
        return {"ok": True, "products": len(product_ids),
                "content_updated": content.get("updated", 0),
                "price_skipped": content.get("price_skipped", 0),
                "stock_synced": stock.get("synced", 0),
                "hidden": reconcile.get("hidden", 0),
                "relisted": reconcile.get("relisted", 0),
                "failed": (content.get("failed", 0) + stock.get("failed", 0)
                           + reconcile.get("failed", 0)),
                "content_errors": (content.get("errors") or [])[:5],
                "stock_errors": (stock.get("errors") or [])[:5],
                "reconcile_errors": (reconcile.get("errors") or [])[:5]}

    # ══ Availability reconcile — make the LIVE storefront match vendor supply ══
    # The import flags a part out_of_stock/discontinued, but flagging alone does
    # NOT pull a listing that is ALREADY LIVE on Shopify down (the recurring sync
    # merely stops *refreshing* it — the live listing stays ACTIVE and buyable).
    # This closes that gap. Two directions, both gated + fail-soft:
    #   • HIDE   — an unavailable part whose listing is still ACTIVE → productUpdate
    #              status:DRAFT (DRAFT also unpublishes it from the Online Store).
    #   • RELIST — a back-in-stock part whose listing is DRAFT *and the ERP itself
    #              hid it* → REST publish (status:active + published:true).
    # The shopify_hidden_by_erp gate guarantees we never auto-resurrect a listing a
    # human drafted for any other reason (owner decision 2026-06-24).
    def _desired_hidden(self, p: Product) -> bool:
        """True when the storefront listing should be HIDDEN because the vendor
        can't supply the part. Mirrors ProductImportService's full-automation
        policy: explicit out_of_stock/discontinued, a deactivated product, or a
        discontinued status all mean 'off the storefront'."""
        if (p.vendor_availability or "").strip() in (
                VendorAvailability.OUT_OF_STOCK, VendorAvailability.DISCONTINUED):
            return True
        if not p.is_active:
            return True
        if (p.status or "") == ProductStatus.DISCONTINUED:
            return True
        return False

    @staticmethod
    def _numeric_id(gid: str) -> str:
        """gid://shopify/Product/123456 → '123456' (REST needs the numeric id)."""
        return (gid or "").rsplit("/", 1)[-1].strip()

    def _rest_product_update(self, numeric_id: str, fields: dict) -> tuple[bool, str]:
        """REST PUT products/{id}.json — used for the publish-to-Online-Store flip
        (status + published). The ERP token has write_products but NOT
        write_publications, so the GraphQL publishablePublish path is unavailable;
        REST 'published:true' is the proven workaround (.shopify-work/publish_rest.py).
        Fail-soft: returns (ok, err)."""
        import httpx
        if not numeric_id.isdigit():
            return False, f"non-numeric product id: {numeric_id!r}"
        url = (f"https://{self._store_domain()}/admin/api/{_API_VERSION}"
               f"/products/{numeric_id}.json")
        body = {"product": {"id": int(numeric_id), **fields}}
        try:
            resp = httpx.put(
                url, json=body,
                headers={"X-Shopify-Access-Token": self._token(),
                         "Content-Type": "application/json"},
                timeout=30.0,
            )
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            return True, ""
        except Exception as exc:  # noqa: BLE001 — fail-soft on any network error
            return False, f"network error: {exc}"

    def reconcile_availability(self, product_ids: list[int] | None = None,
                              *, dry_run: bool = False, progress=None) -> dict:
        """Make each LINKED listing's live visibility match vendor supply.
        product_ids=None → every linked product (an explicit list scopes it).
        Admin-gated, fail-soft. dry_run computes the plan and writes nothing — the
        one-time audit uses it. Returns {ok, considered, hidden, relisted, failed,
        dry_run, errors, sample}."""
        if self.current_user_id is not None:
            self.assert_can(Permission.PUBLISH_SHOPIFY)
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured — set shopify_store_url "
                    "and shopify_access_token in Settings."}
        # Candidate set = every LINKED product (real product GID). We must include
        # OOS/discontinued products — they are exactly the ones to hide — so this
        # deliberately does NOT apply the availability exclusion the sync uses.
        q = self.db.query(Product).filter(Product.shopify_product_id.like("gid://%"))
        if product_ids is not None:
            if not product_ids:
                return {"ok": True, "considered": 0, "hidden": 0, "relisted": 0,
                        "failed": 0, "dry_run": dry_run, "errors": [], "sample": []}
            q = q.filter(Product.id.in_(product_ids))

        summary = {"ok": True, "considered": 0, "hidden": 0, "relisted": 0,
                   "failed": 0, "dry_run": dry_run, "errors": [], "sample": []}
        rows = q.all()
        total = len(rows)
        for i, p in enumerate(rows, 1):
            summary["considered"] += 1
            self._tick(progress, "reconcile", i, total)
            live = (p.shopify_status or "").strip().upper()
            want_hidden = self._desired_hidden(p)
            # DELIBERATE: if a human re-activated an OOS part on Shopify, this
            # re-hides it (vendor still can't supply it → the owner's "never
            # oversell" priority wins over a manual re-activation). It is NOT silent
            # — every hide is counted + sampled in the returned summary. Flip this
            # to leave-and-flag if the owner ever wants manual re-activation to win.
            if want_hidden and live == "ACTIVE":
                action = "hide"
            elif (not want_hidden) and live == "DRAFT" and p.shopify_hidden_by_erp:
                action = "relist"   # only re-list listings the ERP itself hid
            else:
                continue

            if dry_run:
                summary["hidden" if action == "hide" else "relisted"] += 1
                if len(summary["sample"]) < 25:
                    summary["sample"].append({"sku": p.sku, "action": action})
                continue

            if action == "hide":
                d = self._graphql(self._PRODUCT_UPDATE, {"input": {
                    "id": p.shopify_product_id, "status": "DRAFT"}})
                errs = (((d.get("data") or {}).get("productUpdate") or {})
                        .get("userErrors") or d.get("errors"))
                if errs:
                    summary["failed"] += 1
                    if len(summary["errors"]) < 10:
                        summary["errors"].append(
                            {"sku": p.sku, "action": "hide", "error": str(errs)[:200]})
                    continue
                p.shopify_status = "DRAFT"
                p.shopify_hidden_by_erp = True
                summary["hidden"] += 1
            else:  # relist
                ok, err = self._rest_product_update(
                    self._numeric_id(p.shopify_product_id),
                    {"status": "active", "published": True})
                if not ok:
                    summary["failed"] += 1
                    if len(summary["errors"]) < 10:
                        summary["errors"].append(
                            {"sku": p.sku, "action": "relist", "error": err})
                    continue
                p.shopify_status = "ACTIVE"
                p.shopify_hidden_by_erp = False
                summary["relisted"] += 1

            # Persist this change IMMEDIATELY — the Shopify side effect already
            # happened and is irreversible. A coarse chunk-commit would risk a crash
            # between the live hide and the DB write, which would lose
            # shopify_hidden_by_erp and strand the listing as DRAFT-forever (the HIDE
            # branch needs live==ACTIVE to re-set the flag, and RE-LIST needs the
            # flag — so it could never be auto-re-listed). One commit per CHANGED
            # product (not per considered) keeps this cheap.
            self.db.commit()
            if len(summary["sample"]) < 25:
                summary["sample"].append({"sku": p.sku, "action": action})
        return summary

    def refresh_live_status(self) -> dict:
        """Read-only walk of the live store; refresh the cached shopify_status (and
        backfill any missing variant/inventory GIDs) for every LINKED product by
        matching its stored product GID. Makes the availability reconcile
        authoritative against the LIVE store, not a possibly-stale cache. Admin-gated,
        fail-soft."""
        if self.current_user_id is not None:
            self.assert_can(Permission.PUBLISH_SHOPIFY)
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured."}
        self._last_error = ""
        variants = self._fetch_all_variants()
        if variants is None:
            return {"ok": False,
                    "error": getattr(self, "_last_error", "") or "variant fetch failed"}
        by_pid: dict[str, dict] = {}
        for v in variants:
            if v.get("product_id"):
                by_pid.setdefault(v["product_id"], v)   # first variant carries product status
        seen = updated = 0
        for p in (self.db.query(Product)
                  .filter(Product.shopify_product_id.like("gid://%")).all()):
            v = by_pid.get(p.shopify_product_id)
            if not v:
                continue
            seen += 1
            new_status = (v.get("status") or "")[:20]
            if new_status and new_status.upper() != (p.shopify_status or "").upper():
                p.shopify_status = new_status
                updated += 1
            if (not (p.shopify_variant_id or "").startswith("gid://")
                    and (v.get("variant_id") or "").startswith("gid://")):
                p.shopify_variant_id = v["variant_id"]
            if (not (p.shopify_inventory_item_id or "").startswith("gid://")
                    and (v.get("inventory_item_id") or "").startswith("gid://")):
                p.shopify_inventory_item_id = v["inventory_item_id"]
            if seen % 500 == 0:
                self.db.commit()
        self.db.commit()
        return {"ok": True, "linked_seen": seen, "status_updated": updated,
                "store_variants": len(variants)}

    # ══ Phase A — Connect & Link (read-only against Shopify) ══════════════════
    # The store already carries the catalog (scraper-fed, old JAKS-PAI-#### SKUs).
    # Linking walks every Shopify variant ONCE (paginated, read-only), then matches
    # ERP products locally by vendor-source SKU / ERP SKU / handle and stores the
    # REAL GIDs. Without this, the first publish would duplicate the whole store —
    # imports park the handle (not a GID) in shopify_product_id.

    def _graphql(self, query: str, variables: dict) -> dict:
        """One fail-soft GraphQL call. Returns {} + raises nothing on transport
        errors — callers treat a missing 'data' key as failure."""
        import httpx
        url = f"https://{self._store_domain()}/admin/api/{_API_VERSION}/graphql.json"
        try:
            resp = httpx.post(
                url, json={"query": query, "variables": variables},
                headers={"X-Shopify-Access-Token": self._token(),
                         "Content-Type": "application/json"},
                timeout=30.0,
            )
            return resp.json() or {}
        except Exception as exc:  # noqa: BLE001 — fail-soft
            return {"errors": [{"message": f"network/parse error: {exc}"}]}

    _VARIANTS_QUERY = (
        "query($cursor: String) {"
        "  productVariants(first: 250, after: $cursor) {"
        "    pageInfo { hasNextPage endCursor }"
        "    nodes { id sku product { id handle status } inventoryItem { id } }"
        "  }"
        "}"
    )

    def _fetch_all_variants(self) -> list[dict] | None:
        """Pull every variant in the store: [{variant_id, sku, product_id, handle,
        status}]. ~60 requests for a 13k catalog. None on any API failure (the
        underlying Shopify error is parked on self._last_error for surfacing)."""
        out: list[dict] = []
        cursor = None
        while True:
            data = self._graphql(self._VARIANTS_QUERY, {"cursor": cursor})
            if data.get("errors") or "data" not in data:
                errs = data.get("errors")
                if isinstance(errs, list) and errs:
                    self._last_error = "; ".join(
                        str(e.get("message", e)) for e in errs[:3])
                else:
                    self._last_error = str(errs or data)[:300]
                return None
            block = (data["data"] or {}).get("productVariants") or {}
            for n in block.get("nodes") or []:
                prod = n.get("product") or {}
                out.append({
                    "variant_id": n.get("id") or "",
                    "sku": (n.get("sku") or "").strip(),
                    "product_id": prod.get("id") or "",
                    "handle": (prod.get("handle") or "").strip().lower(),
                    "status": prod.get("status") or "",
                    "inventory_item_id": (n.get("inventoryItem") or {}).get("id") or "",
                })
            page = block.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return out
            cursor = page.get("endCursor")

    def link_status(self) -> dict:
        """Counts for the Settings card: configured / linked / unlinked."""
        total = self.db.query(Product).filter(Product.is_active == True).count()  # noqa: E712
        linked = (self.db.query(Product)
                  .filter(Product.is_active == True,  # noqa: E712
                          Product.shopify_product_id.like("gid://%")).count())
        return {"configured": self.is_configured(), "total": total,
                "linked": linked, "unlinked": total - linked}

    def match_and_link(self) -> dict:
        """Match every unlinked active ERP product to its existing Shopify listing
        and store the real product/variant GIDs. READ-ONLY against Shopify; the
        only writes are to the ERP's shopify_* columns. Idempotent — already-linked
        products (gid:// prefix) are skipped, so re-running is always safe."""
        if self.current_user_id is not None:
            self.assert_can(Permission.PUBLISH_SHOPIFY)
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured — set shopify_store_url "
                    "and shopify_access_token in Settings."}

        self._last_error = ""
        variants = self._fetch_all_variants()
        if variants is None:
            detail = getattr(self, "_last_error", "") or "no detail"
            hint = ""
            tok = self._token()
            if tok and not tok.startswith("shpat_"):
                hint = (" — the saved token doesn't look like an Admin API access token "
                        "(custom-app tokens start with shpat_; create one under Shopify "
                        "Admin → Settings → Apps and sales channels → Develop apps)")
            return {"ok": False,
                    "error": f"Shopify rejected the variant listing "
                             f"[{self._store_domain()}]: {detail}{hint}"}

        by_sku: dict[str, dict] = {}
        by_handle: dict[str, dict] = {}
        for v in variants:
            if v["sku"]:
                by_sku.setdefault(v["sku"].upper(), v)
            if v["handle"]:
                by_handle.setdefault(v["handle"], v)

        unlinked = (self.db.query(Product)
                    .filter(Product.is_active == True,  # noqa: E712
                            ~Product.shopify_product_id.like("gid://%"))
                    .all())
        # vendor_id → vendor_code, for building the store's historical SKU shapes
        from app.models.vendor import Vendor
        vendor_codes = {v.id: (v.vendor_code or "").strip().upper()
                        for v in self.db.query(Vendor).all()}
        summary = {"ok": True, "store_variants": len(variants),
                   "candidates": len(unlinked), "linked": 0, "unmatched": 0,
                   "sample_unmatched": []}
        n = 0
        for p in unlinked:
            hit = None
            # 1) vendor-source SKUs. The ERP stores the RAW part number; the live
            #    store's listings (scraper-fed) use ``JAKS-PAI-<part#>`` (13.3k of
            #    13.5k variants, verified 2026-06-12) with some ``JAKS-<part#>``.
            #    Build every historical shape from the part # + vendor code.
            for src in p.vendor_sources:
                part = (src.vendor_part_number or "").strip().upper()
                code = vendor_codes.get(src.vendor_id, "")
                candidates = [src.vendor_sku, src.vendor_part_number]
                if part:
                    candidates.append(f"JAKS-{part}")
                    if code:
                        candidates.append(f"JAKS-{code}-{part}")
                for key in candidates:
                    if key and key.strip().upper() in by_sku:
                        hit = by_sku[key.strip().upper()]
                        break
                if hit:
                    break
            # 2) the ERP's own (scheme) SKU
            if not hit and p.sku and p.sku.upper() in by_sku:
                hit = by_sku[p.sku.upper()]
            # 3) the parked handle from import
            if not hit:
                handle = (p.shopify_product_id or "").strip().lower()
                if handle and handle in by_handle:
                    hit = by_handle[handle]
            if hit:
                p.shopify_product_id = hit["product_id"]
                p.shopify_variant_id = hit["variant_id"]
                p.shopify_inventory_item_id = hit.get("inventory_item_id") or ""
                p.shopify_status = (hit["status"] or "")[:20]
                summary["linked"] += 1
            else:
                summary["unmatched"] += 1
                if len(summary["sample_unmatched"]) < 10:
                    summary["sample_unmatched"].append(p.sku)
            n += 1
            if n % 500 == 0:
                self.db.commit()    # chunk commits — 13k rows
        self.db.commit()

        # Safety guard: a reachable store with thousands of variants but a
        # near-total match MISS almost always means a SKU-shape/config problem,
        # not "these are all genuinely new". Flag it as not-ok so the operator
        # investigates BEFORE a push creates duplicate listings for the whole
        # catalog. (A legitimately small store, or a small candidate set, is
        # exempt.)
        if (summary["candidates"] >= 50 and summary["store_variants"] >= 500
                and summary["linked"] == 0):
            summary["ok"] = False
            summary["warning"] = (
                f"Linked 0 of {summary['candidates']} candidates although the store "
                f"has {summary['store_variants']} variants — likely a SKU-shape or "
                "configuration mismatch. Investigate before pushing (a push now would "
                "create duplicate listings).")
        return summary


# ── Background worker (manual "Sync now" + nightly schedule) ───────────────────
def run_background_shopify_sync(user_id: int | None = None,
                                product_ids: list[int] | None = None) -> None:
    """Refresh price + stock for linked Shopify listings, recording the outcome to
    settings (shopify_last_sync / shopify_last_sync_error / shopify_last_sync_summary)
    so the UI can surface it. Opens its OWN DB session (the request session is closed
    by the time a BackgroundTask runs). user_id=None = system run (permission bypass).
    Mirrors app/services/import_review_service.run_background_staging."""
    from datetime import datetime
    from app.database import SessionLocal
    from app.settings_utils import set_setting_value_db
    db = SessionLocal()
    try:
        res = ShopifyService(db, user_id).sync_linked(product_ids)
        set_setting_value_db(db, "shopify_last_sync",
                             datetime.now().isoformat(timespec="seconds"))
        if res.get("ok"):
            set_setting_value_db(db, "shopify_last_sync_error", "")
            set_setting_value_db(
                db, "shopify_last_sync_summary",
                f"{res.get('content_updated', 0)} listings, "
                f"{res.get('stock_synced', 0)} stock, "
                f"{res.get('hidden', 0)} hidden, {res.get('relisted', 0)} re-listed, "
                f"{res.get('failed', 0)} failed")
        else:
            set_setting_value_db(db, "shopify_last_sync_error",
                                 str(res.get("error", ""))[:300])
        db.commit()
    except Exception as exc:  # noqa: BLE001 — never let a background sync crash the worker
        db.rollback()
        try:
            set_setting_value_db(db, "shopify_last_sync_error", str(exc)[:300])
            db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
