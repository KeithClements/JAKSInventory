"""
tests/test_workflow_series2.py
==============================
Smoke tests for Backend Workflow Series 2:
  - Payment NSF, allocate, de-allocate routes
  - PO receive status guard (rejects non-SENT/PARTIAL)
  - PO receive condition_notes captured on POReceiptLine
  - Core vendor-credit-difference route

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_workflow_series2.py -v

Uses an in-memory SQLite DB isolated from the live data/jaks.db.
The module-level patch of app.database must happen before any app imports.
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# ── Patch BEFORE any app.* imports ──────────────────────────────────────────
import app.database as _appdb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# StaticPool forces every Session to reuse ONE shared in-memory connection,
# so that Base.metadata.create_all() and the test + route sessions all see
# the same tables. Without StaticPool, each new connection would start with
# a fresh empty database.
_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_appdb.engine = _TEST_ENGINE
_appdb.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

# Register all models and create tables
from app.models import __all_models__  # noqa: F401
from app.database import Base

Base.metadata.create_all(bind=_TEST_ENGINE)

# ── App imports (safe after patch) ────────────────────────────────────────────
import pytest
from fastapi.testclient import TestClient

from app.constants import (
    CoreDenialResolution,
    CoreDirection,
    CoreStatus,
    CoreVendorStatus,
    FulfillmentSource,
    InvoiceStatus,
    LineType,
    PaymentMethod,
    PaymentStatus,
    POStatus,
    SOLineStatus,
    SOPaymentMode,
    SOStatus,
    UserRole,
)
from app.deps import get_db
from app.models.core import CoreCharge
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment, PaymentAllocation
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine, POReceiptLine
from app.models.quote import SalesOrder, SOLine
from app.models.user import User
from app.models.vendor import Vendor
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.sales_order_service import SalesOrderService

# ── FastAPI app + dependency override ────────────────────────────────────────
# Import app AFTER patching _appdb so startup uses the test engine.
# Then override get_db so route handlers also use the test session factory.
from app.main import app as _fastapi_app


def _override_get_db():
    """Yield a session from the in-memory test engine instead of the real DB."""
    session = _appdb.SessionLocal()
    try:
        yield session
    finally:
        session.close()


_fastapi_app.dependency_overrides[get_db] = _override_get_db

_client = TestClient(_fastapi_app, raise_server_exceptions=False)

# ── Shared counter for unique identifiers ─────────────────────────────────────
_counter = itertools.count(1)
_UID = 1  # stub current_user_id (admin user id=1)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _activate_db():
    """Re-point app DB globals + get_db at THIS module's engine at run time,
    so the suite is immune to import order. See tests/conftest.py."""
    from tests.conftest import activate
    activate(_TEST_ENGINE)
    yield


@pytest.fixture()
def db(_activate_db):
    """Session backed by the module-level in-memory engine."""
    session = _appdb.SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True, scope="module")
def _seed_admin_user(_activate_db):
    """Seed the admin user (id=1) once per module — required for permission checks."""
    session = _appdb.SessionLocal()
    try:
        if not session.query(User).filter(User.id == 1).first():
            session.add(User(
                id=1,
                name="Test Admin",
                username="admin",
                password_hash="[test-no-auth]",
                role=UserRole.ADMIN,
            ))
            session.commit()
    finally:
        session.close()


# ── Data builders ─────────────────────────────────────────────────────────────

def _make_customer(db) -> Customer:
    c = Customer(company_name=f"TestCo-{next(_counter)}", is_active=True)
    db.add(c)
    db.flush()
    return c


def _make_product(db, qty_on_hand: int = 10) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"SKU-S2-{n:06d}",
        title=f"Widget-S2 {n}",
        qty_on_hand=qty_on_hand,
        qty_committed=0,
        cost=10.0,
    )
    db.add(p)
    db.flush()
    return p


def _make_vendor(db) -> Vendor:
    n = next(_counter)
    v = Vendor(name=f"Vendor-{n}", vendor_code=f"V{n:03d}"[:4])
    db.add(v)
    db.flush()
    return v


def _make_so(db, customer: Customer) -> SalesOrder:
    n = next(_counter)
    so = SalesOrder(
        so_number=f"SO-S2-{n:04d}",
        customer_id=customer.id,
        status=SOStatus.OPEN,
        payment_mode=SOPaymentMode.NONE,
        deposit_amount=0.0,
        notes="",
        internal_notes="",
    )
    db.add(so)
    db.flush()
    return so


def _make_so_line(
    db, so: SalesOrder, product: Product, qty: int = 2, price: float = 100.0
) -> SOLine:
    line = SOLine(
        so_id=so.id,
        product_id=product.id,
        line_type=LineType.PRODUCT,
        description=product.title,
        qty_ordered=qty,
        qty_committed=qty,
        qty_fulfilled=0,
        qty_invoiced=0,
        unit_price=price,
        unit_cost=product.cost,
        discount_pct=0.0,
        core_charge=0.0,
        source="stock",
        fulfillment_source=FulfillmentSource.STOCK,
        line_status=SOLineStatus.RESERVED_STOCK,
        sort_order=0,
    )
    product.qty_committed += qty
    db.add(line)
    db.flush()
    return line


def _make_open_invoice(db) -> Invoice:
    """Create an OPEN invoice via SO fulfillment."""
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=10)
    so = _make_so(db, cust)
    line = _make_so_line(db, so, prod, qty=2, price=100.0)
    db.commit()
    inv = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 2})
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _make_draft_po(db, vendor: Vendor, product: Product, qty: int = 5):
    """Create a DRAFT PO with one line. Returns (PurchaseOrder, POLine)."""
    n = next(_counter)
    po = PurchaseOrder(
        po_number=f"PO-S2-{n:04d}",
        vendor_id=vendor.id,
        status=POStatus.DRAFT,
        freight_in_cost=0.0,
        notes="",
        internal_notes="",
    )
    db.add(po)
    db.flush()
    line = POLine(
        po_id=po.id,
        product_id=product.id,
        description=product.title,
        qty_ordered=qty,
        qty_received=0,
        qty_billed=0,
        unit_cost=product.cost,
        core_charge_per_unit=0.0,
        notes="",
    )
    db.add(line)
    db.commit()
    return po, line


def _make_sent_po(db, vendor: Vendor, product: Product, qty: int = 5):
    """Create a SENT PO (ready to receive). Returns (PurchaseOrder, POLine)."""
    po, line = _make_draft_po(db, vendor, product, qty)
    po.status = POStatus.SENT
    db.commit()
    return po, line


def _make_core_charge(db, customer: Customer, product: Product, vendor: Vendor) -> CoreCharge:
    """Create a minimal CoreCharge in SHIPPED_TO_VENDOR status."""
    core = CoreCharge(
        direction=CoreDirection.CUSTOMER_OWES_RETURN,
        customer_id=customer.id,
        vendor_id=vendor.id,
        product_id=product.id,
        qty_charged=1,
        qty_returned=1,
        vendor_unit_charge=50.0,
        customer_unit_charge=60.0,
        status=CoreStatus.SHIPPED_TO_VENDOR,
        vendor_status=CoreVendorStatus.PENDING,
        notes="",
    )
    db.add(core)
    db.commit()
    return core


# ══════════════════════════════════════════════════════════════════════════════
# Payment Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPaymentNSF:

    def test_nsf_route_reverses_payment(self, db):
        """POST /payments/{id}/nsf → payment status NSF, redirects to /payments/{id}."""
        inv = _make_open_invoice(db)
        pmt = PaymentService(db, _UID).record_payment(
            customer_id=inv.customer_id,
            amount_received=inv.total,
            payment_method=PaymentMethod.CHECK,
            data={"notes": "check payment"},
            invoice_ids=[inv.id],
        )
        db.expire_all()
        pmt_id = pmt.id

        resp = _client.post(
            f"/payments/{pmt_id}/nsf",
            data={"nsf_fee": "35"},
            follow_redirects=False,
        )

        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        assert f"/payments/{pmt_id}" in resp.headers["location"]

        db.expire_all()
        pmt_r = db.query(Payment).filter(Payment.id == pmt_id).first()
        assert pmt_r is not None
        assert pmt_r.status == PaymentStatus.NSF, f"Expected NSF, got {pmt_r.status}"


class TestPaymentAllocate:

    def test_allocate_route_applies_payment(self, db):
        """POST /payments/{id}/allocate with invoice_id and amount → allocation created."""
        inv = _make_open_invoice(db)
        # Create an unapplied payment (no invoice_ids passed)
        pmt = PaymentService(db, _UID).record_payment(
            customer_id=inv.customer_id,
            amount_received=inv.total,
            payment_method=PaymentMethod.CHECK,
            data={"notes": "unapplied"},
            invoice_ids=None,
        )
        db.expire_all()
        pmt_id = pmt.id
        inv_id = inv.id
        inv_total = inv.total

        # Verify no allocations before the route call
        pmt_before = db.query(Payment).filter(Payment.id == pmt_id).first()
        assert pmt_before.amount_allocated == 0.0

        resp = _client.post(
            f"/payments/{pmt_id}/allocate",
            data={"invoice_id": str(inv_id), "amount": str(inv_total)},
            follow_redirects=False,
        )

        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        assert f"/payments/{pmt_id}" in resp.headers["location"]

        db.expire_all()
        pmt_r = db.query(Payment).filter(Payment.id == pmt_id).first()
        assert pmt_r.amount_allocated > 0, "Allocation should have been created"
        assert abs(pmt_r.amount_allocated - inv_total) < 0.01

    def test_deallocate_route_removes_allocation(self, db):
        """POST /payments/{id}/allocations/{alloc_id}/remove → alloc removed."""
        inv = _make_open_invoice(db)
        # Record payment allocated to invoice in one step
        pmt = PaymentService(db, _UID).record_payment(
            customer_id=inv.customer_id,
            amount_received=inv.total,
            payment_method=PaymentMethod.CHECK,
            data={"notes": "allocated"},
            invoice_ids=[inv.id],
        )
        db.expire_all()
        pmt_id = pmt.id

        pmt_r = db.query(Payment).filter(Payment.id == pmt_id).first()
        allocs = [a for a in pmt_r.allocations if not a.is_reversed]
        assert len(allocs) == 1, f"Expected 1 active allocation, found {len(allocs)}"
        alloc_id = allocs[0].id

        resp = _client.post(
            f"/payments/{pmt_id}/allocations/{alloc_id}/remove",
            follow_redirects=False,
        )

        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        assert f"/payments/{pmt_id}" in resp.headers["location"]

        db.expire_all()
        alloc = db.query(PaymentAllocation).filter(PaymentAllocation.id == alloc_id).first()
        assert alloc is None, "Allocation row should have been deleted"


# ══════════════════════════════════════════════════════════════════════════════
# PO Receive Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPOReceive:

    def test_receive_blocked_on_draft_po(self, db):
        """POST /purchase-orders/{id}/receive on DRAFT PO → redirects with error."""
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_draft_po(db, vendor, product, qty=3)
        po_id = po.id
        line_id = line.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/receive",
            data={f"recv_{line_id}": "3"},
            follow_redirects=False,
        )

        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        location = resp.headers["location"]
        assert "error" in location, f"Expected error redirect, got: {location}"

        # Verify no receipt lines were created
        db.expire_all()
        receipt_lines = (
            db.query(POReceiptLine)
            .filter(POReceiptLine.po_id == po_id)
            .all()
        )
        assert len(receipt_lines) == 0, "No receipt lines should be created for DRAFT PO"

    def test_receive_condition_notes_saved(self, db):
        """POST with condition_{line_id}=damaged → POReceiptLine.condition_notes set."""
        vendor = _make_vendor(db)
        product = _make_product(db, qty_on_hand=10)
        po, line = _make_sent_po(db, vendor, product, qty=5)
        po_id = po.id
        line_id = line.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/receive",
            data={
                f"recv_{line_id}": "5",
                f"condition_{line_id}": "damaged",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303, (
            f"Expected 303, got {resp.status_code}: {resp.headers.get('location')}"
        )

        db.expire_all()
        receipt_line = (
            db.query(POReceiptLine)
            .filter(POReceiptLine.po_line_id == line_id)
            .first()
        )
        assert receipt_line is not None, "POReceiptLine should have been created"
        assert receipt_line.condition_notes == "damaged", (
            f"Expected condition_notes='damaged', got {receipt_line.condition_notes!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Core Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCoreVendorCreditDiff:

    def test_vendor_credit_diff_route(self, db):
        """POST /cores/{id}/vendor-credit-difference → redirects to /cores/."""
        customer = _make_customer(db)
        product = _make_product(db)
        vendor = _make_vendor(db)
        core = _make_core_charge(db, customer, product, vendor)
        core_id = core.id

        resp = _client.post(
            f"/cores/{core_id}/vendor-credit-difference",
            data={
                "actual_credit": "40.00",
                "resolution": CoreDenialResolution.ABSORBED_BY_JAKS,
                "notes": "vendor paid less than expected",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        location = resp.headers["location"]
        assert "/cores/" in location, f"Expected redirect to /cores/, got: {location}"
        assert "error" not in location, f"Got error redirect: {location}"

        db.expire_all()
        core_r = db.query(CoreCharge).filter(CoreCharge.id == core_id).first()
        assert core_r.denial_resolution == CoreDenialResolution.ABSORBED_BY_JAKS
        assert core_r.denial_notes == "vendor paid less than expected"
