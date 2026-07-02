"""
app/services/enrichment_service.py
==================================
Phase 2 §7.2 — product enrichment sync from the owner's EXTERNAL catalog tool.

The scraper/cataloging lives entirely outside the ERP now (P2-D9). Its output is
a CSV that this sync ingests to ENRICH already-existing products with:
  • cross_references  (OEM / competitor / vendor-alt part numbers)
  • product_applications  (engine make/model + CPL, optional ESN range)

HARD INVARIANTS (enrich-only):
  • Match jaks_sku → products.sku. A row with no matching product is SKIPPED.
  • NEVER creates a product.
  • NEVER touches cost / selling_price / any pricing or product field — it only
    INSERTS cross_reference / product_application rows (idempotent: existing
    rows are left untouched).

CSV contract (headers case-insensitive; aliases accepted):
  match:   jaks_sku | sku | internal_sku | part_number
  x-refs:  cross_ref(+cross_ref_type) | oem_number | competitor_number |
           vendor_part   (+ optional brand)
  apps:    engine_make|make , engine_model|model , cpl , esn_range|esn

API: enrich_from_rows(list[dict]) is the core (testable); enrich_from_csv_text /
enrich_from_csv_path are thin convenience wrappers.
"""
from __future__ import annotations

import csv
import io

from app.constants import CrossRefType
from app.models.product import Product, CrossReference, ProductApplication
from app.services.base import BaseService


_SKU_KEYS = ("jaks_sku", "sku", "internal_sku", "part_number", "jaks_part")
_REF_TYPE_MAP = {
    "oem": CrossRefType.OEM,
    "competitor": CrossRefType.COMPETITOR,
    "comp": CrossRefType.COMPETITOR,
    "vendor_alt": CrossRefType.VENDOR_ALT,
    "vendor": CrossRefType.VENDOR_ALT,
    "alt": CrossRefType.VENDOR_ALT,
}


def _norm(s) -> str:
    return str(s or "").strip().lower()


def _get(row: dict, *keys: str) -> str:
    """First non-empty value among the alias keys (row keys pre-lowercased)."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


class ProductEnrichmentService(BaseService):
    """§7.2 enrich-only catalog sync. Read products, write cross_refs + apps."""

    def enrich_from_csv_text(self, text: str) -> dict:
        reader = csv.DictReader(io.StringIO(text))
        return self.enrich_from_rows(list(reader))

    def enrich_from_csv_path(self, path: str) -> dict:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return self.enrich_from_rows(list(csv.DictReader(fh)))

    def enrich_from_rows(self, rows: list[dict]) -> dict:
        summary = {
            "rows": 0, "matched": 0, "skipped_no_sku": 0, "skipped_no_product": 0,
            "cross_refs_added": 0, "cross_refs_existing": 0,
            "applications_added": 0, "applications_existing": 0,
        }
        if not rows:
            return summary

        # sku(lower) → product_id, in one query (never load to mutate the product)
        sku_to_id = {
            _norm(sku): pid
            for pid, sku in self.db.query(Product.id, Product.sku).all()
        }
        if not sku_to_id:
            summary["rows"] = len(rows)
            summary["skipped_no_product"] = len(rows)
            return summary

        # Existing-row sets for idempotent upsert (dedups against the DB AND within
        # the same CSV, since we add to the sets as we insert).
        seen_xref = {
            (pid, rt, _norm(rn))
            for pid, rt, rn in self.db.query(
                CrossReference.product_id, CrossReference.ref_type, CrossReference.ref_number
            ).all()
        }
        seen_app = {
            (pid, _norm(mk), _norm(md), _norm(cpl))
            for pid, mk, md, cpl in self.db.query(
                ProductApplication.product_id, ProductApplication.engine_make,
                ProductApplication.engine_model, ProductApplication.cpl,
            ).all()
        }

        source = ""  # optional provenance tag if the CSV carries it
        for raw in rows:
            summary["rows"] += 1
            row = {_norm(k): v for k, v in raw.items()}
            sku = _get(row, *_SKU_KEYS)
            if not sku:
                summary["skipped_no_sku"] += 1
                continue
            pid = sku_to_id.get(_norm(sku))
            if pid is None:
                summary["skipped_no_product"] += 1   # NEVER create a product
                continue
            summary["matched"] += 1
            source = _get(row, "source") or source

            # ── cross-references ──────────────────────────────────────────────
            for ref_number, ref_type, brand in self._cross_refs_from_row(row):
                key = (pid, ref_type, _norm(ref_number))
                if key in seen_xref:
                    summary["cross_refs_existing"] += 1
                    continue
                from app.services.crossref_hygiene import is_garbage_ref
                if is_garbage_ref(ref_number):
                    summary["cross_refs_skipped_garbage"] = summary.get("cross_refs_skipped_garbage", 0) + 1
                    continue
                seen_xref.add(key)
                self.db.add(CrossReference(
                    product_id=pid, ref_type=ref_type, ref_number=ref_number,
                    brand=brand, status="proven",
                ))
                summary["cross_refs_added"] += 1

            # ── application (make/model + CPL) ────────────────────────────────
            make = _get(row, "engine_make", "make")
            model = _get(row, "engine_model", "model")
            cpl = _get(row, "cpl")
            if make or model:
                key = (pid, _norm(make), _norm(model), _norm(cpl))
                if key in seen_app:
                    summary["applications_existing"] += 1
                else:
                    seen_app.add(key)
                    self.db.add(ProductApplication(
                        product_id=pid, engine_make=make, engine_model=model, cpl=cpl,
                        esn_range=_get(row, "esn_range", "esn") or None,
                        source=_get(row, "source"),
                    ))
                    summary["applications_added"] += 1

        self.db.commit()
        return summary

    @staticmethod
    def _cross_refs_from_row(row: dict) -> list[tuple[str, str, str]]:
        """Extract (ref_number, ref_type, brand) tuples from a row — supports a
        generic cross_ref(+type) column and the typed oem/competitor/vendor cols."""
        brand = _get(row, "brand", "cross_ref_brand")
        out: list[tuple[str, str, str]] = []
        direct = _get(row, "cross_ref", "ref_number", "crossref")
        if direct:
            rt = _REF_TYPE_MAP.get(_norm(_get(row, "cross_ref_type", "ref_type")), CrossRefType.OEM)
            out.append((direct, rt, brand))
        oem = _get(row, "oem_number", "oem")
        if oem:
            out.append((oem, CrossRefType.OEM, brand))
        comp = _get(row, "competitor_number", "competitor")
        if comp:
            out.append((comp, CrossRefType.COMPETITOR, brand))
        valt = _get(row, "vendor_part", "vendor_number", "vendor_alt")
        if valt:
            out.append((valt, CrossRefType.VENDOR_ALT, brand))
        return out
