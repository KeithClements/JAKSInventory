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

from sqlalchemy import func

from app.constants import AuditAction, CrossRefType, EntityType, InventoryTxnType, ProductStatus
from app.models.inventory import InventoryTransaction
from app.models.product import (
    CrossReference, Product, ProductApplication, ProductCategory, ProductCostHistory,
    ProductImage, ProductVendorSource,
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

    @staticmethod
    def _validate_pricing(cost=None, markup_pct=None, price_override=None,
                          vendor_cost=None) -> None:
        """
        Money fields can never be negative. The new-product / edit forms only set
        ``min=0`` client-side, so a hand-crafted or autofilled POST could still
        push a negative cost (QA 2026-06-29: ``cost=-50`` saved silently). Reject
        any negative money value here so create, update, autosave, AND quick-create
        all inherit the guard.

        Zero is allowed on purpose — ``cost`` is legitimately 0 before the first
        receipt (moving-avg COGS), and a 0 % markup / $0 override are real values.
        Only values supplied in this call are checked (None = "not being set"), so
        a partial update that omits a field never trips on a stored value.
        """
        for label, value in (
            ("Cost", cost),
            ("Markup %", markup_pct),
            ("Price override", price_override),
            ("Vendor cost", vendor_cost),
        ):
            if value is None:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                # Non-numeric strings are caught upstream by the form parser; a
                # non-numeric reaching here just isn't a negative-value violation.
                continue
            if num < 0:
                raise ValueError(
                    f"{label} can't be negative (got {num:,.2f}). Use 0 if there is "
                    f"no value yet."
                )

    def create_product(self, data: dict) -> Product:
        """
        Create a new product record.

        Two paths, decided by what's in ``data``:
          • Vendor-SKU path (the new product form): when ``vendor_id`` is
            provided the customer-facing SKU is the vendor's real part number
            (MASTER_PLAN §20) — unless the part is private-label
            (``is_house_brand``), where the owner-typed JAKS Product # is used
            instead and the vendor part# still rides on the source for the PO.
            The vendor's part# is stamped on a ProductVendorSource (preferred)
            and a VENDOR_ALT CrossReference so a search for it finds the product.
          • Manual-SKU path (legacy callers: quick-create slide-over, importer,
            tests): ``data['sku']`` is supplied directly and stamped as-is. The
            auto-SKU columns (engine_code/category_code/part_seq) are left blank.

        Required (vendor-SKU): vendor_id, vendor_part_number.
        Optional (vendor-SKU): vendor_cost, engine_make, engine_model,
                               is_house_brand, jaks_product_number.
        """
        # ── Path selector — vendor_id present means "vendor source known" ──────
        vendor_id = data.get("vendor_id")
        if vendor_id:
            return self._create_product_with_auto_sku(data)

        # ── Manual-SKU path (legacy) ────────────────────────────────────────────
        sku = data.get("sku", "").strip().upper()
        if not sku:
            raise ValueError("sku is required")
        existing = self.db.query(Product).filter(Product.sku == sku).first()
        if existing:
            raise ValueError(f"SKU '{sku}' already exists (product_id={existing.id})")

        self._validate_pricing(
            cost=data.get("cost"),
            markup_pct=data.get("markup_pct"),
            price_override=data.get("price_override"),
            vendor_cost=data.get("vendor_cost"),
        )
        self._validate_core_charges(
            bool(data.get("has_core", False)),
            data.get("vendor_core_charge", 0.0),
            data.get("customer_core_charge", 0.0),
        )

        product = Product(
            sku=sku,
            title=data.get("title", ""),
            description=data.get("description", ""),
            seo_description=data.get("seo_description", ""),
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

    # ── Auto-SKU orchestrator (new product form) ──────────────────────────────

    def _create_product_with_auto_sku(self, data: dict) -> Product:
        """The vendor-SKU path of ``create_product`` (MASTER_PLAN §20). The
        customer-facing SKU is the vendor's real part number for standard parts,
        or the owner-typed JAKS Product # for private-label (``is_house_brand``)
        parts; either way the vendor's part# is stamped on a preferred
        ProductVendorSource AND a VENDOR_ALT CrossReference so searches find it.

        All-or-nothing: any failure rolls back the whole transaction so we never
        leave a Product behind without its SKU + vendor source + cross-ref.
        """
        vendor_id = int(data["vendor_id"])
        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")

        part_number = (data.get("vendor_part_number") or "").strip()
        if not part_number:
            raise ValueError("Vendor part number is required")

        # Reject a duplicate (vendor, part#) — that exact source already exists.
        collision = (
            self.db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.vendor_id == vendor_id,
                ProductVendorSource.vendor_part_number == part_number,
                ProductVendorSource.is_active == True,  # noqa: E712
            )
            .first()
        )
        if collision is not None:
            existing_sku = (collision.product.sku
                            if collision.product is not None else "?")
            raise ValueError(
                f"This vendor already sources part# {part_number} "
                f"(product {existing_sku})."
            )

        self._validate_pricing(
            cost=data.get("cost"),
            markup_pct=data.get("markup_pct"),
            price_override=data.get("price_override"),
            vendor_cost=data.get("vendor_cost"),
        )
        self._validate_core_charges(
            bool(data.get("has_core", False)),
            data.get("vendor_core_charge", 0.0),
            data.get("customer_core_charge", 0.0),
        )

        # Customer-facing SKU. DEFAULT (vendor scheme): {prefix}-{vendor_code}-{part#}
        # e.g. JAKS-PAI-340097 — readable + unique per (vendor, part#). CODED scheme
        # (opt-in per product via data['sku_scheme']='coded', or the sku_scheme
        # setting): {prefix}-{ENGINE}-{CATEGORY}-{Vseq} via SkuService. Private-label
        # (is_house_brand) parts keep the owner-typed JAKS Product #.
        from app.settings_utils import get_setting_value_db
        from app.services.sku_service import build_vendor_sku, bare_part_number, SkuService
        engine_make = (data.get("engine_make") or "").strip()
        engine_model = (data.get("engine_model") or "").strip()
        # House brand = the per-product checkbox OR the vendor being flagged
        # private_label (DFT/Migao, etc.). Either way the vendor code is hidden.
        is_house_brand = (bool(data.get("is_house_brand", False))
                          or bool(getattr(vendor, "private_label", False)))
        scheme = (data.get("sku_scheme")
                  or get_setting_value_db(self.db, "sku_scheme", "vendor")).strip().lower()
        prefix = get_setting_value_db(self.db, "sku_prefix", "JAKS")
        use_coded = scheme == "coded" and not is_house_brand
        # Strip any stray {prefix}-{code}- the typed part # may already carry, so
        # re-assembly never doubles it (symmetric with the importer). Identity-safe
        # for a normal bare part #.
        bare = bare_part_number(part_number)

        if is_house_brand:
            # House brand: an owner-typed JAKS Product # wins (true private label);
            # otherwise mask the vendor code with the house prefix and KEEP the
            # vendor part # → {prefix}-{prefix}-{part#}, e.g. JAKS-JAKS-10R1273.
            owner_number = (data.get("jaks_product_number")
                            or data.get("sku") or "").strip().upper()
            customer_sku = (owner_number
                            or build_vendor_sku(prefix, prefix, bare))
        elif use_coded:
            if not (vendor.vendor_number or "").strip():
                raise ValueError(
                    "The coded SKU scheme needs this vendor's 1-digit SKU number "
                    "(set it on the vendor form) — or use the default scheme."
                )
            customer_sku = None   # minted after the product row exists (needs id + category)
        else:
            customer_sku = build_vendor_sku(prefix, vendor.vendor_code, bare)

        # Unique-sku guard for the pre-computed schemes (coded skus are sequenced
        # and unique by construction). Fail loud rather than save a duplicate.
        if customer_sku is not None:
            clash = self.db.query(Product).filter(Product.sku == customer_sku).first()
            if clash is not None:
                if is_house_brand:
                    # The part is already house brand — masking collapsed the vendor
                    # code, so the only remedy is a distinct owner number.
                    raise ValueError(
                        f"SKU '{customer_sku}' already exists (product_id={clash.id}). "
                        f"Give this private-label part a distinct JAKS Product #."
                    )
                raise ValueError(
                    f"SKU '{customer_sku}' already exists (product_id={clash.id}). "
                    f"Another vendor may use the same part number — mark this part "
                    f"private-label and give it a distinct JAKS Product #."
                )

        import uuid as _uuid
        product = Product(
            sku=customer_sku or f"__SKUTMP__{_uuid.uuid4().hex}",
            title=data.get("title", ""),
            description=data.get("description", ""),
            seo_description=data.get("seo_description", ""),
            brand=data.get("brand", ""),
            manufacturer=data.get("manufacturer", ""),
            engine_manufacturer=engine_make,
            engine_model=engine_model,
            is_house_brand=is_house_brand,
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
        self.db.flush()  # product.id available below

        if use_coded:
            # Mint the coded SKU now that the product row (id + category relationship)
            # exists. assign_new_sku stamps engine_code/category_code/part_seq and a
            # sequenced, unique sku, replacing the temp placeholder above.
            SkuService(self.db, self.current_user_id).assign_new_sku(product, vendor)
            self.db.flush()

        # Vendor source — preferred + active. vendor_sku mirrors the typed part#
        # so it stays searchable through the precomputed _norm columns. (Cost is
        # optional on the new form; missing/None → 0.0 on the vendor source.)
        source = ProductVendorSource(
            product_id=product.id,
            vendor_id=vendor_id,
            vendor_part_number=part_number,
            vendor_sku=part_number,
            vendor_cost=float(data.get("vendor_cost") or 0.0),
            is_preferred=True,
            is_active=True,
        )
        self.db.add(source)

        # VENDOR_ALT cross-reference — guarded against the
        # (product_id, ref_type, ref_number) unique index. Stored uppercased
        # to match add_cross_reference's normalization.
        ref_number = part_number.upper()
        existing_xref = (
            self.db.query(CrossReference)
            .filter(
                CrossReference.product_id == product.id,
                CrossReference.ref_type == CrossRefType.VENDOR_ALT,
                CrossReference.ref_number == ref_number,
            )
            .first()
        )
        if existing_xref is None:
            self.db.add(CrossReference(
                product_id=product.id,
                ref_type=CrossRefType.VENDOR_ALT,
                ref_number=ref_number,
                brand=vendor.name or "",
                status="proven",
            ))

        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product.id,
            action=AuditAction.CREATED,
            new_value={
                "sku": product.sku, "title": product.title,
                "vendor_id": vendor_id, "vendor_part_number": part_number,
            },
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

        # Reject negative money before any mutation. Only fields actually present in
        # ``data`` are checked (others are None → skipped), so a partial save/autosave
        # that omits a price field never trips on an untouched stored value.
        self._validate_pricing(
            cost=data.get("cost"),
            markup_pct=data.get("markup_pct"),
            price_override=data.get("price_override"),
            vendor_cost=data.get("vendor_cost"),
        )

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
            "engine_manufacturer", "engine_model", "is_house_brand",
            # SEO / marketplace (Shopify + eBay export-sync)
            "seo_title", "seo_description", "search_keywords",
            # Warranty fields
            "is_warrantable", "manufacturer_warranty_months",
            "supplier_warranty_months", "supplier_warranty_type",
            "jaks_warranty_months", "warranty_percentage",
        ]
        for field in updatable:
            if field in data:
                setattr(product, field, data[field])

        # ── Operator price lock (scraper-audit bug #11) ────────────────────────
        # An operator CHANGING the exact sell price via the UI locks it against
        # the nightly scraper feed (pricing_update_sell skips locked products'
        # sell-price writes). Clearing the override clears the lock so the feed
        # may manage the price again. Re-posting the SAME stored value (autosave
        # re-submits the whole form on every debounce) never toggles the lock.
        if "price_override" in data:
            _new_po = data["price_override"]
            _old_po = old_snapshot["price_override"]
            if _new_po is None:
                if _old_po is not None:
                    product.price_override_locked = False
            elif _old_po is None or abs(float(_old_po) - float(_new_po)) >= 0.005:
                product.price_override_locked = True

        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.EDITED,
            old_value=old_snapshot,
            new_value={k: data[k] for k in data if k in old_snapshot},
        )
        self.db.commit()
        return product

    def unlock_price_override(self, product_id: int) -> Product:
        """Clear ONLY the operator price lock — price_override itself is kept.

        The product screen's "unlock" control calls this so the nightly scraper
        feed (pricing_update_sell) may manage the sell price again. Idempotent:
        unlocking an unlocked product is a no-op (no audit row)."""
        product = self._get_or_404(product_id)
        if product.price_override_locked:
            product.price_override_locked = False
            self.audit(
                entity_type=EntityType.PRODUCT,
                entity_id=product_id,
                action=AuditAction.EDITED,
                old_value={"price_override_locked": True},
                new_value={"price_override_locked": False},
                notes="price lock cleared (scraper feed may manage the sell price)",
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
        Compare new_cost (a PO receipt price) to the stored quote on
        ProductVendorSource.vendor_cost. If they differ, record a ProductCostHistory
        row capturing the divergence. Per §8N this is history-only: it does NOT mutate
        the vendor quote (vendor_cost stays the quoted/catalog price) and does NOT
        touch product.cost (the moving-average COGS, owned by _apply_moving_average_cost).
        Returns True if a history row was written (i.e. the costs differed).
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

        # §8N (owner-ruled 2026-06-01): record the divergence in ProductCostHistory
        # but DO NOT mutate the stored quote. ProductVendorSource.vendor_cost is the
        # vendor's quoted/catalog price — a PO receipt price (often a one-off negotiated
        # cost) must not clobber it. And product.cost is the moving-weighted-average
        # COGS, written only by InventoryService._apply_moving_average_cost on receipt,
        # so a receipt cost is never mirrored onto product.cost here either.

        # flush only — caller (POService.create_receipt) commits the whole transaction
        self.db.flush()
        return True

    # ── Inventory (ledger query) ───────────────────────────────────────────────

    # R3 — Txn types that move AVAILABILITY (qty_committed), not on-hand stock.
    # Semantics (verified against every writer):
    #   SO_COMMITTED  (qty_change = -qty) — reserves stock at SO create/add-line;
    #                 the parts are still physically on the shelf, so on-hand is
    #                 UNCHANGED. Excluding these is BY DESIGN, not an omission.
    #   SO_RELEASED   (qty_change = +qty) — returns the reservation on cancel /
    #                 qty decrease / fulfill. The actual on-hand deduction is the
    #                 separate INVOICE_SALE row written by InvoiceService.finalise
    #                 (which IS counted). Counting SO_COMMITTED/SO_RELEASED would
    #                 double-move on-hand for every committed-then-invoiced line.
    #   TRANSFER      — location-only moves; live in inventory_transfers, never
    #                 written to this ledger, excluded here as a guard anyway.
    # Recomputation EXCLUDES these instead of allow-listing the rest so any
    # future on-hand-affecting txn type is counted automatically rather than
    # silently missed (the old allow-list was one new enum member away from a
    # drifting resync).
    _COMMITMENT_ONLY_TXN_TYPES = (
        InventoryTxnType.SO_COMMITTED,
        InventoryTxnType.SO_RELEASED,
        InventoryTxnType.TRANSFER,
    )

    def get_qty_on_hand(self, product_id: int, location_id: int | None = None) -> int:
        """
        Return current on-hand quantity recomputed from the InventoryTransaction
        ledger (the source of truth; Product.qty_on_hand is a cache).
        If location_id is None, returns total across all locations.

        Commitment movements (SO_COMMITTED / SO_RELEASED) are excluded by
        design — committed stock is still ON HAND until invoiced. See
        _COMMITMENT_ONLY_TXN_TYPES above.
        """
        query = self.db.query(func.sum(InventoryTransaction.qty_change)).filter(
            InventoryTransaction.product_id == product_id,
            InventoryTransaction.transaction_type.not_in(
                [t.value for t in self._COMMITMENT_ONLY_TXN_TYPES]
            ),
        )
        if location_id is not None:
            query = query.filter(InventoryTransaction.location_id == location_id)
        result = query.scalar()
        return int(result or 0)

    def get_qty_committed_from_ledger(
        self, product_id: int, location_id: int | None = None
    ) -> int:
        """
        R3 — Recompute the open commitment (qty_committed cache) from the
        commitment ledger rows. SO_COMMITTED rows carry qty_change = -qty
        (reserved) and SO_RELEASED rows carry qty_change = +qty (released), so
        the outstanding commitment is the NEGATED sum over both types.
        Counterpart of :meth:`get_qty_on_hand` — together the two cover every
        ledger row except TRANSFER (location-only, never written here).
        """
        query = self.db.query(func.sum(InventoryTransaction.qty_change)).filter(
            InventoryTransaction.product_id == product_id,
            InventoryTransaction.transaction_type.in_([
                InventoryTxnType.SO_COMMITTED.value,
                InventoryTxnType.SO_RELEASED.value,
            ]),
        )
        if location_id is not None:
            query = query.filter(InventoryTransaction.location_id == location_id)
        return -int(query.scalar() or 0)

    def resync_qty_on_hand(self, product_id: int) -> tuple[int, int]:
        """
        R3 — Recompute qty_on_hand from the ledger and write the corrected value
        to the Product cache. Returns (old_qty, new_qty).

        Audit-logs old → new even when the delta is 0 so there is a record that
        the resync ran. Flushes only — the caller (admin route) commits.
        """
        product = self._get_or_404(product_id)
        old_qty = product.qty_on_hand
        new_qty = self.get_qty_on_hand(product_id)
        product.qty_on_hand = new_qty
        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.INVENTORY_ADJUSTED,
            old_value={"qty_on_hand": old_qty},
            new_value={
                "qty_on_hand": new_qty,
                "delta": new_qty - old_qty,
                "source": "ledger_resync",
            },
            notes=f"Inventory resync from ledger: {old_qty} → {new_qty}",
        )
        self.db.flush()
        return old_qty, new_qty

    def resync_qty_committed(self, product_id: int) -> tuple[int, int]:
        """§21 — recompute qty_committed from the commitment ledger and write the
        corrected cache. Mirrors resync_qty_on_hand; recovery path for a drifted
        qty_committed (which otherwise makes in-stock parts read as unavailable
        with no fix short of a ledger-polluting manual adjustment). Returns
        (old, new). Flushes only — caller commits."""
        product = self._get_or_404(product_id)
        old_qty = product.qty_committed
        new_qty = max(0, self.get_qty_committed_from_ledger(product_id))
        product.qty_committed = new_qty
        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.INVENTORY_ADJUSTED,
            old_value={"qty_committed": old_qty},
            new_value={"qty_committed": new_qty, "delta": new_qty - old_qty,
                       "source": "ledger_resync"},
            notes=f"qty_committed resync from ledger: {old_qty} → {new_qty}",
        )
        self.db.flush()
        return old_qty, new_qty

    def resync_qty_on_order(self, product_id: int) -> tuple[int, int]:
        """§21 — recompute qty_on_order from open purchase orders and write the
        corrected cache. On-order is incremented at Place Order (→ SENT) or at
        verbal-order create (→ VERBAL_ORDER) and decremented on receipt, so the
        truth is the outstanding qty over POs that are placed-but-not-done
        (SENT / VERBAL_ORDER / PARTIAL). Returns (old, new). Flushes only.
        C9 — VERBAL_ORDER was previously excluded here, so phone orders never
        showed as on-order even after a resync."""
        from app.constants import POStatus
        from app.models.purchase_order import POLine, PurchaseOrder
        product = self._get_or_404(product_id)
        old_qty = product.qty_on_order
        outstanding = (
            self.db.query(POLine)
            .join(PurchaseOrder, POLine.po_id == PurchaseOrder.id)
            .filter(
                POLine.product_id == product_id,
                PurchaseOrder.status.in_(
                    [POStatus.SENT, POStatus.VERBAL_ORDER, POStatus.PARTIAL]
                ),
            )
            .all()
        )
        new_qty = sum(ln.qty_outstanding for ln in outstanding)
        product.qty_on_order = new_qty
        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.INVENTORY_ADJUSTED,
            old_value={"qty_on_order": old_qty},
            new_value={"qty_on_order": new_qty, "delta": new_qty - old_qty,
                       "source": "open_po_resync"},
            notes=f"qty_on_order resync from open POs: {old_qty} → {new_qty}",
        )
        self.db.flush()
        return old_qty, new_qty

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
        from app.services.crossref_hygiene import is_garbage_ref
        if is_garbage_ref(ref_number):
            raise ValueError(
                f"'{ref_number.strip()}' is not a usable part number — "
                "placeholder values and refs under 3 characters pollute cross-reference search."
            )
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

    # ── Engine Applications (fitment) ─────────────────────────────────────────
    # §21 — owner-authored engine fitment, surfaced on product detail and queried
    # by the engine-application search. Dedup grain is the model's documented
    # (product_id, engine_make, engine_model, cpl) — re-adding the same fit is a
    # no-op rather than a duplicate row.

    def add_application(
        self, product_id: int, engine_make: str, engine_model: str = "",
        cpl: str = "", esn_range: str | None = None, notes: str = "",
    ) -> ProductApplication:
        """Add an engine application to a product. Idempotent on the dedup grain;
        a blank engine_make is rejected (the one required identifier)."""
        self._get_or_404(product_id)
        make = (engine_make or "").strip()
        if not make:
            raise ValueError("Engine make is required for an application.")
        model = (engine_model or "").strip()
        cpl_v = (cpl or "").strip()

        existing = (
            self.db.query(ProductApplication)
            .filter(
                ProductApplication.product_id == product_id,
                func.lower(ProductApplication.engine_make) == make.lower(),
                func.lower(ProductApplication.engine_model) == model.lower(),
                func.lower(ProductApplication.cpl) == cpl_v.lower(),
            )
            .first()
        )
        if existing is not None:
            # Refresh the looser fields on the existing row instead of duplicating.
            existing.esn_range = (esn_range or "").strip() or None
            existing.notes = (notes or "").strip()
            self.db.commit()
            return existing

        app = ProductApplication(
            product_id=product_id, engine_make=make, engine_model=model,
            cpl=cpl_v, esn_range=(esn_range or "").strip() or None,
            notes=(notes or "").strip(), source="manual",
        )
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        return app

    def remove_application(self, application_id: int) -> None:
        app = self.db.query(ProductApplication).filter(
            ProductApplication.id == application_id
        ).first()
        if app is None:
            raise ValueError(f"ProductApplication {application_id} not found")
        self.db.delete(app)
        self.db.commit()

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

    # Image provenance for the "clean supersedes watermarked" rule: ATL re-hosts
    # unwatermarked photos on the Shopify CDN; PAI's own CDN is watermarked. (Smart
    # Import tags every image source='pai', so we key off the URL host, not source.)
    _CLEAN_IMG_HOST = "cdn.shopify.com"
    _WATERMARKED_IMG_HOST = "paiindustries.com"

    def supersede_primary_with_clean(self, product_id: int) -> bool:
        """Make a clean (unwatermarked, Shopify-CDN) image the product's PRIMARY,
        demoting any watermarked (PAI-CDN) image to a kept-but-non-primary fallback
        (nothing is deleted). No-op when the product has no clean image. Idempotent;
        returns True if the primary actually changed."""
        imgs = (self.db.query(ProductImage)
                .filter(ProductImage.product_id == product_id)
                .order_by(ProductImage.id).all())
        clean = [i for i in imgs if self._CLEAN_IMG_HOST in (i.file_path or "").lower()]
        if not clean:
            return False
        target_id = clean[0].id
        changed = False
        for i in imgs:
            want = (i.id == target_id)
            if bool(i.is_primary) != want:
                i.is_primary = want
                changed = True
        if changed:
            self.db.commit()
        return changed

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
