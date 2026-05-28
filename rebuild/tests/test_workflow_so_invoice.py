"""
tests/test_workflow_so_invoice.py
===================================
Service-layer smoke tests for Backend Workflow Series 1:
  - SO → Invoice conversion (fulfill_and_invoice)
  - Invoice lock enforcement (EOD / QBO-sync / paid triggers)

Run with:
    .venv/Scripts/python.exe -m pytest tests/test_workflow_so_invoice.py -v

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

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
_appdb.engine = _TEST_ENGINE
_appdb.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

# Register all models and create tables
from app.models import __all_models__  # noqa: F401
from app.database import Base

Base.metadata.create_all(bind=_TEST_ENGINE)

# ── App imports (safe after patch) ────────────────────────────────────────────
import pytest
from app.constants import (
    FulfillmentSource,
    InvoiceLockReason,
    InvoiceStatus,
    LineType,
    PaymentMethod,
    SOLineStatus,
    SOPaymentMode,
    SOStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.sales_order_service import SalesOrderService

# ── Shared counter for unique SKUs / SO numbers ───────────────────────────────
_counter = itertools.count(1)
_UID = 1  # stub current_user_id


# ── Data builders ─────────────────────────────────────────────────────────────

def _make_customer(db) -> Customer:
    c = Customer(company_name=f"TestCo-{next(_counter)}", is_active=True)
    db.add(c)
    db.flush()
    return c


def _make_product(db, qty_on_hand: int = 10) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"SKU-{n:06d}",
        title=f"Widget {n}",
        qty_on_hand=qty_on_hand,
        qty_committed=0,
        cost=10.0,
    )
    db.add(p)
    db.flush()
    return p


def _make_so(db, customer: Customer, payment_mode: str = SOPaymentMode.NONE) -> SalesOrder:
    n = next(_counter)
    so = SalesOrder(
        so_number=f"SO-2026-{n:04d}",
        customer_id=customer.id,
        status=SOStatus.OPEN,
        payment_mode=payment_mode,
        deposit_amount=0.0,
        notes="",
        internal_notes="",
    )
    db.add(so)
    db.flush()
    return so


def _make_so_line(
    db,
    so: SalesOrder,
    product: Product,
    qty: int = 3,
    price: float = 50.0,
) -> SOLine:
    """Create an SOLine with qty_committed set (mimics SalesOrderService.add_line)."""
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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    """Session backed by the module-level in-memory engine.
    Tests create their own data with unique IDs; no rollback needed.
    """
    session = _appdb.SessionLocal()
    yield session
    session.close()


# ══════════════════════════════════════════════════════════════════════════════
# SO → Invoice conversion
# ══════════════════════════════════════════════════════════════════════════════

class TestSOToInvoiceConversion:

    def test_fulfilled_so_creates_open_invoice(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=10)
        so = _make_so(db, cust)
        line = _make_so_line(db, so, prod, qty=3)
        db.commit()

        invoice = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 3})

        db.expire_all()
        assert invoice.status == InvoiceStatus.OPEN, "invoice should be OPEN after fulfillment"
        assert invoice.sales_order_id == so.id
        assert invoice.customer_id == cust.id

    def test_so_status_advances_to_invoiced(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=10)
        so = _make_so(db, cust)
        line = _make_so_line(db, so, prod, qty=3)
        db.commit()

        SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 3})

        db.expire_all()
        so_r = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
        assert so_r.status == SOStatus.INVOICED

    def test_so_header_fields_carried_to_invoice(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=5)
        so = _make_so(db, cust)
        so.customer_po_number = "PO-TEST-99"
        so.customer_job_number = "JOB-42"
        so.esn = "ESN-XYZ"
        so.notes = "Priority customer"
        line = _make_so_line(db, so, prod, qty=2)
        db.commit()

        invoice = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 2})

        assert invoice.customer_po_number == "PO-TEST-99"
        assert invoice.customer_job_number == "JOB-42"
        assert invoice.esn == "ESN-XYZ"
        assert invoice.notes == "Priority customer"

    def test_inventory_decremented_on_fulfillment(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=10)
        so = _make_so(db, cust)
        line = _make_so_line(db, so, prod, qty=4)
        db.commit()

        SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 4})

        db.expire(prod)
        assert prod.qty_on_hand == 6  # 10 - 4

    def test_partial_fulfillment_so_becomes_partial(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=10)
        so = _make_so(db, cust)
        line = _make_so_line(db, so, prod, qty=5)
        db.commit()

        SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 3})

        db.expire_all()
        so_r = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
        assert so_r.status == SOStatus.PARTIAL

    def test_partial_fulfillment_does_not_over_release_committed(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=10)
        so = _make_so(db, cust)
        line = _make_so_line(db, so, prod, qty=5)  # commits 5
        db.commit()

        SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 3})  # ship 3

        db.expire_all()
        prod_r = db.query(Product).filter(Product.id == prod.id).first()
        line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
        assert line_r.qty_committed == 2, "should still have 2 committed for remaining qty"
        assert prod_r.qty_committed == 2

    def test_deposit_auto_allocated_to_invoice(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=10)
        so = _make_so(db, cust, payment_mode=SOPaymentMode.DEPOSIT)
        line = _make_so_line(db, so, prod, qty=2, price=100.0)
        db.commit()

        # Collect $50 deposit on the SO
        SalesOrderService(db, _UID).collect_deposit(so.id, 50.0, PaymentMethod.CHECK)

        # Fulfill — deposit should auto-allocate to the new invoice
        invoice = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 2})

        db.expire(invoice)
        assert invoice.amount_paid >= 49.99, "deposit should be applied to invoice"
        assert invoice.balance_due < invoice.total, "balance should be reduced by deposit"


# ══════════════════════════════════════════════════════════════════════════════
# Invoice lock enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestInvoiceLockEnforcement:

    def _open_invoice(self, db) -> Invoice:
        """Helper: create an OPEN invoice via SO fulfillment."""
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=10)
        so = _make_so(db, cust)
        line = _make_so_line(db, so, prod, qty=2, price=100.0)
        db.commit()
        inv = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 2})
        db.expire_all()
        return db.query(Invoice).filter(Invoice.id == inv.id).first()

    def test_paid_invoice_locks_automatically(self, db):
        inv = self._open_invoice(db)
        total = inv.total

        PaymentService(db, _UID).record_payment(
            customer_id=inv.customer_id,
            amount_received=total,
            payment_method=PaymentMethod.CHECK,
            data={"notes": "full payment test"},
            invoice_ids=[inv.id],
        )

        db.expire(inv)
        assert inv.status == InvoiceStatus.PAID
        assert inv.is_locked is True
        assert inv.lock_reason == InvoiceLockReason.PAID

    def test_qbo_synced_invoice_locks(self, db):
        inv = self._open_invoice(db)

        InvoiceService(db, _UID).mark_synced(inv.id, "QBO-TEST-1")

        db.expire(inv)
        assert inv.is_locked is True
        assert inv.lock_reason == InvoiceLockReason.QBO_SYNC
        assert inv.qbo_invoice_id == "QBO-TEST-1"

    def test_locked_invoice_rejects_add_line(self, db):
        inv = self._open_invoice(db)
        InvoiceService(db, _UID).mark_synced(inv.id, "QBO-TEST-2")
        db.expire(inv)

        with pytest.raises(ValueError, match="locked"):
            InvoiceService(db, _UID).add_line(
                inv.id,
                None,
                {"description": "Forbidden line", "qty": 1, "unit_price": 10.0},
            )

    def test_locked_invoice_rejects_update_line(self, db):
        inv = self._open_invoice(db)
        InvoiceService(db, _UID).mark_synced(inv.id, "QBO-TEST-3")
        db.expire(inv)
        first_line_id = inv.lines[0].id

        with pytest.raises(ValueError, match="locked"):
            InvoiceService(db, _UID).update_line(first_line_id, {"qty": 99})

    def test_locked_invoice_rejects_remove_line(self, db):
        inv = self._open_invoice(db)
        InvoiceService(db, _UID).mark_synced(inv.id, "QBO-TEST-4")
        db.expire(inv)
        first_line_id = inv.lines[0].id

        with pytest.raises(ValueError, match="locked"):
            InvoiceService(db, _UID).remove_line(first_line_id)

    def test_locked_invoice_accepts_payment(self, db):
        inv = self._open_invoice(db)
        InvoiceService(db, _UID).mark_synced(inv.id, "QBO-TEST-5")
        db.expire(inv)

        # Payments must always be accepted on locked invoices
        PaymentService(db, _UID).record_payment(
            customer_id=inv.customer_id,
            amount_received=inv.total,
            payment_method=PaymentMethod.CHECK,
            data={"notes": "payment on locked invoice"},
            invoice_ids=[inv.id],
        )

        db.expire(inv)
        assert inv.status == InvoiceStatus.PAID

    def test_eod_lock_does_not_change_status(self, db):
        inv = self._open_invoice(db)
        assert inv.status == InvoiceStatus.OPEN

        InvoiceService(db, _UID).lock(inv.id, reason=InvoiceLockReason.END_OF_DAY)

        db.expire(inv)
        assert inv.is_locked is True
        assert inv.lock_reason == InvoiceLockReason.END_OF_DAY
        assert inv.status == InvoiceStatus.OPEN  # lock doesn't change status

    def test_lock_is_idempotent(self, db):
        inv = self._open_invoice(db)

        InvoiceService(db, _UID).lock(inv.id, reason=InvoiceLockReason.END_OF_DAY)
        first_locked_at = inv.locked_at
        InvoiceService(db, _UID).lock(inv.id, reason=InvoiceLockReason.QBO_SYNC)  # should no-op

        db.expire(inv)
        assert inv.lock_reason == InvoiceLockReason.END_OF_DAY  # first lock wins
        assert inv.locked_at == first_locked_at
