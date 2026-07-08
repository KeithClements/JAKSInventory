"""
tests/smoke/factories.py
========================
Stable seed data for the smoke suite. Idempotent: safe to call on a fresh DB
(creates the fixtures) or an existing one (returns the already-seeded rows).

Design — two DISTINCT products so the two inventory deltas never overlap:
  * SALE product  — seeded with ample on-hand stock; flows through
    quote → sales order → invoice and is DECREMENTED when the invoice is finalized.
  * RECEIVE product — seeded at zero on-hand; a PO is created + received against it
    and it is INCREMENTED. Starting at 0 makes the post-receive assertion exact.

We set ``qty_on_hand`` directly here: every code path the smoke flow exercises
reads/writes the CACHED ``Product.qty_on_hand`` (PO receive does ``+= qty``,
invoice finalize does ``-= qty`` and checks the cached ``qty_available`` property),
so the cache is the source of truth for these flows. The customer is intentionally
NOT seeded — creating it is smoke step 1.

A vendor (+ preferred vendor sources) is seeded so the PO vendor picker is
non-empty and the PO add-line can auto-fill cost.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.constants import PaymentTerms, ProductStatus
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor

# ── Stable identifiers (distinctive tokens so product search is unambiguous) ──
VENDOR_NAME = "SMOKE Test Vendor"
VENDOR_CODE = "SMK"

SALE_SKU = "SMOKE-SALE-001"
SALE_TITLE = "SMOKE Sale Widget (smoke-suite fixture)"
SALE_START_QTY = 100          # ample stock so finalize never hits the shortage guard
SALE_COST = 50.0

RECV_SKU = "SMOKE-RECV-001"
RECV_TITLE = "SMOKE Receive Widget (smoke-suite fixture)"
RECV_START_QTY = 0            # start at zero → post-receive on-hand == received qty
RECV_COST = 30.0


@dataclass
class SeededData:
    vendor_id: int
    sale_product_id: int
    sale_sku: str
    recv_product_id: int
    recv_sku: str


def _get_or_create_vendor(db) -> Vendor:
    v = db.query(Vendor).filter(Vendor.name == VENDOR_NAME).first()
    if v is None:
        v = Vendor(
            name=VENDOR_NAME,
            vendor_code=VENDOR_CODE,
            contact_name="Sales Desk",
            phone="555-0142",
            email="sales@smoke-vendor.example",
            account_number="SMK-0001",
            payment_terms=PaymentTerms.NET_30,
            is_active=True,
        )
        db.add(v)
        db.flush()
    return v


def _get_or_create_product(db, *, sku, title, cost, qty_on_hand) -> Product:
    p = db.query(Product).filter(Product.sku == sku).first()
    if p is None:
        p = Product(
            sku=sku,
            title=title,
            description=title,
            manufacturer="SMOKE",
            cost=cost,
            last_cost=cost,
            markup_pct=40.0,
            qty_on_hand=qty_on_hand,
            qty_committed=0,
            qty_on_order=0,
            reorder_point=1,
            status=ProductStatus.ACTIVE,
            is_active=True,
        )
        db.add(p)
        db.flush()
    return p


def _ensure_vendor_source(db, *, product: Product, vendor: Vendor, cost: float) -> None:
    exists = (
        db.query(ProductVendorSource)
        .filter(
            ProductVendorSource.product_id == product.id,
            ProductVendorSource.vendor_id == vendor.id,
        )
        .first()
    )
    if exists is None:
        db.add(ProductVendorSource(
            product_id=product.id,
            vendor_id=vendor.id,
            vendor_part_number=product.sku,
            vendor_sku=product.sku,
            vendor_cost=cost,
            is_preferred=True,
            is_active=True,
        ))


def seed(SessionLocal) -> SeededData:
    """Seed the stable fixtures and return their ids/SKUs."""
    db = SessionLocal()
    try:
        vendor = _get_or_create_vendor(db)
        sale = _get_or_create_product(
            db, sku=SALE_SKU, title=SALE_TITLE, cost=SALE_COST, qty_on_hand=SALE_START_QTY
        )
        recv = _get_or_create_product(
            db, sku=RECV_SKU, title=RECV_TITLE, cost=RECV_COST, qty_on_hand=RECV_START_QTY
        )
        _ensure_vendor_source(db, product=sale, vendor=vendor, cost=SALE_COST)
        _ensure_vendor_source(db, product=recv, vendor=vendor, cost=RECV_COST)
        db.commit()
        return SeededData(
            vendor_id=vendor.id,
            sale_product_id=sale.id,
            sale_sku=sale.sku,
            recv_product_id=recv.id,
            recv_sku=recv.sku,
        )
    finally:
        db.close()


def get_qty_on_hand(SessionLocal, product_id: int) -> int:
    """Read a product's current cached on-hand quantity straight from the DB —
    the most reliable inventory check, immune to UI rendering quirks."""
    db = SessionLocal()
    try:
        p = db.query(Product).filter(Product.id == product_id).first()
        return int(p.qty_on_hand) if p else -1
    finally:
        db.close()


def sum_invoice_product_qty(SessionLocal, invoice_id: int, product_id: int) -> int:
    """Sum the ordered quantity of a given product across an invoice's product lines.
    Used to compute the exact expected finalize decrement regardless of how many
    lines reference the product."""
    from app.models.invoice import InvoiceLine

    db = SessionLocal()
    try:
        lines = (
            db.query(InvoiceLine)
            .filter(
                InvoiceLine.invoice_id == invoice_id,
                InvoiceLine.product_id == product_id,
            )
            .all()
        )
        return int(sum((ln.qty or 0) for ln in lines))
    finally:
        db.close()
