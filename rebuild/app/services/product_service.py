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
    CrossReference, Product, ProductCostHistory, ProductImage, ProductVendorSource,
)
from app.models.vendor import Vendor
from app.services.base import BaseService


class ProductService(BaseService):

    # ── Product CRUD ──────────────────────────────────────────────────────────

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
        Generates vendor_sku = JAKS-[VENDOR_CODE]-[PART#].
        """
        self._get_or_404(product_id)
        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")

        is_preferred = bool(data.get("is_preferred", False))
        if is_preferred:
            self._clear_preferred_vendor(product_id)

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

        # If this is the preferred source, sync cost up to product
        if is_preferred:
            product = self._get_or_404(product_id)
            product.cost = source.vendor_cost
            product.cost_source = "vendor"

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

        # Sync cost up to product
        product = self._get_or_404(product_id)
        product.cost = source.vendor_cost
        product.cost_source = "vendor"
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

        if source.is_preferred:
            product = self._get_or_404(product_id)
            product.cost = new_cost
            product.cost_source = "vendor"

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
