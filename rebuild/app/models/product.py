from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import (
    ProductStatus, ProductReturnPolicy, KitType, UnitOfMeasure,
    CrossRefType, SerialNumberStatus, SuggestedSellType,
)


class ProductCategory(Base):
    """Self-referential adjacency list. Max 3 levels: Major Group → Category → Subcategory."""
    __tablename__ = "product_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_categories.id"), nullable=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # ── §18 Category Maintenance — display order, optional per-category default
    #    markup (wired later, O5), and importer classification keywords. ──
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    default_markup_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    import_keywords: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JAKS SKU scheme: short code for this node (FP, INJ, GSK, ENG, …). The SKU's
    # [CATEGORY] segment uses the code of the DEEPEST category a product is tagged to.
    # Owner-maintained on the Category Maintenance screen; auto-derived from the name
    # when left blank (see SkuService.code_for_category).
    code: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    # Self-referential — children share same parent_id FK
    children: Mapped[list[ProductCategory]] = relationship(
        "ProductCategory", back_populates="parent"
    )
    parent: Mapped[ProductCategory | None] = relationship(
        "ProductCategory", back_populates="children", remote_side=[id]
    )
    products: Mapped[list[Product]] = relationship("Product", back_populates="category")

    @property
    def full_path(self) -> str:
        if self.parent:
            return f"{self.parent.full_path} → {self.name}"
        return self.name


class Brand(Base):
    """Owner-maintained parts-brand list (PAI, Interstate-McBee, SAMPA, JAK'S).
    §18.2 — Brand is DISTINCT from Vendor (who we buy from) and from
    Manufacturer / Engine Make (the engine platform). Maintained on the
    Inventory → Category Maintenance screen; seeded from constants.BRANDS."""
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_house_brand: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Manufacturer(Base):
    """Owner-maintained Manufacturer / Engine-Make list (Cummins, CAT, Detroit,
    Mack, Volvo, International, Paccar, Mercedes). §18 A1 — the SAME concept as
    Product.engine_manufacturer (one field). DISTINCT from Brand and Vendor.
    Maintained on Category Maintenance; seeded from constants.ENGINE_MAKES."""
    __tablename__ = "manufacturers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    # sku is the JAKS master SKU. vendor-specific SKUs live in product_vendor_sources.
    # For single-sourced products this mirrors the vendor source SKU. For multi-sourced
    # products the preferred vendor source SKU is used here by convention.
    sku: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    manufacturer: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Category ──────────────────────────────────────────────────────────────
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_categories.id"), nullable=True
    )

    # ── JAKS SKU components (frozen at mint; owner-locked scheme 2026-06-06) ──────
    # The customer-facing `sku` is assembled as
    #   JAKS-[engine_code]-[category_code]-[vendor digit][part_seq:04d]
    # These columns store the FROZEN pieces so the assembled sku never drifts if a
    # category is later renamed/re-coded, and so a 2nd vendor's version of the SAME
    # physical part can reuse part_seq with a different vendor digit (readable
    # equivalence: 90001 ↔ 30001). engine_code is '' for multi-fit / engine-agnostic
    # parts — the [ENGINE] segment is then omitted (JAKS-[CATEGORY]-[V][NNNN]).
    engine_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    category_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    part_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProductStatus.ACTIVE
    )
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )

    # ── Pricing ───────────────────────────────────────────────────────────────
    # R11 — MOVING WEIGHTED-AVERAGE COGS cost (Option A, owner-ruled 2026-06-01).
    # Written ONLY by InventoryService._apply_moving_average_cost on PO receipt.
    # DO NOT write product.cost from vendor-source sync — vendor quote price
    # belongs on ProductVendorSource.vendor_cost (per-source, source of truth).
    # See JAKS_UI_Change_Plan.md §8N for the ruling.
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # R11 — most recent receipt unit cost (vs. weighted avg above)
    last_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # "receipt" = set by moving-avg path; "manual" = user-set; "vendor" = legacy (do not set)
    cost_source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    markup_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_of_measure: Mapped[str] = mapped_column(
        String(10), nullable=False, default=UnitOfMeasure.EA
    )

    # ── Core Charges (separate vendor cost vs customer charge — cores are marked up) ──
    has_core: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vendor_core_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    customer_core_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Inventory (CACHED values — InventoryTransaction ledger is source of truth) ──
    # These are updated by InventoryService whenever a transaction is written.
    # Never write to them directly — always go through InventoryService so the
    # ledger and cache stay in sync.  Use ProductService.get_qty_on_hand(id, db)
    # to recalculate from the ledger if you suspect drift.
    qty_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_committed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_on_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # R6 — backordered demand (SO lines without stock or linked PO)
    qty_backordered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_stock_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_lbs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Serial / Kit ──────────────────────────────────────────────────────────
    has_serial_number: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kit_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Return Policy ─────────────────────────────────────────────────────────
    is_returnable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    return_policy_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ProductReturnPolicy.STANDARD
    )
    return_window_override_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restock_fee_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    special_order_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── External IDs ─────────────────────────────────────────────────────────
    shopify_product_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    shopify_variant_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    ebay_listing_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    # ── ESN / Engine Info ─────────────────────────────────────────────────────
    engine_manufacturer: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    engine_model: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Warranty ──────────────────────────────────────────────────────────────
    # is_warrantable: whether JAKS offers any extended warranty on this product.
    # Supplier/vendor warranty = what the vendor includes at no charge.
    # JAKS warranty = extension JAKS sells as a paid upsell (child line on quote).
    # warranty_percentage: price calc base — extended_price = unit_price × pct% × (months÷12)
    is_warrantable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manufacturer_warranty_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supplier_warranty_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supplier_warranty_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="parts_only"
    )
    jaks_warranty_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warranty_percentage: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)

    # ── Phase 2 — extended classification (PAI scraper import / enrichment) ─────
    manufacturer_part_number: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    product_family: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    engine_family: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    truck_make: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    application_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    country_of_origin: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    is_imported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_house_brand: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_performance_part: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dimensions: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    # ── Phase 2 — market pricing references ─────────────────────────────────────
    # price_override (above) = OUR sell price; cost/last_cost = moving-avg COGS;
    # vendor_cost lives on ProductVendorSource. These are market-facing refs only:
    list_price: Mapped[float | None] = mapped_column(Float, nullable=True)         # MSRP / catalog list
    map_price: Mapped[float | None] = mapped_column(Float, nullable=True)          # minimum advertised price
    compare_at_price: Mapped[float | None] = mapped_column(Float, nullable=True)   # Shopify display "was" price
    price_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    price_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Phase 2 — channel / SEO / enrichment provenance ─────────────────────────
    shopify_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    seo_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    seo_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    search_keywords: Mapped[str] = mapped_column(Text, nullable=False, default="")  # scraper Tags
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enrichment_source: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    category: Mapped[ProductCategory | None] = relationship(
        "ProductCategory", back_populates="products"
    )
    superseded_by: Mapped[Product | None] = relationship(
        "Product", remote_side=[id], foreign_keys=[superseded_by_id]
    )
    vendor_sources: Mapped[list[ProductVendorSource]] = relationship(
        "ProductVendorSource", back_populates="product", cascade="all, delete-orphan"
    )
    images: Mapped[list[ProductImage]] = relationship(
        "ProductImage", back_populates="product", cascade="all, delete-orphan"
    )
    cross_references: Mapped[list[CrossReference]] = relationship(
        "CrossReference",
        foreign_keys="CrossReference.product_id",
        primaryjoin="Product.id == CrossReference.product_id",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    applications: Mapped[list[ProductApplication]] = relationship(
        "ProductApplication", back_populates="product", cascade="all, delete-orphan"
    )
    cost_history: Mapped[list[ProductCostHistory]] = relationship(
        "ProductCostHistory", back_populates="product"
    )
    serial_numbers: Mapped[list[ProductSerialNumber]] = relationship(
        "ProductSerialNumber", back_populates="product"
    )
    po_lines: Mapped[list[POLine]] = relationship("POLine", back_populates="product")
    quote_lines: Mapped[list[QuoteLine]] = relationship("QuoteLine", back_populates="product")
    so_lines: Mapped[list[SOLine]] = relationship("SOLine", back_populates="product")
    invoice_lines: Mapped[list[InvoiceLine]] = relationship(
        "InvoiceLine", back_populates="product"
    )
    inventory_transactions: Mapped[list[InventoryTransaction]] = relationship(
        "InventoryTransaction", back_populates="product"
    )
    core_charges: Mapped[list[CoreCharge]] = relationship(
        "CoreCharge", back_populates="product"
    )
    suggested_sells: Mapped[list[SuggestedSell]] = relationship(
        "SuggestedSell",
        foreign_keys="SuggestedSell.product_id",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="SuggestedSell.sort_order",
    )
    # Phase 2 — competitor market prices (NEVER cost; pure market intelligence)
    competitor_prices: Mapped[list["CompetitorPrice"]] = relationship(
        "CompetitorPrice", back_populates="product", cascade="all, delete-orphan",
    )

    # ── Computed properties ───────────────────────────────────────────────────
    @property
    def selling_price(self) -> float:
        """
        Quick estimated sell price using the product's own markup_pct.

        IMPORTANT: a model property has no DB session, so when markup_pct is unset
        this uses a hardcoded 30 % estimate — NOT the default_markup_pct setting.
        For a price that respects the current setting (the O5 path), call:
            PricingService(db).sell_price_for(product)
        which the search results, product CSV export, and pickers now use.
        A 0 % markup is honored as a real value (sell at cost); only an unset
        (NULL) markup falls back to the estimate here.
        """
        if self.price_override and self.price_override > 0:
            return self.price_override
        markup = self.markup_pct if self.markup_pct is not None else 30.0
        return round(self.cost * (1 + markup / 100), 2)

    @property
    def margin_percent(self) -> float | None:
        """Computed gross margin % on the current sell price vs moving-avg cost.
        NOT stored (ruling: margin changes whenever cost or price changes; freeze
        only on quote/invoice lines). Returns None when there is no sell price.
        Reads ~100% for freshly-imported parts until the first PO receipt sets a
        real moving-avg cost — expected, not a defect."""
        sell = self.selling_price
        if not sell or sell <= 0:
            return None
        return round((sell - self.cost) / sell * 100, 1)

    @property
    def qty_available(self) -> int:
        return self.qty_on_hand - self.qty_committed

    @property
    def preferred_vendor_source(self) -> ProductVendorSource | None:
        return next((s for s in self.vendor_sources if s.is_preferred), None)

    @property
    def primary_image(self) -> ProductImage | None:
        return next((img for img in self.images if img.is_primary), None)

    @property
    def core_margin(self) -> float:
        return round(self.customer_core_charge - self.vendor_core_charge, 2)

    @property
    def is_low_stock(self) -> bool:
        return self.qty_on_hand <= self.reorder_point

    @property
    def is_superseded(self) -> bool:
        return self.status == ProductStatus.SUPERSEDED


class ProductVendorSource(Base):
    """
    One row per vendor that supplies this product.
    Replaces the old product.vendor_id single FK.
    Each source has its own vendor SKU, cost, and lead time.
    """
    __tablename__ = "product_vendor_sources"
    __table_args__ = (
        Index("ix_pvs_product_id", "product_id"),
        Index("ix_pvs_product_id_preferred", "product_id", "is_preferred"),
        Index("ix_pvs_vendor_id", "vendor_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)

    vendor_part_number: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # Assembled JAKS SKU for this vendor: JAKS-[VENDOR_CODE]-[PART_NUMBER]
    vendor_sku: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    vendor_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_cost_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    product: Mapped[Product] = relationship("Product", back_populates="vendor_sources")
    vendor: Mapped[Vendor] = relationship("Vendor", back_populates="vendor_sources")


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alt_text: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped[Product] = relationship("Product", back_populates="images")

    @property
    def url(self) -> str:
        """Render-ready <img src>. Imported images store a full external URL
        (e.g. the PAI CDN) and pass through as-is; locally-uploaded files are
        served from /static/. Fixes the '/static/https://…' broken-src bug."""
        fp = self.file_path or ""
        return fp if fp.startswith(("http://", "https://")) else "/static/" + fp


class CrossReference(Base):
    """OEM numbers, competitor part numbers, and vendor alternative numbers."""
    __tablename__ = "cross_references"
    __table_args__ = (
        # ref_number is the quote-screen hot-path search — indexed first
        Index("ix_cross_references_ref_number", "ref_number"),
        Index("ix_cross_references_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    ref_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CrossRefType.OEM
    )
    ref_number: Mapped[str] = mapped_column(String(100), nullable=False)
    brand: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="proven")

    # R5 — bumped on each successful invoice finalization that used this cross-ref.
    # After 3, ResearchService.suggest_promote_to_proven() returns True.
    successful_sale_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # R5 — for obsolete cross-refs, link to the replacement product.
    # Search results display "Obsolete — use replacement [SKU]".
    replacement_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )

    product: Mapped[Product] = relationship(
        "Product", foreign_keys=[product_id], back_populates="cross_references"
    )
    replacement_product: Mapped[Product | None] = relationship(
        "Product", foreign_keys=[replacement_product_id]
    )


class ProductApplication(Base):
    """§7.2 — engine application: which engine make/model (+ CPL, optional ESN
    range) a product fits. ENRICHED from the owner's external catalog CSV; never
    authored in the ERP. Dedup grain = (product_id, engine_make, engine_model,
    cpl) — the locked 'make/model + CPL' level. esn_range is nullable (CPL-level
    apps often have no specific ESN window)."""
    __tablename__ = "product_applications"
    __table_args__ = (
        Index("ix_product_applications_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    engine_make: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    engine_model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    cpl: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # ESN nullable — many CPL-level applications have no specific serial window.
    esn_range: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    product: Mapped[Product] = relationship("Product", back_populates="applications")


class ProductCostHistory(Base):
    """Records every cost change per vendor source for margin accuracy."""
    __tablename__ = "product_cost_history"
    __table_args__ = (
        Index("ix_product_cost_history_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    old_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    new_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    product: Mapped[Product] = relationship("Product", back_populates="cost_history")


class ProductSerialNumber(Base):
    """Serialized unit tracking — cylinder heads and other serialized products."""
    __tablename__ = "product_serial_numbers"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SerialNumberStatus.IN_STOCK
    )
    po_receipt_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("po_receipt_lines.id"), nullable=True
    )
    invoice_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_lines.id"), nullable=True
    )

    product: Mapped[Product] = relationship("Product", back_populates="serial_numbers")


# ── Scaffold tables (architecture placeholder — logic built later) ─────────────

class ProductKit(Base):
    """Kit header — links a kit SKU to its component BOM."""
    __tablename__ = "product_kits"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    kit_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=KitType.CUSTOM_KIT
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    lines: Mapped[list[ProductKitLine]] = relationship(
        "ProductKitLine", back_populates="kit", cascade="all, delete-orphan"
    )


class ProductKitLine(Base):
    """One component in a JAKS-built kit BOM."""
    __tablename__ = "product_kit_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    kit_id: Mapped[int] = mapped_column(ForeignKey("product_kits.id"), nullable=False)
    component_product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    kit: Mapped[ProductKit] = relationship("ProductKit", back_populates="lines")


class SuggestedSell(Base):
    """
    Per-product suggested sell configuration.
    Defines which related products appear as chips below a quote line.

    relationship_type controls chip behavior at quote time:
      recommended — shown as chip, one-click add
      required    — pre-checked in slide-over (install kits, mounting hardware)
      optional    — shown in slide-over only, not as inline chip
      warranty    — shown as chip; click opens the warranty tier picker

    sort_order controls left-to-right chip display order (0 = first).
    """
    __tablename__ = "suggested_sells"
    __table_args__ = (
        Index("ix_suggested_sells_product_id", "product_id"),
        # Prevent duplicate links between the same pair of products
        Index("ix_suggested_sells_pair", "product_id", "suggested_product_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    suggested_product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SuggestedSellType.RECOMMENDED
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped[Product] = relationship(
        "Product", foreign_keys=[product_id], back_populates="suggested_sells"
    )
    suggested_product: Mapped[Product] = relationship(
        "Product", foreign_keys=[suggested_product_id]
    )


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.vendor import Vendor                         # noqa: E402
from app.models.purchase_order import POLine                 # noqa: E402
from app.models.quote import QuoteLine, SOLine               # noqa: E402
from app.models.invoice import InvoiceLine                   # noqa: E402
from app.models.inventory import InventoryTransaction        # noqa: E402
from app.models.core import CoreCharge                       # noqa: E402
