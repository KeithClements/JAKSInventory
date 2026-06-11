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

# Feed SKUs carry their vendor as a prefix per SCRAPER_REQUIREMENTS.md:
# JAKS-PAI-<part#> / JAKS-IMB-<part#> → vendor_code PAI / IMB. One export file
# may mix vendors; "Our Cost" always means THAT vendor's dealer price.
_VENDOR_PREFIX_RE = re.compile(r"^JAKS-([A-Z0-9]{2,10})-", re.IGNORECASE)


def _vendor_code_from_sku(sku: str) -> str | None:
    """'JAKS-IMB-1832665' → 'IMB'; None when the SKU has no JAKS-<CODE>- prefix."""
    m = _VENDOR_PREFIX_RE.match((sku or "").strip())
    return m.group(1).upper() if m else None


# §18.2 — Brand = the PARTS brand (the seeded Brand rows), not the vendor's
# legal name. Vendor codes map to their brand label; unmapped codes fall back
# to the vendor record's name.
_BRAND_BY_VENDOR_CODE: dict[str, str] = {
    "PAI": "PAI",
    "IMB": "Interstate-McBee",
}

# SKU match aliases (header keys are lowercased before lookup)
_SKU_KEYS = ("jaks_sku", "sku", "variant sku", "internal_sku", "part_number", "jaks_part")
# True PAI cost columns (Pricing-Update pai_cost mode) — NOT "variant price" (that's sell)
_COST_KEYS = ("pai_cost", "vendor_cost", "dealer_cost", "net_cost", "cost")

# ── R3: CSV column-mapping (saved per-vendor templates) ───────────────────────
# Canonical fields a mapping can target — derived from the intermediate row dict
# that full_import() and ImportReviewService._analyze_row() actually consume
# (sku/title/type/tags/price/compare_at/cost/barcode/grams/status/pai_part/oem/
# apps/images/warranty_months/core_charge/is_reman/unit_of_measure/pack_qty).
# (key, label, hint) — key is what mapping_json stores; label/hint feed the UI.
CANONICAL_IMPORT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("sku",                "SKU / Part Number",   "REQUIRED — the vendor's part number (dedup key)"),
    ("title",              "Title",               "Product name / description line"),
    ("category",           "Category / Type",     "Maps to the product category"),
    ("engine_make",        "Engine Make",         "e.g. CUMMINS — feeds classification + applications"),
    ("engine_models",      "Engine Model(s)",     "Pipe/comma-separated list, e.g. ISX|N14"),
    ("price",              "Sell Price",          "OUR sell price → price_override (never cost)"),
    ("compare_at_price",   "Compare-At Price",    "Marketing compare-at / MSRP"),
    ("cost",               "Vendor Cost",         "True vendor cost → vendor source (never COGS)"),
    ("core_charge",        "Core Charge",         "Dollar amount; sets has_core when > 0"),
    ("barcode",            "Barcode / UPC",       ""),
    ("weight_lbs",         "Weight (lbs)",        "Pounds — converted internally"),
    ("weight_grams",       "Weight (grams)",      "Grams, Shopify-style"),
    ("status",             "Status",              "active / draft — defaults to active"),
    ("vendor_part_number", "Vendor Part #",       "Defaults to the SKU when unmapped"),
    ("oem_refs",           "OEM References",      "Pipe/comma list, 'BRAND:NUM' or 'BRAND NUM'"),
    ("image_urls",         "Image URL(s)",        "Pipe-separated list of image URLs"),
    ("warranty_years",     "Warranty (years)",    "Converted to months"),
    ("warranty_months",    "Warranty (months)",   "Wins over years when both are mapped"),
    ("is_reman",           "Is Reman",            "1/true/yes — activates the core lifecycle"),
    ("unit_of_measure",    "Unit of Measure",     "Defaults to EA"),
    ("pack_qty",           "Pack Qty",            "Defaults to 1"),
)
CANONICAL_FIELD_KEYS = {k for k, _, _ in CANONICAL_IMPORT_FIELDS}

# Fuzzy header-name guesses: normalized header → canonical field. Used only to
# PRE-FILL the mapping screen; the user always confirms before anything parses.
_HEADER_GUESSES: dict[str, str] = {
    # sku / part number
    "sku": "sku", "variant sku": "sku", "part number": "sku", "part no": "sku",
    "part": "sku", "partnumber": "sku", "item number": "sku", "item no": "sku",
    "item": "sku", "pai part no": "sku", "mfr part number": "sku", "jaks sku": "sku",
    # title
    "title": "title", "name": "title", "product name": "title",
    "product title": "title", "description": "title", "desc": "title",
    # category
    "category": "category", "type": "category", "product type": "category",
    "product category": "category",
    # engine make / models
    "engine make": "engine_make", "make": "engine_make", "manufacturer": "engine_make",
    "engine": "engine_make",
    "engine model": "engine_models", "engine models": "engine_models",
    "model": "engine_models", "models": "engine_models", "application": "engine_models",
    "applications": "engine_models",
    # money
    "price": "price", "sell price": "price", "selling price": "price",
    "retail price": "price", "retail": "price", "list price": "price",
    "variant price": "price", "your price": "price",
    "msrp": "compare_at_price", "compare at price": "compare_at_price",
    "variant compare at price": "compare_at_price",
    "cost": "cost", "dealer cost": "cost", "net cost": "cost", "net price": "cost",
    "vendor cost": "cost", "wholesale": "cost", "wholesale price": "cost",
    "core": "core_charge", "core charge": "core_charge", "core price": "core_charge",
    # physical
    "barcode": "barcode", "upc": "barcode", "ean": "barcode",
    "weight": "weight_lbs", "weight lbs": "weight_lbs", "lbs": "weight_lbs",
    "wt": "weight_lbs", "weight grams": "weight_grams", "grams": "weight_grams",
    "variant grams": "weight_grams",
    # misc
    "status": "status",
    "vendor part number": "vendor_part_number", "vendor part": "vendor_part_number",
    "mfg part number": "vendor_part_number",
    "oem": "oem_refs", "oem refs": "oem_refs", "oem references": "oem_refs",
    "cross reference": "oem_refs", "cross references": "oem_refs",
    "interchange": "oem_refs",
    "image": "image_urls", "images": "image_urls", "image src": "image_urls",
    "image url": "image_urls", "image urls": "image_urls", "photo": "image_urls",
    "warranty years": "warranty_years", "warranty": "warranty_years",
    "warranty months": "warranty_months",
    "is reman": "is_reman", "reman": "is_reman",
    "uom": "unit_of_measure", "unit": "unit_of_measure",
    "unit of measure": "unit_of_measure",
    "pack qty": "pack_qty", "pack": "pack_qty", "qty per pack": "pack_qty",
}


def _norm_header(h: str) -> str:
    """Normalize a CSV header for fuzzy matching: lowercase, non-alnum → space."""
    return re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).strip()


def _multi(s: str) -> list[str]:
    """Split a pipe/semicolon/comma-separated multi-value cell."""
    return [x.strip() for x in re.split(r"[|;,]", s or "") if x.strip()]


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
                    # Optional scraper columns (SCRAPER_REQUIREMENTS.md) — absent
                    # on older exports; downstream treats blank as "don't touch".
                    "cost": _get(row, "our cost", "pai_cost", "vendor_cost",
                                 "dealer_cost", "net_cost", "cost"),
                    "manufacturer": _get(row, "manufacturer", "engine make",
                                         "engine_make", "engine_manufacturer"),
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

    # ══ R3: generic mapped-CSV support (custom vendor feeds) ═══════════════════
    @staticmethod
    def csv_headers(text: str) -> list[str]:
        """The CSV's header row, as written in the file (csv-parsed, not split)."""
        reader = csv.reader(io.StringIO(text))
        first = next(reader, [])
        return [h.strip() for h in first if (h or "").strip()]

    @classmethod
    def detect_known_format(cls, text: str) -> str | None:
        """'jaks' / 'shopify' when the existing parsers RECOGNIZE the headers,
        else None (→ the upload flow routes to the column-mapping screen).

        Stricter than detect_format(): that one falls back to 'shopify' for
        anything non-JAKS, which silently imports a SAMPA/IMB-style feed SKU-less
        (no 'Variant SKU' column → every row dropped or staged empty)."""
        headers = {h.lower() for h in cls.csv_headers(text)}
        if "pai_part_no" in headers:
            return "jaks"
        if "variant sku" in headers and "handle" in headers:
            return "shopify"
        return None

    @staticmethod
    def guess_mapping(headers: list[str]) -> dict[str, str]:
        """Fuzzy header → canonical-field guesses to PRE-FILL the mapping screen.
        First header wins per canonical field (no double-targeting); unmatched
        headers are simply omitted (the UI shows them as 'ignore')."""
        out: dict[str, str] = {}
        taken: set[str] = set()
        for h in headers:
            field = _HEADER_GUESSES.get(_norm_header(h))
            if field and field not in taken:
                out[h] = field
                taken.add(field)
        return out

    def parse_mapped_csv(self, text: str, mapping: dict[str, str]) -> list[dict]:
        """Parse ANY one-row-per-product CSV through a user-confirmed column
        mapping {csv header → canonical field} into the SAME intermediate dict
        shape as parse_shopify_csv()/parse_jaks_export_csv(), so the staged-
        candidate pipeline (and every downstream guard — vendor digit, dedupe,
        DUPLICATE handling, category gating) applies unchanged.

        SKU is the required minimum mapping — raises ValueError without it."""
        clean = {str(h).strip(): f for h, f in (mapping or {}).items()
                 if f in CANONICAL_FIELD_KEYS and str(h).strip()}
        if "sku" not in set(clean.values()):
            raise ValueError("The mapping must assign a SKU / Part Number column "
                             "— refusing to import SKU-less rows.")
        reader = csv.DictReader(io.StringIO(text))
        rows: list[dict] = []
        for raw in reader:
            row = {_norm(k): (v or "").strip() for k, v in raw.items()}
            vals: dict[str, str] = {}
            for header, field in clean.items():
                v = row.get(_norm(header), "")
                if v and field not in vals:   # first non-empty column wins per field
                    vals[field] = v
            sku = (vals.get("sku") or "").strip()
            if not sku:
                continue

            # OEM refs: "BRAND:NUM" / "BRAND NUM" / bare number → "BRAND NUM"
            oem: list[str] = []
            for item in _multi(vals.get("oem_refs", "")):
                if ":" in item:
                    brand_str, _, num = item.partition(":")
                    if num.strip():
                        oem.append(f"{brand_str.strip()} {num.strip()}")
                else:
                    oem.append(item)

            # Applications: engine_make + model list → ["MAKE MODEL", ...]
            engine_make = vals.get("engine_make", "").strip().upper()
            apps: list[str] = []
            for model in _multi(vals.get("engine_models", "")):
                model = model.upper()
                apps.append(f"{engine_make} {model}".strip() if engine_make else model)
            if not apps and engine_make:
                apps.append(engine_make)

            title = vals.get("title", "").strip()
            images = [{"url": u, "alt": title}
                      for u in _multi(vals.get("image_urls", ""))]

            # Warranty: months wins when both are mapped; else years*12
            w_months = 0
            if vals.get("warranty_months"):
                try:
                    w_months = int(round(float(vals["warranty_months"])))
                except (TypeError, ValueError):
                    w_months = 0
            elif vals.get("warranty_years"):
                w_months = _years_to_months(vals["warranty_years"])

            # Weight → grams (full_import stores lbs via _grams_to_lbs)
            grams_raw = vals.get("weight_grams", "").strip()
            if not grams_raw and vals.get("weight_lbs"):
                lbs = _to_float(vals["weight_lbs"])
                grams_raw = str(round(lbs * 453.592, 1)) if lbs is not None else ""

            core_charge = _to_float(vals.get("core_charge")) or 0.0
            is_reman = (vals.get("is_reman", "0").strip().lower()
                        in ("1", "true", "yes", "y"))
            uom = (vals.get("unit_of_measure") or "EA").strip().upper() or "EA"
            try:
                pack_qty = max(1, int(float(vals.get("pack_qty") or 1)))
            except (TypeError, ValueError):
                pack_qty = 1

            rows.append({
                "sku":             sku,                  # vendor part # → dedup key
                "title":           title,
                "type":            vals.get("category", "").strip(),
                "tags":            engine_make,
                "price":           vals.get("price", "").strip(),          # SELL price
                "compare_at":      vals.get("compare_at_price", "").strip(),
                "cost":            vals.get("cost", "").strip(),           # vendor cost
                "barcode":         vals.get("barcode", "").strip(),
                "grams":           grams_raw,
                "status":          vals.get("status", "active").strip() or "active",
                "pai_part":        vals.get("vendor_part_number", "").strip() or sku,
                "oem":             oem,
                "apps":            apps,
                "images":          images,
                "warranty_months": w_months,
                "handle":          sku.lower(),
                "core_charge":     core_charge,
                "is_reman":        is_reman,
                "unit_of_measure": uom,
                "pack_qty":        pack_qty,
            })
        return rows

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
        # each row's vendor comes from its feed-SKU prefix (JAKS-PAI-… /
        # JAKS-IMB-…; no prefix = legacy PAI) and the vendor digit from the
        # owner-set Vendor.vendor_number — never auto-created or defaulted, or
        # a feed would mint SKUs in another vendor's namespace. EVERY vendor
        # the feed references must exist with its digit, else fail cleanly and
        # write nothing (atomic — a half-imported mixed feed is worse).
        feed_codes = {(_vendor_code_from_sku(p.get("sku", "")) or _PAI_VENDOR_CODE)
                      for p in rows if p.get("sku")}
        vendors_by_code: dict[str, Vendor] = {}
        problems: list[str] = []
        for code in sorted(feed_codes):
            q = self.db.query(Vendor).filter(func.upper(Vendor.vendor_code) == code)
            if code == _PAI_VENDOR_CODE:   # legacy PAI rows may match by name
                q = self.db.query(Vendor).filter(
                    (func.upper(Vendor.vendor_code) == code)
                    | (Vendor.name == _PAI_VENDOR_NAME))
            v = q.first()
            label = _PAI_VENDOR_NAME if code == _PAI_VENDOR_CODE else (
                v.name if v else code)
            digit = (v.vendor_number or "").strip() if v else ""
            if not digit:
                problems.append(
                    f"'{label}' " + ("has no vendor digit (SKU #) set" if v
                                     else "does not exist"))
            else:
                vendors_by_code[code] = v
        if problems:
            summary["error"] = (
                "Import aborted: vendor " + "; vendor ".join(problems) + ". "
                "Create the vendor(s) with their vendor digit first "
                "(Inventory → Vendors → SKU #), then re-run the import."
            )
            return summary
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

            # Per-row vendor: the feed-SKU prefix names it (no prefix = PAI).
            row_code = _vendor_code_from_sku(sku) or _PAI_VENDOR_CODE
            row_vendor = vendors_by_code[row_code]
            row_digit = (row_vendor.vendor_number or "").strip()

            cat_id = self._resolve_category(p["type"], cat_cache, summary, dry_run)
            # §18.6 — refine below the Shopify-Type category + derive engine make.
            cls = classifier.classify(
                title=p["title"], tags=p.get("tags", ""),
                app_makes=[_split_two(a)[0] for a in p["apps"]],
                app_models=[_split_two(a)[1] for a in p["apps"]],
            )
            # JAKS SKU scheme — mint the customer-facing SKU now; the raw CSV SKU is
            # parked on the vendor source (vendor_sku, below) so it stays searchable.
            # The (engine, category) sequence is SHARED across vendors by design —
            # the same part from a 2nd vendor reads 90001 ↔ 30001 (twin rule).
            ecode = _engine_code(cls["engine_model"] or "")
            ccode = self._sku_cat_code(cls["category_id"] or cat_id, p["type"], cat_code_cache)
            new_seq = seq_counters.get((ecode, ccode), 0) + 1
            seq_counters[(ecode, ccode)] = new_seq
            new_sku = assemble_sku(ccode, row_digit, new_seq, engine_code=ecode)
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
                # §18.2 — Brand = the parts brand (seeded Brand rows: PAI /
                # Interstate-McBee / …), keyed off the row's vendor.
                brand=_BRAND_BY_VENDOR_CODE.get(row_code, row_vendor.name),
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
                enrichment_source=(f"{row_code} scraper (JAKS export)"
                                   if p.get("core_charge") is not None
                                   else f"{row_code} scraper (Shopify export)"),
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

            self.db.add(ProductVendorSource(
                product_id=product.id, vendor_id=row_vendor.id,
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
                    engine_model=model, source=row_code,
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

    # ══ Mode 2c: PRICING UPDATE — sell + cost + manufacturer (scraper refresh) ═
    def pricing_update_sell(
        self,
        text: str,
        *,
        dry_run: bool = True,
        max_change_pct: float | None = None,
    ) -> dict:
        """
        Refresh sell price, compare-at, OUR cost, and manufacturer on EXISTING
        products from the scraper's standard Shopify export. NEVER creates
        products, NEVER touches competitor prices.

        Columns consumed (each independent — absent column = field not touched):
          • Variant SKU           → dedup key (required)
          • Variant Price         → product.price_override
          • Variant Compare At    → product.compare_at_price
          • pai_cost / Our Cost / vendor_cost / cost
                                  → vendor_cost (+ ProductCostHistory row) on
                                    the source belonging to the vendor named by
                                    the SKU prefix (JAKS-PAI-… → PAI,
                                    JAKS-IMB-… → IMB; no prefix = legacy PAI).
                                    Rows whose product lacks that vendor's
                                    source land in skipped_no_vendor_source
                                    (+ a per-vendor labeled count)
          • Manufacturer          → product.manufacturer, normalized
                                    case-insensitively against the canonical
                                    MANUFACTURERS list; unmapped values pass
                                    through verbatim and are counted in
                                    manufacturer_unmapped_sample for review.

        Shopify image-only rows (blank SKU + blank everything) are silently
        skipped as image_rows_skipped, not counted against skipped_no_sku.

        max_change_pct (optional) caps per-product sell price moves — any
        product whose new price would move more than that % from its current
        price_override is surfaced into over_threshold_sample and SKIPPED for
        the sell + compare changes (cost / manufacturer still apply, since
        they're independent fields and a bad scrape of one need not poison the
        others).
        """
        from app.routers.products import MANUFACTURERS as _MFG_CANON
        reader = list(csv.DictReader(io.StringIO(text)))
        summary = {
            "mode": "pricing_update", "source": "sell", "dry_run": dry_run,
            "rows": len(reader), "image_rows_skipped": 0,
            "skipped_no_sku": 0, "skipped_no_product": 0, "skipped_no_price": 0,
            "matched": 0, "prices_updated": 0, "compare_updated": 0,
            "unchanged": 0, "over_threshold_skipped": 0,
            "costs_updated": 0, "skipped_no_vendor_source": 0,
            "manufacturer_updated": 0, "manufacturer_unmapped_sample": [],
            "sample": [], "over_threshold_sample": [],
        }

        def _count_no_source(code: str | None) -> None:
            """Aggregate + per-vendor labeled key so the owner can tell PAI
            gaps from IMB gaps in the result panel."""
            summary["skipped_no_vendor_source"] += 1
            label = f"skipped_no_{(code or _PAI_VENDOR_CODE).lower()}_source"
            summary[label] = summary.get(label, 0) + 1
        sku_to_id = self._sku_to_id_map()
        # Multi-vendor feed (SCRAPER_REQUIREMENTS.md): the Variant SKU prefix
        # names the vendor (JAKS-PAI-… / JAKS-IMB-…) and "Our Cost" is THAT
        # vendor's dealer price. Vendors resolve lazily, cached per code; a
        # prefix with NO matching vendor record is never silently re-routed to
        # another vendor — those rows land in a per-vendor skipped count.
        _vendor_cache: dict[str, Vendor | None] = {}

        def _vendor_for(code: str | None) -> Vendor | None:
            if code is None:
                # No JAKS-<CODE>- prefix → legacy single-vendor feeds are PAI.
                code = _PAI_VENDOR_CODE
            code = code.upper()
            if code not in _vendor_cache:
                q = self.db.query(Vendor).filter(func.upper(Vendor.vendor_code) == code)
                if code == _PAI_VENDOR_CODE:   # legacy PAI rows may match by name
                    q = self.db.query(Vendor).filter(
                        (func.upper(Vendor.vendor_code) == code)
                        | (Vendor.name == _PAI_VENDOR_NAME))
                _vendor_cache[code] = q.first()
            return _vendor_cache[code]
        _PRICE_KEYS = ("variant price", "price", "sell_price", "sell price",
                       "selling_price", "retail_price", "retail price")
        _COMPARE_KEYS = ("variant compare at price", "compare_at_price",
                         "compare at price", "msrp", "compare_at")
        _OUR_COST_KEYS = ("our cost", "our_cost") + _COST_KEYS  # adds our cost
        _MFG_KEYS = ("manufacturer", "engine make", "engine_make",
                     "engine_manufacturer", "make")
        # Case-insensitive canonical mapping: "CUMMINS" / "cummins" → "Cummins"
        _mfg_lookup = {m.lower(): m for m in _MFG_CANON}
        seen_handles: set[str] = set()
        for raw in reader:
            row = {_norm(k): v for k, v in raw.items()}
            sku = _get(row, *_SKU_KEYS)
            price_raw = _get(row, *_PRICE_KEYS)
            compare_raw = _get(row, *_COMPARE_KEYS)
            cost_raw = _get(row, *_OUR_COST_KEYS)
            mfg_raw = _get(row, *_MFG_KEYS)

            # Shopify image-only rows: SKU blank AND no useful columns. The
            # Handle column links them to the parent — count, never warn.
            if not sku and not price_raw and not compare_raw \
                    and not cost_raw and not mfg_raw:
                summary["image_rows_skipped"] += 1
                continue
            if not sku:
                summary["skipped_no_sku"] += 1
                continue
            pid = sku_to_id.get(_norm(sku))
            if pid is None:
                summary["skipped_no_product"] += 1
                continue
            # Same-SKU repeats (rare — Shopify variant rows): act once.
            if sku in seen_handles:
                continue
            seen_handles.add(sku)

            new_price = _to_float(price_raw)
            new_compare = _to_float(compare_raw)
            new_cost = _to_float(cost_raw)
            # Manufacturer: trim, then canonicalize case-insensitively.
            mfg_in = (mfg_raw or "").strip()
            new_mfg = _mfg_lookup.get(mfg_in.lower()) if mfg_in else None
            mfg_unmapped = bool(mfg_in) and new_mfg is None
            if mfg_unmapped:
                # Pass through verbatim — frees us to handle "Detroit" / "DDC"
                # etc. without losing the data; surface for review.
                new_mfg = mfg_in

            if (new_price is None and new_compare is None
                    and new_cost is None and not mfg_in):
                summary["skipped_no_price"] += 1
                continue
            summary["matched"] += 1

            product = self.db.get(Product, pid)
            if product is None:
                summary["skipped_no_product"] += 1
                continue

            old_price = product.price_override
            old_compare = product.compare_at_price
            old_mfg = product.manufacturer

            price_change = (new_price is not None
                            and (old_price is None
                                 or abs((old_price or 0.0) - new_price) >= 0.005))
            compare_change = (new_compare is not None
                              and (old_compare is None
                                   or abs((old_compare or 0.0) - new_compare) >= 0.005))
            mfg_change = (new_mfg is not None
                          and (old_mfg or "").strip() != new_mfg)

            # Threshold rail — applies only to price_override moves where
            # there IS a prior price to compare against. Cost & manufacturer
            # are independent: a bad scrape of one shouldn't suppress the
            # others, so the gate only skips the price/compare writes.
            over_threshold = False
            if (price_change and max_change_pct is not None
                    and old_price is not None and old_price > 0):
                pct = abs(new_price - old_price) / old_price * 100.0
                if pct > max_change_pct:
                    over_threshold = True
                    summary["over_threshold_skipped"] += 1
                    if len(summary["over_threshold_sample"]) < 10:
                        summary["over_threshold_sample"].append({
                            "sku": sku, "old_price": old_price,
                            "new_price": new_price, "change_pct": round(pct, 1),
                        })
                    price_change = False
                    compare_change = False  # paired with price for sample-row sanity

            # Cost change check — the SKU prefix names the vendor whose dealer
            # price "Our Cost" is; the write targets THAT vendor's source.
            cost_src = None
            cost_vendor = None
            cost_change = False
            old_cost_val = None
            if new_cost is not None and new_cost > 0:
                vcode = _vendor_code_from_sku(sku)
                cost_vendor = _vendor_for(vcode)
                if cost_vendor is None:
                    _count_no_source(vcode)
                else:
                    cost_src = self.db.query(ProductVendorSource).filter(
                        ProductVendorSource.product_id == pid,
                        ProductVendorSource.vendor_id == cost_vendor.id,
                    ).first()
                    if cost_src is None:
                        _count_no_source(vcode)
                    else:
                        old_cost_val = cost_src.vendor_cost or 0.0
                        if abs(old_cost_val - new_cost) >= 0.005:
                            cost_change = True

            if (not price_change and not compare_change
                    and not mfg_change and not cost_change):
                # If only an over-threshold row landed here, we already counted it.
                if not over_threshold:
                    summary["unchanged"] += 1
                continue

            if price_change:
                summary["prices_updated"] += 1
            if compare_change:
                summary["compare_updated"] += 1
            if cost_change:
                summary["costs_updated"] += 1
            if mfg_change:
                summary["manufacturer_updated"] += 1
                if mfg_unmapped and len(summary["manufacturer_unmapped_sample"]) < 10:
                    summary["manufacturer_unmapped_sample"].append({
                        "sku": sku, "manufacturer": new_mfg,
                    })
            if len(summary["sample"]) < 10:
                summary["sample"].append({
                    "sku": sku,
                    "old_price": old_price, "new_price": new_price,
                    "old_compare": old_compare, "new_compare": new_compare,
                    "old_cost": old_cost_val, "new_cost": new_cost if cost_change else None,
                    "old_mfg": old_mfg, "new_mfg": new_mfg if mfg_change else None,
                })

            if not dry_run:
                if price_change:
                    product.price_override = new_price
                if compare_change:
                    product.compare_at_price = new_compare
                if mfg_change:
                    product.manufacturer = new_mfg
                if cost_change and cost_src is not None:
                    cost_src.vendor_cost = new_cost
                    cost_src.last_cost_updated_at = datetime.utcnow()
                    self.db.add(ProductCostHistory(
                        product_id=pid, vendor_id=cost_vendor.id,
                        old_cost=old_cost_val, new_cost=new_cost,
                        changed_by_id=self.current_user_id,
                        notes=f"Scraper refresh ({cost_vendor.vendor_code or cost_vendor.name} cost)",
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
