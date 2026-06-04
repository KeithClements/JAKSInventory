"""
app/services/product_service.py
=================================
Product lifecycle management.

Responsibilities:
  - Create / update / deactivate products
  - Manage vendor sources (ProductVendorSource) — preferred vendor logic
  - Track cost changes (ProductCostHistory) via compare_and_record_cost_change()
  - Manage cross references (CrossReference)
  - Handle product status transitions (active → superseded → discontinued)
  - Query on-hand inventory quantity (aggregated from InventoryTransaction ledger)
  - Generate JAKS SKU: JAKS-[VENDOR_CODE]-[PART#]
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from app.constants import AuditAction, CrossRefType, EntityType, InventoryTxnType, ProductStatus
from app.models.inventory import InventoryTransaction
from app.models.product import (
    CrossReference, Product, ProductCategory, ProductCostHistory, ProductImage,
    ProductVendorSource,
)
from app.models.vendor import Vendor
from app.services.base import BaseService


class ProductService(BaseService):

    # ── Category tree (#7 — picker) ───────────────────────────────────────────

    def category_tree(self) -> list[dict]:
        """Active product categories as a nested tree (parent → children) for the
        category/subcategory picker. parent_id already exists on ProductCategory —
        no schema change. Each node: {id, name, level, full_path, children:[...]}.
        Children are sorted by name; orphans (parent inactive/missing) surface at
        the top level so nothing is hidden."""
        cats = (
            self.db.query(ProductCategory)
            .filter(ProductCategory.is_active == True)  # noqa: E712
            .all()
        )
        present = {c.id for c in cats}
        by_parent: dict[int | None, list] = {}
        for c in cats:
            root_key = c.parent_id if c.parent_id in present else None
            by_parent.setdefault(root_key, []).append(c)

        def build(parent_id):
            nodes = sorted(by_parent.get(parent_id, []), key=lambda c: (c.name or "").lower())
            return [{
                "id": c.id,
                "name": c.name,
                "level": c.level,
                "full_path": c.full_path,
                "children": build(c.id),
            } for c in nodes]

        return build(None)

    # ── Product CRUD ──────────────────────────────────────────────────────────

    @staticmethod
    def _validate_core_charges(has_core: bool, vendor_core, customer_core) -> None:
        """
        A core charge is a deposit JAKS recovers from the vendor when the old core
        is returned. If we charge the customer LESS than the vendor charges us,
        every unreturned core is a loss. Require customer core >= vendor core.
        (Equal is allowed — that's a clean pass-through deposit.)
        """
        if not has_core:
            return
        vendor = float(vendor_core or 0)
        customer = float(customer_core or 0)
        if customer < vendor:
            raise ValueError(
                f"Customer core charge (${customer:,.2f}) can't be less than the "
                f"vendor core charge (${vendor:,.2f}). Charge the customer at least "
                f"the vendor core charge, or JAKS loses money on cores that aren't "
                f"returned."
            )

    def create_product(self, data: dict) -> Product:
        """
        Create a new product record.
        Validates: sku uniqueness.
        data keys: sku, title, description, brand, manufacturer, cost,
                   markup_pct, category_id, has_core, vendor_core_charge,
                   customer_core_charge, unit_of_measure, ...
        """
        sku = data.get("sku", "").strip().upper()
        if not sku:
            raise ValueError("sku is required")
        existing = self.db.query(Product).filter(Product.sku == sku).first()
        if existing:
            raise ValueError(f"SKU '{sku}' already exists (product_id={existing.id})")

        self._validate_core_charges(
            bool(data.get("has_core", False)),
            data.get("vendor_core_charge", 0.0),
            data.get("customer_core_charge", 0.0),
        )

        product = Product(
            sku=sku,
            title=data.get("title", ""),
            description=data.get("description", ""),
            brand=data.get("brand", ""),
            manufacturer=data.get("manufacturer", ""),
            cost=float(data.get("cost", 0.0)),
            markup_pct=data.get("markup_pct"),
            price_override=data.get("price_override"),
            category_id=data.get("category_id"),
            has_core=bool(data.get("has_core", False)),
            vendor_core_charge=float(data.get("vendor_core_charge", 0.0)),
            customer_core_charge=float(data.get("customer_core_charge", 0.0)),
            unit_of_measure=data.get("unit_of_measure", "EA"),
            reorder_point=int(data.get("reorder_point", 0)),
            max_stock_level=data.get("max_stock_level"),
            weight_lbs=float(data.get("weight_lbs", 0.0)),
            is_returnable=bool(data.get("is_returnable", True)),
            return_policy_type=data.get("return_policy_type", "standard"),
            notes=data.get("notes", ""),
            internal_notes=data.get("internal_notes", ""),
            status=ProductStatus.ACTIVE,
        )
        self.db.add(product)
        self.db.flush()  # get product.id before audit

        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product.id,
            action=AuditAction.CREATED,
            new_value={"sku": sku, "title": product.title},
        )
        self.db.commit()
        return product

    def update_product(self, product_id: int, data: dict) -> Product:
        """
        Update product fields.
        Automatically records cost change history if cost changes.
        Audits every call with old/new snapshot.
        """
        product = self._get_or_404(product_id)

        # Validate the *effective* core charges (incoming value, else current) before
        # any mutation so a bad save is rejected cleanly with nothing partially written.
        self._validate_core_charges(
            bool(data["has_core"]) if "has_core" in data else product.has_core,
            data["vendor_core_charge"] if "vendor_core_charge" in data else product.vendor_core_charge,
            data["customer_core_charge"] if "customer_core_charge" in data else product.customer_core_charge,
        )

        old_snapshot = {
            "sku": product.sku,
            "title": product.title,
            "cost": product.cost,
            "markup_pct": product.markup_pct,
            "price_override": product.price_override,
        }

        # Cost change — record history before updating
        new_cost = data.get("cost")
        if new_cost is not None and float(new_cost) != product.cost:
            self.db.add(ProductCostHistory(
                product_id=product_id,
                vendor_id=None,
                old_cost=product.cost,
                new_cost=float(new_cost),
                changed_by_id=self.current_user_id,
                notes=data.get("cost_change_notes", "manual update"),
            ))

        updatable = [
            "title", "description", "brand", "manufacturer", "cost",
            "markup_pct", "price_override", "category_id", "has_core",
            "vendor_core_charge", "customer_core_charge", "unit_of_measure",
            "reorder_point", "max_stock_level", "weight_lbs", "is_returnable",
            "return_policy_type", "return_window_override_days",
            "restock_fee_percent", "notes", "internal_notes",
            "engine_manufacturer", "engine_model",
            # Warranty fields
            "is_warrantable", "manufacturer_warranty_months",
            "supplier_warranty_months", "supplier_warranty_type",
            "jaks_warranty_months", "warranty_percentage",
        ]
        for field in updatable:
            if field in data:
                setattr(product, field, data[field])

        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.EDITED,
            old_value=old_snapshot,
            new_value={k: data[k] for k in data if k in old_snapshot},
        )
        self.db.commit()
        return product

    def deactivate_product(self, product_id: int, reason: str) -> None:
        """Set status=inactive. Does not delete — preserves history."""
        product = self._get_or_404(product_id)
        old_status = product.status
        product.status = ProductStatus.INACTIVE
        product.is_active = False
        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=ProductStatus.INACTIVE,
            notes=reason,
        )
        self.db.commit()

    def supersede_product(self, old_product_id: int, new_product_id: int) -> None:
        """
        Mark old product as superseded, set superseded_by_id = new_product_id.
        Used when a vendor releases a revised part number.
        """
        old_product = self._get_or_404(old_product_id)
        # verify new product exists
        self._get_or_404(new_product_id)

        old_product.status = ProductStatus.SUPERSEDED
        old_product.superseded_by_id = new_product_id
        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=old_product_id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": str(old_product.status)},
            new_value={"status": ProductStatus.SUPERSEDED, "superseded_by_id": new_product_id},
        )
        self.db.commit()

    # ── Vendor Sources ────────────────────────────────────────────────────────

    def add_vendor_source(self, product_id: int, vendor_id: int, data: dict) -> ProductVendorSource:
        """
        Add a vendor source for a product.
        If is_preferred=True, clears preferred flag on all other sources first.
        If the product has no preferred source yet, the new source becomes
        preferred automatically so the cached product.cost mirrors it.
        Generates vendor_sku = JAKS-[VENDOR_CODE]-[PART#].
        """
        self._get_or_404(product_id)
        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")

        is_preferred = bool(data.get("is_preferred", False))
        if is_preferred:
            self._clear_preferred_vendor(product_id)
        elif not self._has_preferred_vendor(product_id):
            # The first vendor source becomes preferred automatically. Without
            # this, adding a single source with "Set as preferred" unchecked left
            # the product with NO preferred vendor, so product.cost — the "our
            # cost" shown in the list, preview dock, and Pricing card — never
            # updated to the source's cost. (Owner-reported 2026-06-01.)
            is_preferred = True

        part_number = data.get("vendor_part_number", "").strip()
        vendor_sku = f"JAKS-{vendor.vendor_code.upper()}-{part_number}" if part_number else ""

        source = ProductVendorSource(
            product_id=product_id,
            vendor_id=vendor_id,
            vendor_part_number=part_number,
            vendor_sku=vendor_sku,
            vendor_cost=float(data.get("vendor_cost", 0.0)),
            is_preferred=is_preferred,
            lead_time_days=data.get("lead_time_days"),
            notes=data.get("notes", ""),
            is_active=True,
        )
        self.db.add(source)

        # The preferred source's cost is the product's cost of record — mirror it
        # up to the cached product.cost (and log it for the Cost History tab).
        if is_preferred:
            product = self._get_or_404(product_id)
            self._sync_cost_from_preferred(product, source.vendor_cost, vendor_id)

        self.db.commit()
        return source

    def set_preferred_vendor(self, product_id: int, vendor_source_id: int) -> None:
        """Switch the preferred vendor for a product. Clears all other preferred flags."""
        self._clear_preferred_vendor(product_id)
        source = (
            self.db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.id == vendor_source_id,
                ProductVendorSource.product_id == product_id,
            )
            .first()
        )
        if source is None:
            raise ValueError(f"VendorSource {vendor_source_id} not found for product {product_id}")
        source.is_preferred = True

        # The preferred source governs product.cost — mirror it up.
        product = self._get_or_404(product_id)
        self._sync_cost_from_preferred(product, source.vendor_cost, source.vendor_id)
        self.db.commit()

    def compare_and_record_cost_change(
        self, product_id: int, vendor_id: int, new_cost: float, po_id: int | None = None
    ) -> bool:
        """
        Compare new_cost to current vendor_cost on ProductVendorSource.
        If different: writes ProductCostHistory row, updates vendor source cost,
        and updates product.cost if this is the preferred vendor.
        Returns True if cost changed.
        """
        source = (
            self.db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.product_id == product_id,
                ProductVendorSource.vendor_id == vendor_id,
                ProductVendorSource.is_active == True,  # noqa: E712
            )
            .first()
        )
        if source is None:
            return False

        if abs(source.vendor_cost - new_cost) < 0.001:
            return False  # no meaningful change

        self.db.add(ProductCostHistory(
            product_id=product_id,
            vendor_id=vendor_id,
            old_cost=source.vendor_cost,
            new_cost=new_cost,
            changed_by_id=self.current_user_id,
            po_id=po_id,
            notes="cost updated on PO receipt" if po_id else "manual",
        ))
        source.vendor_cost = new_cost
        source.last_cost_updated_at = datetime.utcnow()

        # Option A (owner-ruled 2026-06-01): product.cost is the moving-weighted-average
        # COGS, written only by InventoryService._apply_moving_average_cost on receipt.
        # DO NOT mirror vendor_cost to product.cost here — the vendor quote price lives
        # on ProductVendorSource.vendor_cost (already updated above). See §8N.

        # flush only — caller (POService.create_receipt) commits the whole transaction
        self.db.flush()
        return True

    # ── Inventory (ledger query) ───────────────────────────────────────────────

    def get_qty_on_hand(self, product_id: int, location_id: int | None = None) -> int:
        """
        Return current on-hand quantity from the InventoryTransaction ledger.
        If location_id is None, returns total across all locations.
        """
        query = self.db.query(func.sum(InventoryTransaction.qty_change)).filter(
            InventoryTransaction.product_id == product_id,
            InventoryTransaction.transaction_type.in_([
                "po_receipt", "return_to_stock", "manual_adjustment",
                "initial_count", "correction", "invoice_sale",
                "write_off", "drop_ship_sale",
            ]),
        )
        if location_id is not None:
            query = query.filter(InventoryTransaction.location_id == location_id)
        result = query.scalar()
        return int(result or 0)

    def is_below_reorder_point(self, product_id: int) -> bool:
        """Return True if qty_on_hand <= reorder_point."""
        product = self._get_or_404(product_id)
        return product.qty_on_hand <= product.reorder_point

    # ── Cross References ──────────────────────────────────────────────────────

    def add_cross_reference(
        self,
        product_id: int,
        ref_type: str,
        ref_number: str,
        brand: str | None = None,
        status: str = "proven",
    ) -> None:
        """Add an OEM or competitor cross reference."""
        self._get_or_404(product_id)
        xref = CrossReference(
            product_id=product_id,
            ref_type=ref_type,
            ref_number=ref_number.strip().upper(),
            brand=brand or "",
            status=status,
        )
        self.db.add(xref)
        self.db.commit()

    def remove_cross_reference(self, cross_reference_id: int) -> None:
        xref = self.db.query(CrossReference).filter(CrossReference.id == cross_reference_id).first()
        if xref is None:
            raise ValueError(f"CrossReference {cross_reference_id} not found")
        self.db.delete(xref)
        self.db.commit()

    def update_cross_reference_status(self, xref_id: int, status: str) -> CrossReference:
        """Update the confidence/verification status on a cross reference."""
        xref = self.db.query(CrossReference).filter(CrossReference.id == xref_id).first()
        if xref is None:
            raise ValueError(f"CrossReference {xref_id} not found")
        xref.status = status
        self.db.commit()
        return xref

    # ── Images ────────────────────────────────────────────────────────────────

    def add_product_image(
        self, product_id: int, file_path: str, source: str = "manual", alt_text: str = ""
    ) -> ProductImage:
        """
        Add an image to a product.
        If no primary image exists yet, the new image is set as primary automatically.
        """
        self._get_or_404(product_id)
        existing = (
            self.db.query(ProductImage)
            .filter(ProductImage.product_id == product_id)
            .all()
        )
        is_primary = not any(img.is_primary for img in existing)
        img = ProductImage(
            product_id=product_id,
            file_path=file_path,
            source=source,
            is_primary=is_primary,
            alt_text=alt_text,
        )
        self.db.add(img)
        self.db.commit()
        return img

    def remove_product_image(self, product_id: int, image_id: int) -> None:
        """
        Remove an image. If it was the primary image, promotes the
        next-oldest remaining image to primary automatically.
        """
        img = (
            self.db.query(ProductImage)
            .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
            .first()
        )
        if img is None:
            raise ValueError(f"Image {image_id} not found for product {product_id}")
        was_primary = img.is_primary
        self.db.delete(img)
        self.db.flush()
        if was_primary:
            next_img = (
                self.db.query(ProductImage)
                .filter(ProductImage.product_id == product_id)
                .order_by(ProductImage.id)
                .first()
            )
            if next_img:
                next_img.is_primary = True
        self.db.commit()

    def set_primary_image(self, product_id: int, image_id: int) -> None:
        """Set an image as the primary product image, clearing the flag on all others."""
        self._get_or_404(product_id)
        self.db.query(ProductImage).filter(
            ProductImage.product_id == product_id,
        ).update({"is_primary": False}, synchronize_session="fetch")
        img = (
            self.db.query(ProductImage)
            .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
            .first()
        )
        if img is None:
            raise ValueError(f"Image {image_id} not found for product {product_id}")
        img.is_primary = True
        self.db.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_or_404(self, product_id: int) -> Product:
        product = self.db.query(Product).filter(Product.id == product_id).first()
        if product is None:
            raise ValueError(f"Product {product_id} not found")
        return product

    def _clear_preferred_vendor(self, product_id: int) -> None:
        """Remove is_preferred from all vendor sources for this product."""
        self.db.query(ProductVendorSource).filter(
            ProductVendorSource.product_id == product_id,
            ProductVendorSource.is_preferred == True,  # noqa: E712
        ).update({"is_preferred": False}, synchronize_session="fetch")

    def _has_preferred_vendor(self, product_id: int) -> bool:
        """True if an active vendor source is already flagged preferred."""
        return (
            self.db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.product_id == product_id,
                ProductVendorSource.is_active == True,  # noqa: E712
                ProductVendorSource.is_preferred == True,  # noqa: E712
            )
            .first()
            is not None
        )

    def _sync_cost_from_preferred(
        self, product: Product, new_cost, vendor_id: int | None = None
    ) -> None:
        """
        Record that the preferred vendor source's quoted cost changed.
        Does NOT write product.cost — product.cost is the moving-weighted-average
        COGS maintained by InventoryService._apply_moving_average_cost (Option A,
        owner-ruled 2026-06-01, see JAKS_UI_Change_Plan.md §8N).

        The ProductCostHistory row is kept so the Cost History tab shows vendor-
        quote changes alongside receipt-driven cost changes.
        """
        new_cost = float(new_cost or 0.0)
        if abs(product.cost - new_cost) >= 0.001:
            self.db.add(ProductCostHistory(
                product_id=product.id,
                vendor_id=vendor_id,
                old_cost=product.cost,   # moving-avg at time of vendor quote change
                new_cost=new_cost,
                changed_by_id=self.current_user_id,
                notes="vendor source cost updated (product.cost moving-avg unchanged)",
            ))
