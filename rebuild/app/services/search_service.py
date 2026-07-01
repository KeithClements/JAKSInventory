"""
app/services/search_service.py
================================
Global search — the quote screen's live search bar hits this.

Search priority (from quoting_requirements.md):
  1. JAKS part number (exact prefix match)
  2. OEM / cross-reference numbers (CrossReference table)
  3. Competitor part numbers (CrossReference table, ref_type='competitor';
     plus CompetitorPrice.competitor_part_number — R2)
  4. Description keyword (LIKE search, lower priority)
  5. Vendor part numbers (ProductVendorSource.vendor_part_number)

Results always include: product_id, part_number, description,
  current_cost, suggested_sell, qty_on_hand, vendor_name, status.

Caller (QuoteService) decides what to do with results.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import or_

from sqlalchemy import desc as sa_desc
from sqlalchemy import func as sa_func

from app.models.competitor import CompetitorPrice
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import (
    CrossReference,
    Product,
    ProductApplication,
    ProductCategory,
    ProductVendorSource,
)
from app.models.purchase_order import PurchaseOrder
from app.models.quote import Quote, SalesOrder
from app.models.vendor import Vendor
from app.services.base import BaseService
from app.utils import calc_sell_price, normalize_part

# Separators stripped from BOTH the query and the column so "OK-1" matches a
# SKU/cross-ref stored as "OK1", "ok 1", "OK.1", etc.  This is the ONE place the
# app normalizes part numbers — it supersedes the per-endpoint de-dash patches
# once screens move to /line-items/product-search.
# R2: also strips ( ) + # % so the column side agrees with the query side
# (utils.normalize_part strips ALL non-alphanumerics) on the common cases —
# a stored "3683512(C)" must match the query "3683512C".
_NORM_SEPARATORS = ("-", " ", ".", "/", "_", "(", ")", "+", "#", "%")


def _norm_col(col):
    """SQL expression: lower-case `col` and strip common part-number separators,
    so it can be compared against utils.normalize_part(query)."""
    expr = sa_func.lower(col)
    for _sep in _NORM_SEPARATORS:
        expr = sa_func.replace(expr, _sep, "")
    return expr


@dataclass
class ProductSearchResult:
    product_id: int
    part_number: str
    description: str
    current_cost: float
    suggested_sell: float
    qty_on_hand: int
    vendor_name: str | None
    status: str
    match_type: str          # part_number | barcode | cross_ref | vendor_sku | competitor | engine_app | description
    cross_ref_number: str | None = None
    last_sold_price: float | None = None   # most recent invoice line price for this product
    last_sold_date: str | None = None      # formatted date of that sale (MM/DD/YY)


class SearchService(BaseService):
    """
    Product lookup used by the quote / invoice / SO screens.
    Returns ranked results — exact matches first, fuzzy last.
    """

    def search_products(
        self,
        query: str,
        limit: int = 25,
        include_inactive: bool = False,
    ) -> list[ProductSearchResult]:
        """
        Multi-strategy search across part numbers, cross references,
        descriptions, and vendor SKUs.
        Returns results sorted: exact > prefix > cross-ref > description.
        """
        q = query.strip()
        if not q:
            return []

        from app.services.pricing_service import PricingService
        _pricing = PricingService(self.db)

        seen: set[int] = set()
        results: list[ProductSearchResult] = []

        def _to_result(product: Product, match_type: str, cross_ref_number: str | None = None) -> ProductSearchResult:
            src = next((s for s in product.vendor_sources if s.is_preferred), None)
            # O5 — settings-backed default markup (0% is a real value; only NULL
            # falls through to the default_markup_pct setting).
            sell = _pricing.sell_price_for(product)

            # Last sold price — most recent finalised invoice line for this product.
            # N+1 is acceptable: max 8 results, local single-user app.
            last_row = (
                self.db.query(InvoiceLine.unit_price, Invoice.created_at)
                .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
                .filter(InvoiceLine.product_id == product.id)
                .order_by(sa_desc(Invoice.created_at))
                .first()
            )
            last_sold_price = float(last_row[0]) if last_row else None
            last_sold_date  = last_row[1].strftime("%m/%d/%y") if last_row else None

            return ProductSearchResult(
                product_id=product.id,
                part_number=product.sku,
                description=product.title,
                current_cost=product.cost,
                suggested_sell=sell,
                qty_on_hand=product.qty_on_hand,
                vendor_name=src.vendor.name if src and src.vendor else None,
                status=product.status,
                match_type=match_type,
                cross_ref_number=cross_ref_number,
                last_sold_price=last_sold_price,
                last_sold_date=last_sold_date,
            )

        base_q = self.db.query(Product)
        if not include_inactive:
            base_q = base_q.filter(Product.is_active == True)  # noqa: E712

        # Normalized query — separators stripped, lower-cased ("OK-1" == "ok1").
        # One normalization for SKU, cross-ref and vendor SKU (this supersedes
        # the earlier SKU-only de-dash patch).
        nq = normalize_part(q)

        if nq:
            # §21 — use the precomputed, INDEXED sku_norm column (kept in sync by
            # the Product before_insert/update listener + backfilled on startup by
            # search_index.ensure_search_norm_columns) instead of normalizing every
            # row with a SQL function on each keystroke. Same pattern as the
            # vendor_part_number_norm / ref_number_norm columns used below.
            sku_norm = Product.sku_norm

            # 1. Exact SKU match (normalized)
            exact = base_q.filter(sku_norm == nq).first()
            if exact and exact.id not in seen:
                seen.add(exact.id)
                results.append(_to_result(exact, "part_number"))

            # 1b. Barcode / UPC exact match (normalized) — a counter scanner
            #     outputs the bare barcode; an exact hit on the dedicated barcode
            #     field is an unambiguous "this exact part" signal, so it ranks
            #     right behind an exact SKU. (The Scan button focuses the search
            #     box; a scanned code resolves here.)
            if len(results) < limit:
                bc_norm = _norm_col(Product.barcode)
                bc_hit = base_q.filter(
                    Product.barcode.isnot(None), Product.barcode != "", bc_norm == nq
                ).first()
                if bc_hit and bc_hit.id not in seen:
                    seen.add(bc_hit.id)
                    results.append(_to_result(bc_hit, "barcode"))

            # 2. SKU contains (normalized), prefix matches ranked first —
            #    "141" finds "14-1234"; "ok1" finds "OK-1".
            if len(results) < limit:
                sku_hits = (
                    base_q.filter(sku_norm.like(f"%{nq}%"))
                    .order_by(sku_norm.like(f"{nq}%").desc(), Product.sku)
                    .limit(limit)
                    .all()
                )
                for p in sku_hits:
                    if p.id not in seen:
                        seen.add(p.id)
                        results.append(_to_result(p, "part_number"))
                    if len(results) >= limit:
                        break

            # 3. Cross-reference lookup (OEM + competitor + vendor_alt), normalized
            if len(results) < limit:
                xref_hits = (
                    self.db.query(CrossReference, Product)
                    .join(Product, CrossReference.product_id == Product.id)
                    .filter(CrossReference.ref_number_norm.like(f"%{nq}%"))
                    .filter(Product.is_active == True if not include_inactive else True)  # noqa: E712
                    .limit(limit)
                    .all()
                )
                for xref, p in xref_hits:
                    if p.id not in seen:
                        seen.add(p.id)
                        results.append(_to_result(p, "cross_ref", cross_ref_number=xref.ref_number))
                    if len(results) >= limit:
                        break

            # 4. Vendor SKU / part number (normalized)
            if len(results) < limit:
                pvs_hits = (
                    self.db.query(ProductVendorSource, Product)
                    .join(Product, ProductVendorSource.product_id == Product.id)
                    .filter(
                        or_(
                            ProductVendorSource.vendor_part_number_norm.like(f"%{nq}%"),
                            ProductVendorSource.vendor_sku_norm.like(f"%{nq}%"),
                        )
                    )
                    .filter(ProductVendorSource.is_active == True)  # noqa: E712
                    .limit(limit)
                    .all()
                )
                for _src, p in pvs_hits:
                    if p.id not in seen:
                        seen.add(p.id)
                        results.append(_to_result(p, "vendor_sku"))
                    if len(results) >= limit:
                        break

            # 5. Competitor part number (CompetitorPrice table), normalized —
            #    R2: a customer calling with an HHP/ATL/IMB number finds our part
            #    even when no CrossReference row exists yet. Surfaces the matched
            #    number the same way cross-ref matches do (cross_ref_number).
            #    §23.3 Phase 1 #2 — reads the precomputed, INDEXED
            #    competitor_part_number_norm column (kept in sync by the
            #    CompetitorPrice before_insert/update listener + backfilled on
            #    startup by search_index.ensure_search_norm_columns) instead of
            #    normalizing every row with a SQL function on each keystroke —
            #    the same fix already applied to sku_norm / ref_number_norm /
            #    vendor_*_norm above.
            if len(results) < limit:
                comp_hits = (
                    self.db.query(CompetitorPrice, Product)
                    .join(Product, CompetitorPrice.product_id == Product.id)
                    .filter(CompetitorPrice.competitor_part_number_norm.like(f"%{nq}%"))
                    .filter(CompetitorPrice.is_active == True)  # noqa: E712
                    .filter(Product.is_active == True if not include_inactive else True)  # noqa: E712
                    .limit(limit)
                    .all()
                )
                for cp, p in comp_hits:
                    if p.id not in seen:
                        seen.add(p.id)
                        results.append(_to_result(p, "competitor", cross_ref_number=cp.competitor_part_number))
                    if len(results) >= limit:
                        break

        # 5c. Engine application (ProductApplication) — "Cummins ISX", "ISX",
        #     "DD15" etc. find every part that fits that engine. Uses the RAW
        #     query (spaces kept) against make / model / "make model". Populated
        #     from the PAI feed but was never queried before. Ranked below part-
        #     number matches so a real part # always wins.
        if q and len(results) < limit:
            make_model = sa_func.lower(
                ProductApplication.engine_make + " " + ProductApplication.engine_model
            )
            ql = q.lower()
            app_hits = (
                self.db.query(ProductApplication, Product)
                .join(Product, ProductApplication.product_id == Product.id)
                .filter(
                    or_(
                        sa_func.lower(ProductApplication.engine_make).like(f"%{ql}%"),
                        sa_func.lower(ProductApplication.engine_model).like(f"%{ql}%"),
                        make_model.like(f"%{ql}%"),
                    )
                )
                .filter(Product.is_active == True if not include_inactive else True)  # noqa: E712
                .limit(limit)
                .all()
            )
            for app_row, p in app_hits:
                if p.id not in seen:
                    seen.add(p.id)
                    label = " ".join(
                        x for x in (app_row.engine_make, app_row.engine_model) if x
                    ).strip()
                    results.append(_to_result(p, "engine_app", cross_ref_number=label or None))
                if len(results) >= limit:
                    break

        # 6. Keyword search (lowest priority — raw prose, not part-normalized).
        #    MULTI-TERM: the query is split on whitespace and EVERY token must
        #    match somewhere across the human-readable fields — title,
        #    description, manufacturer, engine make, brand and category name.
        #    This is why "stud cat" finds an "EXHAUST MANIFOLD STUD KIT" whose
        #    manufacturer is "Caterpillar": "stud" hits the title and "cat" hits
        #    the manufacturer, even though no single field contains the phrase
        #    "stud cat". A single-token query degrades to the old title/desc
        #    contains-search, just widened to the manufacturer/brand/category
        #    fields too. Ranked below every part-number / cross-ref / engine
        #    match so a real part number always wins.
        tokens = [t for t in q.split() if t]
        if len(results) < limit and tokens:
            kw_q = base_q.outerjoin(
                ProductCategory, Product.category_id == ProductCategory.id
            )
            for tok in tokens:
                like = f"%{tok}%"
                kw_q = kw_q.filter(
                    or_(
                        Product.title.ilike(like),
                        Product.description.ilike(like),
                        Product.manufacturer.ilike(like),
                        Product.engine_manufacturer.ilike(like),
                        Product.brand.ilike(like),
                        ProductCategory.name.ilike(like),
                    )
                )
            # Rank a contiguous phrase hit in the title first (so "exhaust
            # manifold stud" beats a product where those words are merely
            # scattered), then by title for a stable order.
            kw_hits = (
                kw_q.order_by(Product.title.ilike(f"%{q}%").desc(), Product.title)
                .limit(limit)
                .all()
            )
            for p in kw_hits:
                if p.id not in seen:
                    seen.add(p.id)
                    results.append(_to_result(p, "description"))
                if len(results) >= limit:
                    break

        return results[:limit]

    def lookup_cross_reference(self, ref_number: str) -> list[ProductSearchResult]:
        """
        Exact lookup by OEM or competitor part number (separator/case-insensitive).
        Returns the JAKS product(s) that match this cross reference.
        """
        nref = normalize_part(ref_number)
        if not nref:
            return []
        from app.services.pricing_service import PricingService
        _pricing = PricingService(self.db)
        hits = (
            self.db.query(CrossReference, Product)
            .join(Product, CrossReference.product_id == Product.id)
            .filter(CrossReference.ref_number_norm == nref)
            .all()
        )
        results = []
        for xref, p in hits:
            src = next((s for s in p.vendor_sources if s.is_preferred), None)
            sell = _pricing.sell_price_for(p)  # O5 — settings-backed default markup
            results.append(ProductSearchResult(
                product_id=p.id,
                part_number=p.sku,
                description=p.title,
                current_cost=p.cost,
                suggested_sell=sell,
                qty_on_hand=p.qty_on_hand,
                vendor_name=src.vendor.name if src and src.vendor else None,
                status=p.status,
                match_type="cross_ref",
                cross_ref_number=xref.ref_number,
            ))
        return results

    def search_customers(self, query: str, limit: int = 20) -> list[dict]:
        """
        Search customers by company_name, contact_name, or phone.
        Phone queries are normalized to digits-only so "303-555-1234" and
        "3035551234" both find the same record.
        Returns lightweight dicts suitable for autocomplete dropdowns.
        """
        q = query.strip()
        if not q:
            return []

        digits = re.sub(r"\D", "", q)
        phone_filters = [Customer.phone.ilike(f"%{q}%")]
        if digits and digits != q:
            phone_filters.append(Customer.phone.ilike(f"%{digits}%"))

        hits = (
            self.db.query(Customer)
            .filter(
                Customer.is_active == True,  # noqa: E712
                or_(
                    Customer.company_name.ilike(f"%{q}%"),
                    Customer.contact_name.ilike(f"%{q}%"),
                    *phone_filters,
                ),
            )
            .order_by(Customer.company_name)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": c.id,
                "company_name": c.company_name,
                "contact_name": c.contact_name,
                "phone": c.phone,
                "payment_terms": c.payment_terms,
                "balance_due": None,  # computed by PaymentService when needed
            }
            for c in hits
        ]

    def search_vendors(self, query: str, limit: int = 20) -> list[dict]:
        """Search vendors by name or vendor_code."""
        q = query.strip()
        if not q:
            return []
        hits = (
            self.db.query(Vendor)
            .filter(
                Vendor.is_active == True,  # noqa: E712
                or_(
                    Vendor.name.ilike(f"%{q}%"),
                    Vendor.vendor_code.ilike(f"%{q}%"),
                ),
            )
            .order_by(Vendor.name)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": v.id,
                "name": v.name,
                "vendor_code": v.vendor_code,
                "phone": v.phone,
                "payment_terms": v.payment_terms,
            }
            for v in hits
        ]

    def search_quotes(self, query: str, limit: int = 6) -> list[dict]:
        """Search quotes by quote_number."""
        q = query.strip()
        if not q:
            return []
        _q_clean = re.sub(r"[^a-zA-Z0-9]", "", q)
        _filters = [Quote.quote_number.ilike(f"%{q}%")]
        if _q_clean:
            # De-dash the column too so "q2026" finds "Q-2026-0001" (unconditional OR).
            _dedashed = sa_func.replace(sa_func.replace(Quote.quote_number, "-", ""), " ", "")
            _filters.append(_dedashed.ilike(f"%{_q_clean}%"))
        hits = (
            self.db.query(Quote)
            .filter(or_(*_filters))
            .order_by(Quote.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": qt.id,
                "quote_number": qt.quote_number,
                "status": qt.status,
                "customer_name": qt.customer.company_name if qt.customer else "",
            }
            for qt in hits
        ]

    def search_sales_orders(self, query: str, limit: int = 6) -> list[dict]:
        """Search sales orders by so_number, customer_po_number, or ESN."""
        q = query.strip()
        if not q:
            return []
        _q_clean = re.sub(r"[^a-zA-Z0-9]", "", q)
        _so_filters = [SalesOrder.so_number.ilike(f"%{q}%")]
        if _q_clean:
            # De-dash the column too so "so20260001" finds "SO-2026-0001" (unconditional OR).
            _dedashed = sa_func.replace(sa_func.replace(SalesOrder.so_number, "-", ""), " ", "")
            _so_filters.append(_dedashed.ilike(f"%{_q_clean}%"))
        hits = (
            self.db.query(SalesOrder)
            .filter(
                or_(
                    *_so_filters,
                    SalesOrder.customer_po_number.ilike(f"%{q}%"),
                    SalesOrder.esn.ilike(f"%{q}%"),
                )
            )
            .order_by(SalesOrder.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": so.id,
                "so_number": so.so_number,
                "status": so.status,
                "customer_name": so.customer.company_name if so.customer else "",
            }
            for so in hits
        ]

    def search_purchase_orders(self, query: str, limit: int = 6) -> list[dict]:
        """Search purchase orders by po_number."""
        q = query.strip()
        if not q:
            return []
        hits = (
            self.db.query(PurchaseOrder)
            .filter(PurchaseOrder.po_number.ilike(f"%{q}%"))
            .order_by(PurchaseOrder.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": po.id,
                "po_number": po.po_number,
                "status": po.status,
                "vendor_name": po.vendor.name if po.vendor else "",
            }
            for po in hits
        ]

    def search_invoices(self, query: str, customer_id: int | None = None, limit: int = 20) -> list[dict]:
        """
        Search invoices by invoice_number, customer_po_number, or ESN.
        Optionally scoped to a single customer.
        """
        q = query.strip()
        if not q:
            return []
        base = self.db.query(Invoice).filter(
            or_(
                Invoice.invoice_number.ilike(f"%{q}%"),
                Invoice.customer_po_number.ilike(f"%{q}%"),
                Invoice.esn.ilike(f"%{q}%"),
            )
        )
        if customer_id is not None:
            base = base.filter(Invoice.customer_id == customer_id)
        hits = base.order_by(Invoice.created_at.desc()).limit(limit).all()
        return [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "customer_id": inv.customer_id,
                "status": inv.status,
                "total": inv.total,
                "balance_due": inv.balance_due,
                "customer_po_number": inv.customer_po_number,
                "esn": inv.esn,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            }
            for inv in hits
        ]
