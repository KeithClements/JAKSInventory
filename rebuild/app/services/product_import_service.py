"""
app/services/product_import_service.py
======================================
Phase 2 — product catalog importer with TWO explicit modes (owner-locked):

  1. FULL PRODUCT IMPORT  — CREATE products from the PAI scraper's Shopify-export
     CSV. Maps Variant Price → price_override (OUR sell price), Variant Compare At
     Price → compare_at_price. Creates a PAI ProductVendorSource with vendor_cost
     LEFT BLANK (we do not have a true PAI cost in this file). Parses Body HTML for
     OEM cross-refs, engine applications, PAI part #, and warranty. Groups image
     rows by Handle. Idempotent: existing SKUs are skipped, never duplicated.

  2. PRICING UPDATE  — NEVER creates products; matches existing products by SKU.
       • source=pai_cost   → update ONLY ProductVendorSource.vendor_cost and append
                             ProductCostHistory when it changes.
       • source=competitor → upsert competitor_prices and append
                             competitor_price_history when price/shipping/core moves.
     Never touches identity, name, category, description, images, cross-refs,
     applications, warranty, or notes.

LOCKED RULES (do not violate):
  • Variant Price is OUR SELL price — never PAI cost.
  • product.cost stays moving-average COGS (set on PO receipt); never written here.
  • vendor_cost lives only on ProductVendorSource and only changes in Pricing-Update
    pai_cost mode from a TRUE cost column.
  • Competitor pricing is market intelligence — never stored as cost.
  • Every mode supports dry_run=True (compute the full summary, write nothing).
"""
from __future__ import annotations

import csv
import html
import io
import re
from collections import OrderedDict
from datetime import datetime

from app.constants import CrossRefType, ProductStatus
from app.services.classification_service import ClassificationService
from app.models.product import (
    Product, ProductCategory, ProductImage, ProductApplication,
    ProductVendorSource, CrossReference, ProductCostHistory,
)
from app.models.competitor import CompetitorPrice, CompetitorPriceHistory
from app.models.vendor import Vendor
from app.services.base import BaseService

_PAI_VENDOR_NAME = "PAI Industries"
_PAI_VENDOR_CODE = "PAI"

# SKU match aliases (header keys are lowercased before lookup)
_SKU_KEYS = ("jaks_sku", "sku", "variant sku", "internal_sku", "part_number", "jaks_part")
# True PAI cost columns (Pricing-Update pai_cost mode) — NOT "variant price" (that's sell)
_COST_KEYS = ("pai_cost", "vendor_cost", "dealer_cost", "net_cost", "cost")


def _norm(s) -> str:
    return str(s or "").strip().lower()


def _get(row: dict, *keys: str) -> str:
    """First non-empty value among alias keys (row keys pre-lowercased)."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _to_float(s) -> float | None:
    try:
        return round(float(str(s).replace(",", "").replace("$", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _grams_to_lbs(s) -> float:
    try:
        return round(float(str(s).strip()) / 453.592, 3)
    except (TypeError, ValueError):
        return 0.0


def _years_to_months(txt: str) -> int:
    m = re.search(r"([\d.]+)", txt or "")
    if not m:
        return 0
    try:
        return int(round(float(m.group(1)) * 12))
    except ValueError:
        return 0


def _section(body: str, header: str) -> str:
    """HTML between '<strong>{header}:</strong>' and the next '<strong>' header (or
    end). Bounds each section so OEM <li>s never leak into Applications."""
    m = re.search(
        re.escape(header) + r"\s*:\s*</strong>(.*?)(?:<p>\s*<strong>|$)",
        body or "", re.I | re.S,
    )
    return m.group(1) if m else ""


def _list_items(segment: str) -> list[str]:
    return [
        html.unescape(x).strip()
        for x in re.findall(r"<li>(.*?)</li>", segment or "", re.S)
        if html.unescape(x).strip()
    ]


def _split_two(item: str) -> tuple[str, str]:
    """'CUMMINS 3068898' -> ('CUMMINS','3068898'); single token -> ('', token)."""
    parts = (item or "").split(None, 1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", (item or "").strip())


def parse_body_html(body: str) -> dict:
    out = {"pai_part": "", "oem": [], "apps": [], "warranty_months": 0}
    m = re.search(r"PAI Part #\s*:\s*</strong>\s*([^<]+)", body or "", re.I)
    if m:
        out["pai_part"] = m.group(1).strip()
    m = re.search(r"Warranty\s*:\s*</strong>\s*([^<]+)", body or "", re.I)
    if m:
        out["warranty_months"] = _years_to_months(m.group(1))
    out["oem"] = _list_items(_section(body, "OEM References"))
    out["apps"] = _list_items(_section(body, "Applications"))
    return out


class ProductImportService(BaseService):

    # ══ Shopify-export parsing (Full import) ═══════════════════════════════════
    def parse_shopify_csv(self, text: str) -> list[dict]:
        """Group Shopify-export rows by Handle → one dict per product. Image-only
        rows (blank Variant SKU) contribute their Image Src to that product."""
        reader = csv.DictReader(io.StringIO(text))
        products: "OrderedDict[str, dict]" = OrderedDict()
        for raw in reader:
            row = {_norm(k): v for k, v in raw.items()}
            handle = _get(row, "handle")
            sku = _get(row, "variant sku")
            img = _get(row, "image src")
            if not handle and not sku:
                continue
            key = handle or sku
            p = products.setdefault(key, {"handle": handle, "images": []})
            if sku and "sku" not in p:
                parsed = parse_body_html(raw.get("Body (HTML)") or row.get("body (html)") or "")
                p.update({
                    "sku": sku,
                    "title": _get(row, "title"),
                    "type": _get(row, "type"),
                    "tags": _get(row, "tags"),
                    "price": _get(row, "variant price"),
                    "compare_at": _get(row, "variant compare at price"),
                    "barcode": _get(row, "variant barcode"),
                    "grams": _get(row, "variant grams"),
                    "status": _get(row, "status"),
                    "pai_part": parsed["pai_part"],
                    "oem": parsed["oem"],
                    "apps": parsed["apps"],
                    "warranty_months": parsed["warranty_months"],
                })
            if img:
                p["images"].append({
                    "url": img,
                    "alt": _get(row, "image alt text"),
                    "pos": _get(row, "image position"),
                })
        return [p for p in products.values() if p.get("sku")]

    # ══ Mode 1: FULL PRODUCT IMPORT ════════════════════════════════════════════
    def full_import(self, text: str, *, dry_run: bool = True,
                    import_images: bool = True, limit: int | None = None) -> dict:
        rows = self.parse_shopify_csv(text)
        if limit:
            rows = rows[:limit]
        summary = {
            "mode": "full_import", "dry_run": dry_run,
            "products_seen": len(rows), "created": 0, "skipped_existing": 0,
            "skipped_no_sku": 0, "cross_refs": 0, "applications": 0, "images": 0,
            "categories_created": 0, "vendor_sources": 0, "needs_review": 0,
            "classified": 0, "sample": [],
        }
        if not rows:
            return summary

        existing = {s.strip().lower() for (s,) in self.db.query(Product.sku).all() if s}
        cat_cache = {c.name.strip().lower(): c.id for c in self.db.query(ProductCategory).all()}
        pai_id = self._resolve_pai_vendor(dry_run)
        classifier = ClassificationService(self.db)   # §18.6 — rules cache built once
        seen: set[str] = set()
        committed = 0

        for p in rows:
            sku = p.get("sku", "")
            k = sku.lower()
            if not sku:
                summary["skipped_no_sku"] += 1
                continue
            if k in existing or k in seen:
                summary["skipped_existing"] += 1
                continue
            seen.add(k)

            cat_id = self._resolve_category(p["type"], cat_cache, summary, dry_run)
            # §18.6 — refine below the Shopify-Type category + derive engine make.
            cls = classifier.classify(
                title=p["title"], tags=p.get("tags", ""),
                app_makes=[_split_two(a)[0] for a in p["apps"]],
                app_models=[_split_two(a)[1] for a in p["apps"]],
            )
            if cls["needs_review"]:
                summary["needs_review"] += 1
            if cls["category_id"] or cls["engine_manufacturer"]:
                summary["classified"] += 1
            n_oem = sum(1 for it in p["oem"] if _split_two(it)[1])
            n_app = len(p["apps"])
            n_img = len(p["images"]) if import_images else 0
            summary["created"] += 1
            summary["cross_refs"] += n_oem
            summary["applications"] += n_app
            summary["images"] += n_img
            summary["vendor_sources"] += 1

            if len(summary["sample"]) < 5:
                summary["sample"].append({
                    "sku": sku, "title": p["title"], "type": p["type"],
                    "price_override": _to_float(p["price"]),
                    "compare_at_price": _to_float(p["compare_at"]),
                    "pai_part": p["pai_part"], "oem_refs": n_oem,
                    "applications": n_app, "images": n_img,
                    "warranty_months": p["warranty_months"],
                })

            if dry_run:
                continue

            product = Product(
                sku=sku,
                title=p["title"][:500],
                brand=_PAI_VENDOR_CODE,                          # §18.2 — Brand (correct)
                # §18.2 / A1: do NOT set manufacturer to the vendor name. Manufacturer =
                # engine make (engine_manufacturer), filled by the §18.6 classification
                # pass. Left blank here so Brand != Vendor != Manufacturer stays clean.
                barcode=p["barcode"] or None,
                category_id=cls["category_id"] or cat_id,        # §18.6 — deeper if confident, else Type
                engine_manufacturer=cls["engine_manufacturer"],  # §18 A1 — Manufacturer = engine make
                engine_model=cls["engine_model"],
                needs_review=cls["needs_review"],                 # §18.6 — low-confidence → Import Review
                status=ProductStatus.ACTIVE,
                is_active=True,
                special_order_only=True,
                weight_lbs=_grams_to_lbs(p["grams"]),
                supplier_warranty_months=p["warranty_months"],
                is_warrantable=bool(p["warranty_months"]),
                shopify_product_id=p["handle"],
                shopify_status=p["status"][:20],
                search_keywords=p["tags"],
                price_override=_to_float(p["price"]),          # OUR sell price
                compare_at_price=_to_float(p["compare_at"]),   # marketing compare-at
                enrichment_source="PAI scraper (Shopify export)",
                last_enriched_at=datetime.utcnow(),
            )
            self.db.add(product)
            self.db.flush()

            if pai_id:
                self.db.add(ProductVendorSource(
                    product_id=product.id, vendor_id=pai_id,
                    vendor_part_number=p["pai_part"], vendor_sku=sku,
                    vendor_cost=0.0,        # BLANK — no true PAI cost in this file
                    is_preferred=True,
                ))
            for it in p["oem"]:
                brand, num = _split_two(it)
                if num:
                    self.db.add(CrossReference(
                        product_id=product.id, ref_type=CrossRefType.OEM,
                        ref_number=num, brand=brand, status="proven",
                    ))
            for it in p["apps"]:
                make, model = _split_two(it)
                self.db.add(ProductApplication(
                    product_id=product.id, engine_make=make,
                    engine_model=model, source="PAI",
                ))
            if import_images:
                for i, im in enumerate(p["images"]):
                    self.db.add(ProductImage(
                        product_id=product.id, file_path=im["url"][:500],
                        source="pai", is_primary=(i == 0), alt_text=im["alt"][:300],
                    ))
            committed += 1
            if committed % 500 == 0:
                self.db.commit()
                self.db.expunge_all()

        if dry_run:
            self.db.rollback()
        else:
            self.db.commit()
        return summary

    # ══ Mode 2a: PRICING UPDATE — PAI cost ═════════════════════════════════════
    def pricing_update_pai_cost(self, text: str, *, dry_run: bool = True) -> dict:
        reader = list(csv.DictReader(io.StringIO(text)))
        summary = {
            "mode": "pricing_update", "source": "pai_cost", "dry_run": dry_run,
            "rows": len(reader), "matched": 0, "skipped_no_sku": 0,
            "skipped_no_product": 0, "skipped_no_pai_source": 0,
            "skipped_no_cost": 0, "costs_updated": 0, "unchanged": 0, "sample": [],
        }
        sku_to_id = {_norm(s): pid for pid, s in self.db.query(Product.id, Product.sku).all()}
        pai = self.db.query(Vendor).filter(
            (Vendor.vendor_code == _PAI_VENDOR_CODE) | (Vendor.name == _PAI_VENDOR_NAME)
        ).first()
        for raw in reader:
            row = {_norm(k): v for k, v in raw.items()}
            sku = _get(row, *_SKU_KEYS)
            if not sku:
                summary["skipped_no_sku"] += 1
                continue
            pid = sku_to_id.get(_norm(sku))
            if pid is None:
                summary["skipped_no_product"] += 1          # NEVER create
                continue
            new_cost = _to_float(_get(row, *_COST_KEYS))
            if new_cost is None:
                summary["skipped_no_cost"] += 1
                continue
            src = None
            if pai is not None:
                src = self.db.query(ProductVendorSource).filter(
                    ProductVendorSource.product_id == pid,
                    ProductVendorSource.vendor_id == pai.id,
                ).first()
            if src is None:
                summary["skipped_no_pai_source"] += 1
                continue
            summary["matched"] += 1
            if abs((src.vendor_cost or 0.0) - new_cost) < 0.005:
                summary["unchanged"] += 1
                continue
            old_cost = src.vendor_cost or 0.0
            summary["costs_updated"] += 1
            if len(summary["sample"]) < 5:
                summary["sample"].append({"sku": sku, "old_cost": old_cost, "new_cost": new_cost})
            if not dry_run:
                src.vendor_cost = new_cost
                src.last_cost_updated_at = datetime.utcnow()
                self.db.add(ProductCostHistory(
                    product_id=pid, vendor_id=pai.id, old_cost=old_cost,
                    new_cost=new_cost, changed_by_id=self.current_user_id,
                    notes="Pricing-Update import (PAI cost)",
                ))
        self.db.commit() if not dry_run else self.db.rollback()
        return summary

    # ══ Mode 2b: PRICING UPDATE — competitor ═══════════════════════════════════
    def pricing_update_competitor(self, text: str, *, dry_run: bool = True) -> dict:
        reader = list(csv.DictReader(io.StringIO(text)))
        summary = {
            "mode": "pricing_update", "source": "competitor", "dry_run": dry_run,
            "rows": len(reader), "matched": 0, "skipped_no_sku": 0,
            "skipped_no_product": 0, "created": 0, "updated": 0, "unchanged": 0,
            "history_appended": 0, "sample": [],
        }
        sku_to_id = {_norm(s): pid for pid, s in self.db.query(Product.id, Product.sku).all()}
        for raw in reader:
            row = {_norm(k): v for k, v in raw.items()}
            sku = _get(row, *_SKU_KEYS)
            if not sku:
                summary["skipped_no_sku"] += 1
                continue
            pid = sku_to_id.get(_norm(sku))
            if pid is None:
                summary["skipped_no_product"] += 1          # NEVER create products
                continue
            name = _get(row, "competitor_name", "competitor", "seller")
            price = _to_float(_get(row, "price", "competitor_price"))
            if not name or price is None:
                continue
            summary["matched"] += 1
            part = _get(row, "competitor_part_number", "competitor_part", "their_part")
            ship = _to_float(_get(row, "shipping_price", "shipping")) or 0.0
            core = _to_float(_get(row, "core_charge", "core")) or 0.0
            existing = self.db.query(CompetitorPrice).filter(
                CompetitorPrice.product_id == pid,
                CompetitorPrice.competitor_name == name,
                CompetitorPrice.competitor_part_number == part,
            ).first()

            if existing is None:
                summary["created"] += 1
                if len(summary["sample"]) < 5:
                    summary["sample"].append({"sku": sku, "competitor": name, "price": price, "change": "new"})
                if not dry_run:
                    self.db.add(CompetitorPrice(
                        product_id=pid, competitor_name=name, competitor_part_number=part,
                        competitor_brand=_get(row, "competitor_brand", "brand"),
                        price=price, shipping_price=ship, core_charge=core,
                        availability_status=_get(row, "availability_status", "availability"),
                        url=_get(row, "url"), source=_get(row, "source"),
                        confidence=_get(row, "confidence"), notes=_get(row, "notes"),
                        seen_at=datetime.utcnow(),
                    ))
                continue

            changed = (abs((existing.price or 0) - price) >= 0.005
                       or abs((existing.shipping_price or 0) - ship) >= 0.005
                       or abs((existing.core_charge or 0) - core) >= 0.005)
            if not changed:
                summary["unchanged"] += 1
                continue
            summary["updated"] += 1
            summary["history_appended"] += 1
            if len(summary["sample"]) < 5:
                summary["sample"].append({"sku": sku, "competitor": name,
                                          "old_price": existing.price, "new_price": price})
            if not dry_run:
                pct = round((price - existing.price) / existing.price * 100, 2) if existing.price else 0.0
                self.db.add(CompetitorPriceHistory(
                    competitor_price_id=existing.id,
                    old_price=existing.price or 0.0, new_price=price,
                    old_shipping_price=existing.shipping_price or 0.0, new_shipping_price=ship,
                    old_core_charge=existing.core_charge or 0.0, new_core_charge=core,
                    percent_change=pct, source=_get(row, "source"), seen_at=datetime.utcnow(),
                ))
                existing.price = price
                existing.shipping_price = ship
                existing.core_charge = core
                existing.seen_at = datetime.utcnow()
        self.db.commit() if not dry_run else self.db.rollback()
        return summary

    # ══ helpers ════════════════════════════════════════════════════════════════
    def _resolve_pai_vendor(self, dry_run: bool) -> int | None:
        v = self.db.query(Vendor).filter(
            (Vendor.vendor_code == _PAI_VENDOR_CODE) | (Vendor.name == _PAI_VENDOR_NAME)
        ).first()
        if v:
            return v.id
        if dry_run:
            return None
        v = Vendor(name=_PAI_VENDOR_NAME, vendor_code=_PAI_VENDOR_CODE, is_active=True)
        self.db.add(v)
        self.db.flush()
        return v.id

    def _resolve_category(self, type_name: str, cache: dict, summary: dict, dry_run: bool) -> int | None:
        name = (type_name or "").strip()
        if not name:
            return None
        key = name.lower()
        if key in cache:
            return cache[key]
        summary["categories_created"] += 1
        if dry_run:
            cache[key] = None
            return None
        cat = ProductCategory(name=name[:200], level=1, is_active=True)
        self.db.add(cat)
        self.db.flush()
        cache[key] = cat.id
        return cat.id
