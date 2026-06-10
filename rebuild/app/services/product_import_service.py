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
                             ALSO upserts a competitor CrossReference per part #
                             (R2 — competitor numbers must be searchable) with a
                             GLOBAL collision guard: a normalized ref_number that
                             already exists on a DIFFERENT product is skipped and
                             counted (cross_ref_collisions), never duplicated.
     Never touches identity, name, category, description, images, applications,
     warranty, or notes (competitor cross-refs above are the one R2 exception).

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

from sqlalchemy import func

from app.constants import CrossRefStatus, CrossRefType, ProductStatus
from app.services.classification_service import ClassificationService
from app.services.sku_service import assemble_sku, derive_category_code, engine_code as _engine_code
from app.models.product import (
    Product, ProductCategory, ProductImage, ProductApplication,
    ProductVendorSource, CrossReference, ProductCostHistory,
)
from app.models.competitor import CompetitorPrice, CompetitorPriceHistory
from app.models.vendor import Vendor
from app.services.base import BaseService
from app.utils import normalize_part

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

    # ══ JAKS ERP native format parser (SCRAPER_EXPORT_SPEC.md v1) ═════════════
    def parse_jaks_export_csv(self, text: str) -> list[dict]:
        """Parse the one-row-per-product JAKS ERP native CSV (new spec, 2026-06-08).

        Pipe-delimited multi-values; cost is a real vendor cost (not 0.0).
        Returns the same intermediate dict shape as parse_shopify_csv() so
        full_import() can consume either format without modification.
        """
        reader = csv.DictReader(io.StringIO(text))
        rows: list[dict] = []
        for raw in reader:
            r = {k.strip().lower(): (v or "").strip() for k, v in raw.items()}
            sku = r.get("pai_part_no", "").strip()
            if not sku:
                continue

            # OEM refs: "BRAND:NUMBER|BRAND:NUMBER" → ["BRAND NUMBER", ...]
            # _split_two() in full_import expects space-separated "BRAND NUMBER"
            oem: list[str] = []
            for item in (r.get("oem_refs") or "").split("|"):
                item = item.strip()
                if not item:
                    continue
                if ":" in item:
                    brand_str, _, num = item.partition(":")
                    if num.strip():
                        oem.append(f"{brand_str.strip()} {num.strip()}")
                else:
                    oem.append(item)

            # Applications: engine_make + pipe-list of models → ["MAKE MODEL", ...]
            engine_make = r.get("engine_make", "").strip().upper()
            apps: list[str] = []
            for model in (r.get("engine_models") or "").split("|"):
                model = model.strip().upper()
                if model:
                    apps.append(f"{engine_make} {model}".strip() if engine_make else model)
            if not apps and engine_make:
                apps.append(engine_make)

            # Images: pipe-list → [{url, alt}, ...]
            title = r.get("title", "").strip()
            images = [
                {"url": u.strip(), "alt": title}
                for u in (r.get("image_urls") or "").split("|")
                if u.strip()
            ]

            # warranty_years → months
            try:
                w_months = int(round(float(r.get("warranty_years") or 0) * 12))
            except (TypeError, ValueError):
                w_months = 0

            # weight_grams → lbs (full_import stores as lbs)
            grams_raw = r.get("weight_grams", "").strip()

            # Core charge, is_reman, unit_of_measure, pack_qty (new 2026-06-08)
            core_charge = _to_float(r.get("core_charge")) or 0.0
            is_reman_raw = (r.get("is_reman") or "0").strip()
            is_reman = is_reman_raw in ("1", "true", "yes")
            unit_of_measure = (r.get("unit_of_measure") or "EA").strip().upper() or "EA"
            try:
                pack_qty = max(1, int(r.get("pack_qty") or 1))
            except (TypeError, ValueError):
                pack_qty = 1

            rows.append({
                "sku":            sku,           # raw PAI part number → vendor_sku dedup key
                "title":          title,
                "type":           r.get("category", "").strip(),
                "tags":           engine_make,
                "price":          r.get("sell_price", "").strip(),   # → price_override
                "compare_at":     "",
                "cost":           r.get("cost", "").strip(),         # → vendor_cost
                "barcode":        "",
                "grams":          grams_raw,
                "status":         r.get("status", "active").strip() or "active",
                "pai_part":       sku,                               # raw PAI # for traceability
                "oem":            oem,
                "apps":           apps,
                "images":         images,
                "warranty_months": w_months,
                "handle":         sku.lower(),
                "core_charge":    core_charge,
                "is_reman":       is_reman,
                "unit_of_measure": unit_of_measure,
                "pack_qty":       pack_qty,
            })
        return rows

    @staticmethod
    def detect_format(text: str) -> str:
        """Return 'jaks' if the CSV header matches SCRAPER_EXPORT_SPEC.md v1,
        else 'shopify' (the Shopify multi-row format)."""
        first = text.split("\n", 1)[0].lower()
        return "jaks" if "pai_part_no" in first else "shopify"

    # ══ Mode 1: FULL PRODUCT IMPORT ════════════════════════════════════════════
    def full_import(self, text: str = "", *, dry_run: bool = True,
                    import_images: bool = True, limit: int | None = None,
                    rows: list | None = None) -> dict:
        # `rows` lets a caller (Phase C apply) feed pre-parsed row dicts directly.
        # When text is provided, auto-detect format: JAKS native (pai_part_no header)
        # or legacy Shopify multi-row.
        if rows is None:
            if text and self.detect_format(text) == "jaks":
                rows = self.parse_jaks_export_csv(text)
            else:
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

        # Dedup on the PARKED original number: product.sku is now the regenerated
        # JAKS scheme SKU, so the stable per-part identity is the CSV "Variant SKU"
        # we park on ProductVendorSource.vendor_sku. (Keying dedup on product.sku
        # would re-create everything on the next import.)
        existing = {s.strip().lower() for (s,) in self.db.query(ProductVendorSource.vendor_sku).all() if s}
        cat_cache = {c.name.strip().lower(): c.id for c in self.db.query(ProductCategory).all()}
        # SKU-scheme guardrail (owner-locked 2026-06-06: ONE digit per vendor):
        # the vendor digit comes from the owner-set Vendor.vendor_number — never
        # auto-created or defaulted, or every feed would mint SKUs in another
        # vendor's namespace. Missing vendor/digit → fail cleanly, write nothing.
        pai_vendor = self._resolve_pai_vendor()
        pai_digit = (pai_vendor.vendor_number or "").strip() if pai_vendor else ""
        if not pai_digit:
            problem = ("has no vendor digit (SKU #) set" if pai_vendor
                       else "does not exist")
            summary["error"] = (
                f"Import aborted: vendor '{_PAI_VENDOR_NAME}' {problem}. "
                f"Create vendor '{_PAI_VENDOR_NAME}' with its vendor digit first "
                "(Inventory → Vendors → SKU #), then re-run the import."
            )
            return summary
        pai_id = pai_vendor.id
        classifier = ClassificationService(self.db)   # §18.6 — rules cache built once
        # JAKS SKU scheme: mint JAKS-[ENGINE]-[CATEGORY]-[V][NNNN] at import time.
        # Seed the per-(engine,category) sequence counters from the DB so re-imports
        # continue the numbering instead of colliding.
        cat_code_cache: dict[int, str] = {}
        seq_counters: dict[tuple[str, str], int] = {}
        for ec, cc, mx in (
            self.db.query(Product.engine_code, Product.category_code, func.max(Product.part_seq))
            .filter(Product.part_seq.isnot(None))
            .group_by(Product.engine_code, Product.category_code).all()
        ):
            seq_counters[(ec or "", cc or "")] = mx or 0
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
            # JAKS SKU scheme — mint the customer-facing SKU now; the raw CSV SKU is
            # parked on the vendor source (vendor_sku, below) so it stays searchable.
            ecode = _engine_code(cls["engine_model"] or "")
            ccode = self._sku_cat_code(cls["category_id"] or cat_id, p["type"], cat_code_cache)
            new_seq = seq_counters.get((ecode, ccode), 0) + 1
            seq_counters[(ecode, ccode)] = new_seq
            new_sku = assemble_sku(ccode, pai_digit, new_seq, engine_code=ecode)
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

            # Core / reman fields — present in JAKS native format, absent (default 0)
            # in legacy Shopify format.
            p_core_charge = _to_float(p.get("core_charge")) or 0.0
            p_is_reman    = bool(p.get("is_reman", False))
            p_uom         = (p.get("unit_of_measure") or "EA") or "EA"

            product = Product(
                sku=new_sku,                                     # JAKS scheme SKU (raw CSV sku parked on the vendor source)
                engine_code=ecode, category_code=ccode, part_seq=new_seq,
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
                enrichment_source="PAI scraper (JAKS export)" if p.get("core_charge") is not None
                                  else "PAI scraper (Shopify export)",
                last_enriched_at=datetime.utcnow(),
                # Core charge — vendor and customer default to the scraped amount.
                # is_reman alone (no dollar amount) still sets has_core=True so the
                # core-return lifecycle activates; owner sets the amount on the product.
                has_core=p_core_charge > 0 or p_is_reman,
                vendor_core_charge=p_core_charge,
                customer_core_charge=p_core_charge,
                is_reman=p_is_reman,
                unit_of_measure=p_uom,
                pack_qty=p.get("pack_qty") or 1,
            )
            self.db.add(product)
            self.db.flush()

            if pai_id:
                self.db.add(ProductVendorSource(
                    product_id=product.id, vendor_id=pai_id,
                    vendor_part_number=p["pai_part"], vendor_sku=sku,
                    vendor_cost=_to_float(p.get("cost")) or 0.0,
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
        sku_to_id = self._sku_to_id_map()
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
            "history_appended": 0, "cross_refs_created": 0,
            "cross_ref_collisions": 0, "cross_ref_collision_sample": [],
            "sample": [],
        }
        sku_to_id = self._sku_to_id_map()
        # R2 — competitor numbers must be searchable. Snapshot ALL existing
        # cross-ref numbers (normalized → owning product ids) once per run for
        # the global collision check; also dedupes within this file/run.
        xref_owners: dict[str, set[int]] = {}
        for _xpid, _xnum in self.db.query(
            CrossReference.product_id, CrossReference.ref_number
        ).all():
            _nrn = normalize_part(_xnum or "")
            if _nrn:
                xref_owners.setdefault(_nrn, set()).add(_xpid)
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

            # R2 — upsert a competitor CrossReference for this part # so the
            # quote-screen search finds the product by the competitor's number.
            # Global collision guard: if the normalized number already exists on
            # a DIFFERENT product, skip + count (no silent cross-product dupes).
            # Already-on-this-product → no-op, so re-runs are idempotent.
            npart = normalize_part(part)
            if npart:
                owners = xref_owners.get(npart)
                if owners and pid not in owners:
                    summary["cross_ref_collisions"] += 1
                    if len(summary["cross_ref_collision_sample"]) < 5:
                        summary["cross_ref_collision_sample"].append({
                            "sku": sku, "competitor_part_number": part,
                            "existing_product_ids": sorted(owners),
                        })
                elif not owners:
                    summary["cross_refs_created"] += 1
                    xref_owners.setdefault(npart, set()).add(pid)
                    if not dry_run:
                        self.db.add(CrossReference(
                            product_id=pid, ref_type=CrossRefType.COMPETITOR,
                            ref_number=part,
                            brand=_get(row, "competitor_brand", "brand") or name,
                            status=CrossRefStatus.FOUND,
                            notes="Competitor pricing import",
                        ))

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
    def _resolve_pai_vendor(self) -> Vendor | None:
        """The EXISTING import vendor record, or None. NEVER auto-creates: the
        vendor digit (Vendor.vendor_number) is owner-assigned, one digit per
        vendor, so a hard-coded default would mint colliding SKUs in the JAKS
        namespace (R1-16). full_import fails cleanly when this returns None or
        the record has no digit set."""
        return self.db.query(Vendor).filter(
            (Vendor.vendor_code == _PAI_VENDOR_CODE) | (Vendor.name == _PAI_VENDOR_NAME)
        ).first()

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

    def _sku_cat_code(self, cat_id: int | None, fallback_name: str, cache: dict) -> str:
        """Category code for the SKU: the node's explicit ``code`` if set, else
        derived from its name. Falls back to the Shopify Type name when there is no
        resolved category yet (e.g. dry-run, before categories are created)."""
        if cat_id is not None:
            if cat_id not in cache:
                c = self.db.get(ProductCategory, cat_id)
                code = (c.code or "").strip().upper() if c else ""
                cache[cat_id] = code or derive_category_code(c.name if c else fallback_name)
            return cache[cat_id]
        return derive_category_code(fallback_name)

    def _sku_to_id_map(self) -> dict:
        """Map BOTH the JAKS scheme SKU and the parked original number
        (ProductVendorSource.vendor_sku) → product id, so Pricing-Update CSVs match
        whether they carry the new SKU or the old JAKS-PAI-… number."""
        m: dict[str, int] = {}
        for pid, psku in self.db.query(Product.id, Product.sku).all():
            if psku:
                m[_norm(psku)] = pid
        for prod_id, vsku in self.db.query(
            ProductVendorSource.product_id, ProductVendorSource.vendor_sku
        ).all():
            if vsku:
                m.setdefault(_norm(vsku), prod_id)
        return m
