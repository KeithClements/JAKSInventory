"""
tests/test_workflow_series3.py
==============================
Smoke tests for Backend Workflow Series 3–5:
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

import app.database as _appdb
from tests.conftest import activate, fresh_engine

# Register all models and create tables
from app.models import __all_models__  # noqa: F401

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
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_credit import VendorCreditMemo
from app.models.vendor_return import VendorReturn
from app.services.report_service import ReportService
from app.services.vendor_return_service import VendorReturnService

from app.main import app as _fastapi_app

_client = TestClient(_fastapi_app, raise_server_exceptions=False)

# ── Shared counter for unique identifiers ─────────────────────────────────────
_counter = itertools.count(1)
_UID = 1  # stub current_user_id (admin user id=1)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == 1).first():
        s.add(User(id=1, name="Test Admin", username="admin_s3",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


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


# ═══════════════════════════════════════════════════════════════════════════════
# Backend Workflow Series 5
#   - 3-way match: resolve_match_line gates approve_bill
#   - approve_bill: APPROVE_VENDOR_BILL permission + DISCREPANCY gate
#   - create_match_vendor_credit: requires ISSUE_CREDIT_MEMO + APPROVE_VENDOR_BILL
#   - Customer list: tab param + counts contract
#   - Customer preview panel: GET /customers/preview/{id}
# ═══════════════════════════════════════════════════════════════════════════════

# Additional imports needed for Series 5
from app.constants import MatchResolution, VendorBillStatus, PaymentTerms
from app.models.purchase_order import PurchaseOrder, POLine, VendorBill, VendorBillLine
from app.services.po_service import POService
from app.services.base import PermissionDeniedError


def _make_po_with_discrepancy(db) -> tuple[PurchaseOrder, POLine, VendorBill]:
    """
    Build a PO with one received line and one vendor bill that over-bills
    (qty_billed > qty_received), leaving the bill in DISCREPANCY.
    Returns (po, po_line, vendor_bill).
    """
    n = next(_counter)
    vendor = _make_vendor(db)
    product = _make_product(db, qty_on_hand=10)
    db.commit()

    po = PurchaseOrder(
        po_number=f"PO-S5-{n:04d}",
        vendor_id=vendor.id,
        status="received",
    )
    db.add(po)
    db.flush()

    line = POLine(
        po_id=po.id,
        product_id=product.id,
        qty_ordered=5,
        qty_received=5,
        qty_billed=0,
        unit_cost=100.0,
    )
    db.add(line)
    db.flush()

    # Create bill that over-bills by 1 unit
    bill = VendorBill(
        po_id=po.id,
        vendor_id=vendor.id,
        bill_number=f"BILL-S5-{n:04d}",
        total_amount=600.0,
        status=VendorBillStatus.DISCREPANCY,
    )
    db.add(bill)
    db.flush()

    bill_line = VendorBillLine(
        bill_id=bill.id,
        po_line_id=line.id,
        qty_billed=6,          # 6 billed vs 5 received → over_billed
        unit_cost=100.0,
    )
    db.add(bill_line)
    line.qty_billed = 6
    db.commit()
    db.expire_all()

    return po, line, bill


class TestMatchResolutionGate:
    """Series 5 — resolve_match_line + approve_bill gate."""

    def test_approve_discrepancy_bill_blocked(self, db):
        """approve_bill on a DISCREPANCY bill raises before resolution."""
        _, _, bill = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)
        with pytest.raises(ValueError, match="unresolved match discrepancies"):
            svc.approve_bill(bill.id)

    def test_resolve_opens_gate(self, db):
        """Resolving the flagged line transitions bill DISCREPANCY → PENDING."""
        po, line, bill = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)

        svc.resolve_match_line(
            line.id,
            decision=MatchResolution.ACCEPTED,
            reason="Accepted the extra unit as a vendor gift",
        )
        db.expire_all()

        updated_bill = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
        assert updated_bill.status == VendorBillStatus.PENDING, (
            f"Expected PENDING after resolution, got {updated_bill.status}"
        )
        updated_line = db.query(POLine).filter(POLine.id == line.id).first()
        assert updated_line.match_resolution == MatchResolution.ACCEPTED
        assert updated_line.match_resolved_by_id == 1

    def test_approve_after_resolution_succeeds(self, db):
        """approve_bill succeeds on a PENDING (gate-open) bill."""
        po, line, bill = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)

        svc.resolve_match_line(line.id, decision=MatchResolution.ACCEPTED)
        db.expire_all()

        svc.approve_bill(bill.id)
        db.expire_all()

        updated_bill = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
        assert updated_bill.status == VendorBillStatus.APPROVED

    def test_resolve_requires_reason_for_rejected(self, db):
        """resolve_match_line with REJECTED and no reason raises ValueError."""
        _, line, _ = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)
        with pytest.raises(ValueError, match="reason is required"):
            svc.resolve_match_line(line.id, decision=MatchResolution.REJECTED, reason="")

    def test_resolve_rejected_reason_provided(self, db):
        """REJECTED with a reason is accepted; bill stays DISCREPANCY (still disputed)."""
        _, line, bill = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)
        svc.resolve_match_line(
            line.id,
            decision=MatchResolution.REJECTED,
            reason="Waiting for corrected bill from vendor",
        )
        db.expire_all()
        # REJECTED means AP is still disputing — bill stays DISCREPANCY
        updated_bill = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
        assert updated_bill.status == VendorBillStatus.DISCREPANCY, (
            "Bill should remain DISCREPANCY when line is rejected (still pending corrected bill)"
        )

    def test_approve_already_approved_raises(self, db):
        """approve_bill on an already-APPROVED bill raises."""
        _, line, bill = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)
        svc.resolve_match_line(line.id, decision=MatchResolution.ACCEPTED)
        svc.approve_bill(bill.id)
        with pytest.raises(ValueError, match="already"):
            svc.approve_bill(bill.id)

    def test_create_credit_requires_both_permissions(self, db):
        """create_match_vendor_credit needs ISSUE_CREDIT_MEMO + APPROVE_VENDOR_BILL."""
        from app.constants import UserRole
        # Create a SALES user — has neither permission
        n = next(_counter)
        sales_user = User(
            name=f"SalesUser-{n}", username=f"sales_{n}",
            password_hash="x", role=UserRole.SALES,
        )
        db.add(sales_user)
        db.commit()

        _, line, _ = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=sales_user.id)
        with pytest.raises(PermissionDeniedError):
            svc.create_match_vendor_credit(line.id, reason="test")

    def test_create_credit_creates_vcm_and_marks_credited(self, db):
        """create_match_vendor_credit creates VCM and marks line as CREDITED."""
        po, line, bill = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)

        vcm = svc.create_match_vendor_credit(
            po_line_id=line.id,
            reason="Over-billed by 1 unit",
        )
        db.expire_all()

        assert vcm is not None
        assert vcm.vcm_number.startswith("VCM-")
        assert vcm.total_amount > 0

        updated_line = db.query(POLine).filter(POLine.id == line.id).first()
        assert updated_line.match_resolution == MatchResolution.CREDITED
        assert updated_line.match_resolution_vcm_id == vcm.id

    def test_create_credit_opens_gate_for_approval(self, db):
        """After create_match_vendor_credit, bill transitions to PENDING."""
        po, line, bill = _make_po_with_discrepancy(db)
        svc = POService(db, current_user_id=1)
        svc.create_match_vendor_credit(line.id, reason="Overcharge")
        db.expire_all()

        updated_bill = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
        assert updated_bill.status == VendorBillStatus.PENDING


class TestMatchResolutionRoutes:
    """Series 5 — HTTP routes: resolve and create-credit."""

    def test_resolve_route_200(self, db):
        """POST /purchase-orders/{po_id}/bills/{bill_id}/lines/{line_id}/resolve → 303."""
        po, line, bill = _make_po_with_discrepancy(db)
        resp = _client.post(
            f"/purchase-orders/{po.id}/bills/{bill.id}/lines/{line.id}/resolve",
            data={"decision": "accepted", "reason": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "ok=match_resolved" in resp.headers.get("location", "")

    def test_resolve_route_bad_decision(self, db):
        """Invalid decision → redirect to error."""
        po, line, bill = _make_po_with_discrepancy(db)
        resp = _client.post(
            f"/purchase-orders/{po.id}/bills/{bill.id}/lines/{line.id}/resolve",
            data={"decision": "nonsense", "reason": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers.get("location", "")

    def test_create_credit_route(self, db):
        """POST /purchase-orders/{po_id}/bills/{bill_id}/create-credit → VCM created."""
        po, line, bill = _make_po_with_discrepancy(db)
        resp = _client.post(
            f"/purchase-orders/{po.id}/bills/{bill.id}/create-credit",
            data={"po_line_id": str(line.id), "reason": "Overcharge", "apply_now": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        loc = resp.headers.get("location", "")
        assert "ok=" in loc and "error=" not in loc, f"Unexpected location: {loc}"


class TestCustomerListContract:
    """Series 5 — customer list route: tab param + counts contract."""

    def _seed_customers(self, db) -> list[Customer]:
        """Seed 3 customers: 1 COD (no activity), 1 Net30 with open invoice, 1 Net30 with open quote."""
        n = next(_counter)
        customers = []

        c_plain = Customer(company_name=f"PlainCo-{n}", is_active=True, payment_terms="cod")
        db.add(c_plain)
        customers.append(c_plain)

        c_inv = Customer(company_name=f"InvCo-{n}", is_active=True, payment_terms="net_30")
        db.add(c_inv)
        customers.append(c_inv)

        c_quote = Customer(company_name=f"QuoteCo-{n}", is_active=True, payment_terms="net_30")
        db.add(c_quote)
        customers.append(c_quote)

        db.flush()

        # Open invoice for c_inv
        from app.models.invoice import Invoice
        inv = Invoice(
            invoice_number=f"INV-S5-{n}",
            customer_id=c_inv.id,
            status="open",
            subtotal=100.0,
            total=100.0,
            balance_due=100.0,
        )
        db.add(inv)

        # Open quote for c_quote
        from app.models.quote import Quote
        q = Quote(
            quote_number=f"Q-S5-{n}",
            customer_id=c_quote.id,
            status="draft",
        )
        db.add(q)

        db.commit()
        db.expire_all()
        return customers

    def test_list_all_tab_renders(self):
        """GET /customers/?tab=all returns 200."""
        resp = _client.get("/customers/?tab=all")
        assert resp.status_code == 200

    def test_list_open_invoices_tab_renders(self):
        """GET /customers/?tab=open_invoices returns 200."""
        resp = _client.get("/customers/?tab=open_invoices")
        assert resp.status_code == 200

    def test_list_unknown_tab_defaults_to_all(self):
        """Unknown tab slug → 200, falls back to 'all'."""
        resp = _client.get("/customers/?tab=does_not_exist")
        assert resp.status_code == 200

    def test_list_search_with_tab(self):
        """Search + tab combo → 200."""
        resp = _client.get("/customers/?tab=all&q=nonexistent_xyz")
        assert resp.status_code == 200


class TestCustomerPreviewRoute:
    """Series 5 — GET /customers/preview/{id} before /{customer_id}."""

    def test_preview_valid_customer(self, db):
        """Returns 200 with customer identity content."""
        c = _make_customer(db)
        db.commit()
        resp = _client.get(f"/customers/preview/{c.id}")
        assert resp.status_code == 200
        assert c.company_name in resp.text

    def test_preview_unknown_customer(self):
        """Returns 404 for a customer that doesn't exist."""
        resp = _client.get("/customers/preview/99999999")
        assert resp.status_code == 404

    def test_preview_route_before_detail_route(self, db):
        """'preview' is not captured as a customer_id (str vs int route ordering)."""
        # If routes are mis-ordered, /customers/preview/1 would try int("preview")
        # and raise a 422 or 500. This test confirms correct ordering.
        c = _make_customer(db)
        db.commit()
        resp = _client.get(f"/customers/preview/{c.id}")
        assert resp.status_code in (200, 404)  # 200 if found, not 422/500


# ═══════════════════════════════════════════════════════════════════════════════
# PO cancel — qty_on_order reversal (SENT vs PARTIAL)
#   Regression: cancelling a PARTIAL PO must reverse only the *outstanding*
#   (unreceived) qty, not the full qty_ordered, or phantom on-order inventory
#   leaks and inflates qty_available / suppresses reorders.
# ═══════════════════════════════════════════════════════════════════════════════
from app.constants import POStatus


def _make_sent_po(db, *, qty_ordered: int, qty_on_hand: int = 0):
    """
    Create → add one product line → send a PO through the real service flow.
    After send_to_vendor, product.qty_on_order == qty_ordered.
    Returns (svc, po, po_line, product).
    """
    vendor = _make_vendor(db)
    product = _make_product(db, qty_on_hand=qty_on_hand)
    db.commit()

    svc = POService(db, current_user_id=1)
    po = svc.create_po(vendor.id, data={})
    line = svc.add_line(po.id, product.id, {"qty_ordered": qty_ordered, "unit_cost": 10.0})
    svc.send_to_vendor(po.id)
    db.expire_all()

    product = db.query(Product).filter(Product.id == product.id).first()
    assert product.qty_on_order == qty_ordered  # baseline established
    return svc, po, db.query(POLine).filter(POLine.id == line.id).first(), product


class TestPOCancelOnOrderReversal:
    """cancel() must reverse the correct on-order qty for SENT and PARTIAL POs."""

    def test_cancel_sent_reverses_full_ordered(self, db):
        """A SENT PO (nothing received) reverses the full qty_ordered."""
        svc, po, line, product = _make_sent_po(db, qty_ordered=10)

        svc.cancel(po.id)
        db.expire_all()

        product = db.query(Product).filter(Product.id == product.id).first()
        assert product.qty_on_order == 0
        assert db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first().status == POStatus.CANCELLED

    def test_cancel_partial_reverses_only_outstanding(self, db):
        """
        REGRESSION: a PARTIAL PO (some goods received) must reverse only the
        unreceived remainder, not the full qty_ordered.

        Order 10, receive 4 → PARTIAL, qty_on_order should be 6 (10 - 4 received).
        Cancelling must drop on-order by the 6 outstanding → 0, NOT by 10.
        """
        svc, po, line, product = _make_sent_po(db, qty_ordered=10)

        # Receive 4 of the 10 → PO goes PARTIAL; receipt already dropped on-order by 4.
        svc.create_receipt(po.vendor_id, {line.id: 4}, data={})
        db.expire_all()
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first()
        product = db.query(Product).filter(Product.id == product.id).first()
        assert po.status == POStatus.PARTIAL
        outstanding = 10 - 4
        assert product.qty_on_order == outstanding  # 6 still on order

        on_order_before = product.qty_on_order
        svc.cancel(po.id)
        db.expire_all()

        product = db.query(Product).filter(Product.id == product.id).first()
        # The fix: subtract only the 6 outstanding, flooring at 0 → 0.
        # The bug would subtract the full 10 (no-op past floor here, but the
        # invariant we assert is "original - outstanding", not "original - ordered").
        assert product.qty_on_order == on_order_before - outstanding == 0
        assert db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first().status == POStatus.CANCELLED

    def test_cancel_partial_does_not_underflow_other_stock(self, db):
        """
        With unrelated on-order stock present, a PARTIAL cancel removes exactly
        the outstanding qty — proving it isn't reversing the full qty_ordered.

        Two POs for the same product: PO-A orders 10, PO-B orders 5 (both sent →
        on_order = 15). Receive 4 on PO-A (on_order = 11, PO-A PARTIAL). Cancelling
        PO-A must leave on_order = 11 - 6 = 5 (PO-B's untouched order), not 11 - 10 = 1.
        """
        vendor = _make_vendor(db)
        product = _make_product(db, qty_on_hand=0)
        db.commit()

        svc = POService(db, current_user_id=1)
        po_a = svc.create_po(vendor.id, data={})
        line_a = svc.add_line(po_a.id, product.id, {"qty_ordered": 10, "unit_cost": 10.0})
        svc.send_to_vendor(po_a.id)

        po_b = svc.create_po(vendor.id, data={})
        svc.add_line(po_b.id, product.id, {"qty_ordered": 5, "unit_cost": 10.0})
        svc.send_to_vendor(po_b.id)
        db.expire_all()

        product = db.query(Product).filter(Product.id == product.id).first()
        assert product.qty_on_order == 15  # 10 + 5

        # Receive 4 on PO-A → on_order 15 - 4 = 11, PO-A PARTIAL
        svc.create_receipt(po_a.vendor_id, {line_a.id: 4}, data={})
        db.expire_all()
        product = db.query(Product).filter(Product.id == product.id).first()
        assert product.qty_on_order == 11

        # Cancel PO-A: reverse the 6 outstanding only → 11 - 6 = 5 (PO-B intact).
        svc.cancel(po_a.id)
        db.expire_all()
        product = db.query(Product).filter(Product.id == product.id).first()
        assert product.qty_on_order == 5, (
            "PARTIAL cancel must reverse only the unreceived remainder (6), "
            "leaving PO-B's 5 on order — the bug would reverse the full 10 → 1."
        )

    def test_cancel_billed_raises(self, db):
        """A BILLED PO cannot be cancelled (3-way match already reconciled)."""
        svc, po, line, product = _make_sent_po(db, qty_ordered=3)
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first()
        po.status = POStatus.BILLED
        db.commit()
        with pytest.raises(ValueError, match="billed"):
            svc.cancel(po.id)

    def test_cancel_idempotent(self, db):
        """Cancelling an already-CANCELLED PO is a no-op and doesn't double-reverse."""
        svc, po, line, product = _make_sent_po(db, qty_ordered=8)
        svc.cancel(po.id)
        db.expire_all()
        product = db.query(Product).filter(Product.id == product.id).first()
        assert product.qty_on_order == 0

        # Second cancel must not touch on-order again.
        svc.cancel(po.id)
        db.expire_all()
        product = db.query(Product).filter(Product.id == product.id).first()
        assert product.qty_on_order == 0
