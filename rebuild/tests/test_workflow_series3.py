"""
tests/test_workflow_series3.py
==============================
Smoke tests for Backend Workflow Series 3:
  - VendorReturnService: create, ship, record decision, close
  - Vendor return HTTP routes (list, new form)
  - ReportService.get_overdue_invoices
  - ReportService.get_sales_tax_collected
  - Report HTTP routes (overdue-invoices, sales-tax)

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_workflow_series3.py -v

Uses an in-memory SQLite DB isolated from the live data/jaks.db.
The module-level patch of app.database must happen before any app imports.
"""
import itertools
import pathlib
import sys
from datetime import date, datetime, timedelta

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
    InvoiceStatus,
    LineType,
    UserRole,
    VendorReturnLineOutcome,
    VendorReturnStatus,
)
from app.deps import get_db
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_credit import VendorCreditMemo
from app.models.vendor_return import VendorReturn
from app.services.report_service import ReportService
from app.services.vendor_return_service import VendorReturnService

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

@pytest.fixture()
def db():
    """Session backed by the module-level in-memory engine."""
    session = _appdb.SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True, scope="module")
def _seed_admin_user():
    """Seed the admin user (id=1) once per module — required for permission checks."""
    session = _appdb.SessionLocal()
    try:
        if not session.query(User).filter(User.id == 1).first():
            session.add(User(
                id=1,
                name="Test Admin",
                username="admin_s3",
                password_hash="[test-no-auth]",
                role=UserRole.ADMIN,
            ))
            session.commit()
    finally:
        session.close()


# ── Data builders ─────────────────────────────────────────────────────────────

def _make_customer(db, interest_rate: float = 0.0, interest_grace_days: int = 10) -> Customer:
    n = next(_counter)
    c = Customer(
        company_name=f"TestCo-S3-{n}",
        is_active=True,
        interest_rate=interest_rate,
        interest_grace_days=interest_grace_days,
    )
    db.add(c)
    db.flush()
    return c


def _make_product(db, qty_on_hand: int = 10) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"VR-TEST-{n:06d}",
        title=f"Widget-S3 {n}",
        qty_on_hand=qty_on_hand,
        qty_committed=0,
        cost=10.0,
    )
    db.add(p)
    db.flush()
    return p


def _make_vendor(db) -> Vendor:
    n = next(_counter)
    v = Vendor(name=f"Vendor-S3-{n}", vendor_code=f"V{n:03d}"[:4])
    db.add(v)
    db.flush()
    return v


def _make_open_invoice(
    db,
    customer: Customer,
    days_overdue: int = 30,
    balance: float = 200.0,
    tax_amount: float = 0.0,
) -> Invoice:
    """Create a minimal OPEN invoice with a past due_date and a positive balance."""
    n = next(_counter)
    due = datetime.utcnow() - timedelta(days=days_overdue)
    inv = Invoice(
        invoice_number=f"INV-S3-{n:06d}",
        customer_id=customer.id,
        status=InvoiceStatus.OPEN,
        due_date=due,
        is_taxable=False,
        tax_rate=0.0,
    )
    db.add(inv)
    db.flush()

    # Add a product line so the invoice has a non-zero total (= balance_due)
    line = InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Test part",
        qty=1,
        unit_price=balance,
        unit_cost=5.0,
        discount_pct=0.0,
        is_taxable=False,
        tax_amount=tax_amount,
        sort_order=0,
    )
    db.add(line)
    db.commit()
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


# ══════════════════════════════════════════════════════════════════════════════
# VendorReturn Service Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestVendorReturnService:

    def test_create_vendor_return_draft(self, db):
        """create_vendor_return() → status=DRAFT, vr_number starts with 'VR-'."""
        vendor = _make_vendor(db)
        product = _make_product(db)

        vr = VendorReturnService(db, _UID).create_vendor_return(
            vendor_id=vendor.id,
            lines=[{
                "product_id": product.id,
                "description": "Wrong part received",
                "qty": 2,
                "expected_unit_credit": 10.0,
            }],
            reason="Wrong part ordered",
        )

        assert vr.id is not None
        assert vr.status == VendorReturnStatus.DRAFT
        assert vr.vr_number.startswith("VR-"), (
            f"Expected vr_number to start with 'VR-', got {vr.vr_number!r}"
        )
        assert vr.vendor_id == vendor.id
        assert len(vr.lines) == 1
        assert vr.expected_credit == pytest.approx(20.0)

    def test_ship_return_changes_status(self, db):
        """ship_return() → DRAFT → SHIPPED, tracking saved, inventory decremented."""
        vendor = _make_vendor(db)
        product = _make_product(db, qty_on_hand=5)
        db.commit()
        initial_qty = product.qty_on_hand

        vr = VendorReturnService(db, _UID).create_vendor_return(
            vendor_id=vendor.id,
            lines=[{
                "product_id": product.id,
                "description": "Defective part",
                "qty": 2,
                "expected_unit_credit": 15.0,
            }],
            reason="Defective on arrival",
        )

        vr_id = vr.id
        vrs = VendorReturnService(db, _UID).ship_return(
            vr_id=vr_id,
            tracking_number="1Z999AA10123456784",
            rma_number="RMA-001",
            decrement_inventory=True,
        )

        assert vrs.status == VendorReturnStatus.SHIPPED
        assert vrs.tracking_number == "1Z999AA10123456784"
        assert vrs.rma_number == "RMA-001"

        # Verify inventory was decremented
        db.expire_all()
        product_r = db.query(Product).filter(Product.id == product.id).first()
        assert product_r.qty_on_hand == initial_qty - 2, (
            f"Expected qty_on_hand={initial_qty - 2}, got {product_r.qty_on_hand}"
        )

    def test_ship_requires_tracking(self, db):
        """ship_return() with empty tracking_number raises ValueError."""
        vendor = _make_vendor(db)
        product = _make_product(db)

        vr = VendorReturnService(db, _UID).create_vendor_return(
            vendor_id=vendor.id,
            lines=[{
                "product_id": product.id,
                "description": "Defective",
                "qty": 1,
                "expected_unit_credit": 5.0,
            }],
            reason="Defective",
        )

        with pytest.raises(ValueError, match="[Tt]racking"):
            VendorReturnService(db, _UID).ship_return(
                vr_id=vr.id,
                tracking_number="",  # empty — should raise
            )

    def test_record_decision_accepted(self, db):
        """record_vendor_decision() → SHIPPED → ACCEPTED, VCM auto-created, actual_credit set."""
        vendor = _make_vendor(db)
        product = _make_product(db, qty_on_hand=10)

        vr = VendorReturnService(db, _UID).create_vendor_return(
            vendor_id=vendor.id,
            lines=[{
                "product_id": product.id,
                "description": "Wrong part",
                "qty": 1,
                "expected_unit_credit": 50.0,
            }],
            reason="Wrong part",
        )
        vr_id = vr.id
        line_id = vr.lines[0].id

        VendorReturnService(db, _UID).ship_return(
            vr_id=vr_id,
            tracking_number="TRACK-001",
            decrement_inventory=False,
        )

        vrd = VendorReturnService(db, _UID).record_vendor_decision(
            vr_id=vr_id,
            line_outcomes=[{
                "line_id": line_id,
                "outcome": VendorReturnLineOutcome.ACCEPTED,
                "actual_unit_credit": 50.0,
            }],
            notes="Vendor accepted full credit",
            auto_create_vcm=True,
        )

        assert vrd.status == VendorReturnStatus.ACCEPTED
        assert vrd.actual_credit == pytest.approx(50.0)

        # VCM should have been auto-created
        db.expire_all()
        vcm = (
            db.query(VendorCreditMemo)
            .filter(VendorCreditMemo.vendor_return_id == vr_id)
            .first()
        )
        assert vcm is not None, "Auto-created VCM should exist for ACCEPTED return"
        assert vcm.total_amount == pytest.approx(50.0)

    def test_close_return(self, db):
        """close_vendor_return() → ACCEPTED → CLOSED."""
        vendor = _make_vendor(db)
        product = _make_product(db, qty_on_hand=10)

        vr = VendorReturnService(db, _UID).create_vendor_return(
            vendor_id=vendor.id,
            lines=[{
                "product_id": product.id,
                "description": "Overcharge",
                "qty": 1,
                "expected_unit_credit": 25.0,
            }],
            reason="Overcharge",
        )
        vr_id = vr.id
        line_id = vr.lines[0].id

        VendorReturnService(db, _UID).ship_return(
            vr_id=vr_id,
            tracking_number="TRACK-CLOSE-001",
            decrement_inventory=False,
        )
        VendorReturnService(db, _UID).record_vendor_decision(
            vr_id=vr_id,
            line_outcomes=[{
                "line_id": line_id,
                "outcome": VendorReturnLineOutcome.ACCEPTED,
                "actual_unit_credit": 25.0,
            }],
            auto_create_vcm=False,
        )

        vrc = VendorReturnService(db, _UID).close_vendor_return(vr_id=vr_id)
        assert vrc.status == VendorReturnStatus.CLOSED


# ══════════════════════════════════════════════════════════════════════════════
# Vendor Return HTTP Routes
# ══════════════════════════════════════════════════════════════════════════════

class TestVendorReturnRoutes:

    def test_list_route_200(self):
        """GET /vendor-returns/ returns 200."""
        resp = _client.get("/vendor-returns/")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}"
        )

    def test_new_form_200(self):
        """GET /vendor-returns/new returns 200."""
        resp = _client.get("/vendor-returns/new")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# ReportService — Overdue Invoices
# ══════════════════════════════════════════════════════════════════════════════

class TestReportServiceOverdue:

    def test_overdue_invoices_empty(self, db):
        """No eligible invoices → rows=[], all totals zero."""
        # Use a future as_of date far in the past so no test-created invoices bleed in
        # Actually use a very-old date so existing invoices have future due dates.
        # Better: use a specific date before any test data.
        as_of = date(2000, 1, 1)
        result = ReportService(db).get_overdue_invoices(as_of_date=as_of)

        assert result["rows"] == [], f"Expected empty rows, got {result['rows']}"
        assert result["totals"]["invoice_count"] == 0
        assert result["totals"]["balance_due"] == 0.0
        assert result["totals"]["interest_accrued"] == 0.0
        assert result["totals"]["total_owed"] == 0.0

    def test_overdue_invoice_appears(self, db):
        """OPEN invoice 30 days past due appears with days_overdue=30, interest=0."""
        customer = _make_customer(db, interest_rate=0.0)
        inv = _make_open_invoice(db, customer, days_overdue=30, balance=200.0)

        # Use today as as_of so the invoice (due 30 days ago) is overdue
        as_of = date.today()
        result = ReportService(db).get_overdue_invoices(as_of_date=as_of)

        inv_ids = [r["invoice"].id for r in result["rows"]]
        assert inv.id in inv_ids, (
            f"Invoice {inv.id} not found in overdue rows. Rows: {inv_ids}"
        )

        row = next(r for r in result["rows"] if r["invoice"].id == inv.id)
        assert row["days_overdue"] >= 29, (
            f"Expected days_overdue ~30, got {row['days_overdue']}"
        )
        assert row["balance_due"] == pytest.approx(200.0)
        assert row["interest_accrued"] == pytest.approx(0.0), (
            "No interest expected when interest_rate=0"
        )

    def test_overdue_interest_calculated(self, db):
        """With interest_rate=1.5 and grace=10, interest_accrued > 0 for 30-day overdue."""
        customer = _make_customer(db, interest_rate=1.5, interest_grace_days=10)
        inv = _make_open_invoice(db, customer, days_overdue=30, balance=1000.0)

        as_of = date.today()
        result = ReportService(db).get_overdue_invoices(as_of_date=as_of)

        row = next(
            (r for r in result["rows"] if r["invoice"].id == inv.id), None
        )
        assert row is not None, f"Invoice {inv.id} not found in overdue rows"

        # days_overdue ~ 30, grace=10, rate=1.5
        # daily_rate = 1.5 / 100 / 30 = 0.0005
        # interest = 1000 * 0.0005 * (30 - 10) = 10.0
        assert row["interest_accrued"] > 0, (
            f"Expected interest > 0 with rate=1.5, got {row['interest_accrued']}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# ReportService — Sales Tax Collected
# ══════════════════════════════════════════════════════════════════════════════

class TestReportServiceTax:

    def test_sales_tax_empty(self, db):
        """No invoices with tax → rows=[], totals all zero."""
        # Date range in the past — before any test invoices
        start = date(1999, 1, 1)
        end = date(1999, 12, 31)
        result = ReportService(db).get_sales_tax_collected(start, end)

        assert result["rows"] == [], f"Expected empty rows, got {result['rows']}"
        assert result["totals"]["invoice_count"] == 0
        assert result["totals"]["taxable_revenue"] == 0.0
        assert result["totals"]["tax_collected"] == 0.0

    def test_sales_tax_collected(self, db):
        """Finalized invoice with tax_amount > 0 on lines → appears in tax report."""
        customer = _make_customer(db)
        # Create an OPEN invoice (finalized) with a taxed line
        inv = _make_open_invoice(
            db,
            customer,
            days_overdue=0,  # Not overdue — just a normal invoice
            balance=500.0,
            tax_amount=40.0,  # $40 tax on the line
        )
        # Give it a non-past due_date so it won't interfere with overdue tests
        inv.due_date = datetime.utcnow() + timedelta(days=30)
        db.commit()
        db.expire_all()

        # Refresh to get current created_at
        inv_r = db.query(Invoice).filter(Invoice.id == inv.id).first()
        inv_created = inv_r.created_at
        if isinstance(inv_created, datetime):
            inv_date = inv_created.date()
        else:
            inv_date = inv_created

        start = inv_date - timedelta(days=1)
        end = inv_date + timedelta(days=1)

        result = ReportService(db).get_sales_tax_collected(start, end)

        inv_ids = [r["invoice"].id for r in result["rows"]]
        assert inv.id in inv_ids, (
            f"Invoice {inv.id} not found in tax rows. Rows: {inv_ids}"
        )

        row = next(r for r in result["rows"] if r["invoice"].id == inv.id)
        assert row["tax_collected"] == pytest.approx(40.0), (
            f"Expected tax_collected=40.0, got {row['tax_collected']}"
        )
        assert result["totals"]["tax_collected"] >= 40.0


# ══════════════════════════════════════════════════════════════════════════════
# Report HTTP Routes
# ══════════════════════════════════════════════════════════════════════════════

class TestReportRoutes:

    def test_overdue_report_route_200(self):
        """GET /reports/overdue-invoices returns 200."""
        resp = _client.get("/reports/overdue-invoices")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}"
        )

    def test_sales_tax_report_route_200(self):
        """GET /reports/sales-tax returns 200."""
        resp = _client.get("/reports/sales-tax")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Inventory Adjustment — Service + HTTP
# ══════════════════════════════════════════════════════════════════════════════

from app.models.inventory import InventoryTransaction  # noqa: E402
from app.services.inventory_service import InventoryService  # noqa: E402
from app.models.quote import LostSaleLog  # noqa: E402


class TestInventoryAdjustment:

    def test_adjust_inventory_add(self, db):
        """adjust_inventory(+3) increments qty_on_hand and creates an InventoryTransaction."""
        product = _make_product(db, qty_on_hand=5)
        db.commit()

        txn = InventoryService(db, current_user_id=_UID).adjust_inventory(
            product_id=product.id,
            qty_delta=3,
            reason="found",
            note="test note",
        )

        db.expire_all()
        product_r = db.query(Product).filter(Product.id == product.id).first()
        assert product_r.qty_on_hand == 8, (
            f"Expected qty_on_hand=8, got {product_r.qty_on_hand}"
        )
        assert txn is not None
        assert txn.qty_change == 3
        assert txn.qty_after == 8

        txn_r = db.query(InventoryTransaction).filter(
            InventoryTransaction.id == txn.id
        ).first()
        assert txn_r is not None, "InventoryTransaction row not found in DB"

    def test_adjust_inventory_remove(self, db):
        """adjust_inventory(-4) decrements qty_on_hand correctly."""
        product = _make_product(db, qty_on_hand=10)
        db.commit()

        txn = InventoryService(db, current_user_id=_UID).adjust_inventory(
            product_id=product.id,
            qty_delta=-4,
            reason="lost",
            note="",
        )

        db.expire_all()
        product_r = db.query(Product).filter(Product.id == product.id).first()
        assert product_r.qty_on_hand == 6, (
            f"Expected qty_on_hand=6, got {product_r.qty_on_hand}"
        )
        assert txn.qty_change == -4
        assert txn.qty_after == 6

    def test_adjust_zero_rejected(self, db):
        """POST /products/{id}/adjust-inventory with qty_delta=0 redirects with ?error=."""
        product = _make_product(db, qty_on_hand=5)
        db.commit()

        resp = _client.post(
            f"/products/{product.id}/adjust-inventory",
            data={"qty_delta": "0", "reason": "found", "note": ""},
            follow_redirects=False,
        )
        # Should be a redirect (303) to /products/{id}?error=...
        assert resp.status_code == 303, (
            f"Expected 303 redirect, got {resp.status_code}"
        )
        location = resp.headers.get("location", "")
        assert "error=" in location, (
            f"Expected ?error= in redirect location, got {location!r}"
        )

    def test_adjust_route_200(self, db):
        """POST /products/{id}/adjust-inventory with valid data redirects to /products/{id}?ok=."""
        product = _make_product(db, qty_on_hand=5)
        db.commit()

        resp = _client.post(
            f"/products/{product.id}/adjust-inventory",
            data={"qty_delta": "1", "reason": "found", "note": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"Expected 303 redirect, got {resp.status_code}"
        )
        location = resp.headers.get("location", "")
        assert f"/products/{product.id}" in location, (
            f"Expected redirect to /products/{product.id}, got {location!r}"
        )
        assert "ok=" in location, (
            f"Expected ?ok= in redirect location, got {location!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Lost Sales Report — Service Layer Only
# ══════════════════════════════════════════════════════════════════════════════

class TestLostSalesReport:

    def test_lost_sales_empty(self, db):
        """Empty date range → rows=[], totals.count=0."""
        result = ReportService(db).get_lost_sales(
            start_date=date(1990, 1, 1),
            end_date=date(1990, 12, 31),
        )

        assert result["rows"] == [], f"Expected empty rows, got {result['rows']}"
        assert result["totals"]["count"] == 0

    def test_lost_sales_row(self, db):
        """A LostSaleLog created directly appears in get_lost_sales with correct fields."""
        customer = _make_customer(db)
        product = _make_product(db)
        db.commit()

        logged_dt = datetime(2025, 6, 15, 12, 0, 0)
        log_entry = LostSaleLog(
            customer_id=customer.id,
            product_id=product.id,
            reason="price",
            competitor_name="AcmeParts",
            competitor_price=49.99,
            notes="Lost on price",
            logged_at=logged_dt,
        )
        db.add(log_entry)
        db.commit()
        db.expire_all()

        result = ReportService(db).get_lost_sales(
            start_date=date(2025, 6, 1),
            end_date=date(2025, 6, 30),
        )

        log_ids = [r["log"].id for r in result["rows"]]
        assert log_entry.id in log_ids, (
            f"LostSaleLog {log_entry.id} not found in rows. IDs: {log_ids}"
        )

        row = next(r for r in result["rows"] if r["log"].id == log_entry.id)
        assert row["reason"] == "price", f"Expected reason='price', got {row['reason']!r}"
        assert row["competitor_name"] == "AcmeParts", (
            f"Expected competitor_name='AcmeParts', got {row['competitor_name']!r}"
        )
        assert row["competitor_price"] == pytest.approx(49.99)
        assert row["customer_name"] == customer.company_name
        assert row["product_sku"] == product.sku
        assert result["totals"]["count"] >= 1
