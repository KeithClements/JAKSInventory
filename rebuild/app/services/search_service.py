"""
app/services/search_service.py
================================
Global search — the quote screen's live search bar hits this.

Search priority (from quoting_requirements.md):
  1. JAKS part number (exact prefix match)
  2. OEM / cross-reference numbers (CrossReference table)
  3. Competitor part numbers (CrossReference table, ref_type='competitor')
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

from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import CrossReference, Product, ProductVendorSource
from app.models.purchase_order import PurchaseOrder
from app.models.quote import Quote, SalesOrder
from app.models.vendor import Vendor
from app.services.base import BaseService
from app.utils import calc_sell_price


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
    match_type: str          # "part_number" | "cross_ref" | "description" | "vendor_sku"
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

        seen: set[int] = set()
        results: list[ProductSearchResult] = []

        def _to_result(product: Product, match_type: str, cross_ref_number: str | None = None) -> ProductSearchResult:
            src = next((s for s in product.vendor_sources if s.is_preferred), None)
            markup = product.markup_pct or 30.0
            sell = product.price_override if (product.price_override and product.price_override > 0) else calc_sell_price(product.cost, markup)

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

        upper = q.upper()

        # 1. Exact SKU match
        exact = base_q.filter(Product.sku == upper).first()
        if exact and exact.id not in seen:
            seen.add(exact.id)
            results.append(_to_result(exact, "part_number"))

        # 2. SKU prefix match (e.g. user typed "14-" → matches "14-1234")
        if len(results) < limit:
            prefix_hits = (
                base_q.filter(Product.sku.ilike(f"{upper}%"))
                .limit(limit)
                .all()
            )
            for p in prefix_hits:
                if p.id not in seen:
                    seen.add(p.id)
                    results.append(_to_result(p, "part_number"))
                if len(results) >= limit:
                    break

        # 3. Cross-reference lookup (OEM + competitor + vendor_alt)
        if len(results) < limit:
            xref_hits = (
                self.db.query(CrossReference, Product)
                .join(Product, CrossReference.product_id == Product.id)
                .filter(CrossReference.ref_number.ilike(f"%{q}%"))
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

        # 4. Vendor SKU / part number
        if len(results) < limit:
            pvs_hits = (
                self.db.query(ProductVendorSource, Product)
                .join(Product, ProductVendorSource.product_id == Product.id)
                .filter(
                    or_(
                        ProductVendorSource.vendor_part_number.ilike(f"%{q}%"),
                        ProductVendorSource.vendor_sku.ilike(f"%{q}%"),
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

        # 5. Description keyword (lowest priority — can match many unrelated items)
        if len(results) < limit:
            desc_hits = (
                base_q.filter(
                    or_(
                        Product.title.ilike(f"%{q}%"),
                        Product.description.ilike(f"%{q}%"),
                    )
                )
                .limit(limit)
                .all()
            )
            for p in desc_hits:
                if p.id not in seen:
                    seen.add(p.id)
                    results.append(_to_result(p, "description"))
                if len(results) >= limit:
                    break

        return results[:limit]

    def lookup_cross_reference(self, ref_number: str) -> list[ProductSearchResult]:
        """
        Exact lookup by OEM or competitor part number.
        Returns the JAKS product(s) that match this cross reference.
        """
        hits = (
            self.db.query(CrossReference, Product)
            .join(Product, CrossReference.product_id == Product.id)
            .filter(CrossReference.ref_number == ref_number.upper())
            .all()
        )
        results = []
        for xref, p in hits:
            src = next((s for s in p.vendor_sources if s.is_preferred), None)
            markup = p.markup_pct or 30.0
            sell = p.price_override if (p.price_override and p.price_override > 0) else calc_sell_price(p.cost, markup)
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
        if _q_clean and _q_clean.lower() != q.lower():
            _filters.append(Quote.quote_number.ilike(f"%{_q_clean}%"))
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
        if _q_clean and _q_clean.lower() != q.lower():
            _so_filters.append(SalesOrder.so_number.ilike(f"%{_q_clean}%"))
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
