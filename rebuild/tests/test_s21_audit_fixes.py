"""
tests/test_s21_audit_fixes.py
=============================
Regression gate for the §21 "Update 6.16" sellable-ERP audit fixes (MASTER_PLAN
§21). Each test locks in one verified money/inventory/security fix so it can't
silently regress:

  #2  void_invoice closes the open CoreCharge rows it created (no phantom cores)
  #3  RBAC — SALES cannot void invoices or record payments; ADMIN/BOOKKEEPING can
  #5  VERBAL_ORDER POs can be received directly (no "Place Order" step)
  #6  credit hold = WARN — finalize bounces to ?credit_hold=1, proceeds on confirm
  #8  FULL payment mode blocks fulfil/invoice while the balance is uncollected
  #4  customer statements include credit memos (balance no longer inflated)
  #9  quotes carry tax (default from customer), clerk toggle, carry forward on convert

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_s21_audit_fixes.py -v
"""
from __future__ import annotations

import datetime as _dt
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.constants import (
    CoreInspectionOutcome, CoreStatus, CreditMemoStatus, InvoiceStatus,
    LineType, POStatus, ProductStatus, SOPaymentMode, UserRole,
)
from app.models.core import CoreCharge
from app.models.credit_memo import CreditMemo
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine
from app.models.user import User
from app.models.vendor import Vendor
from app.services.base import PermissionDeniedError
from app.services.core_service import CoreService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.quote_service import QuoteService
from app.services.sales_order_service import SalesOrderService
from app.services.statement_service import StatementService

_ENGINE = fresh_engine()
_UID = 1                       # admin, seeded by the app-startup hook
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c                # entering the client triggers startup → seeds admin #1


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── builders ──────────────────────────────────────────────────────────────────

def _user(db, role: str) -> User:
    n = next(_seq)
    u = User(name=f"{role}-{n}", username=f"{role}_{n}", password_hash="x", role=role)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _customer(db, **kw) -> Customer:
    c = Customer(company_name=f"S21 Co {next(_seq)}", contact_name="QA",
                 email="s21@test.local", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, **kw) -> Product:
    p = Product(sku=f"S21-{next(_seq):04d}", title="S21 Part", description="S21 Part",
                cost=30.0, markup_pct=50.0, qty_on_hand=kw.pop("qoh", 0),
                status=ProductStatus.ACTIVE, is_active=True, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _finalized_invoice(db, customer_id: int, amount: float = 100.0) -> Invoice:
    inv = Invoice(invoice_number=f"INV-S21-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.DRAFT, tax_rate=0.0, tax_rate_snapshot=0.0,
                  tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT,
                       description="part", qty=1, unit_price=amount,
                       unit_cost=amount * 0.6, is_taxable=False))
    db.commit()
    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _core_line_on(db, invoice: Invoice, product_id: int) -> InvoiceLine:
    """A CORE_CHARGE child line + the CoreCharge that finalize would have made."""
    ln = InvoiceLine(invoice_id=invoice.id, line_type=LineType.CORE_CHARGE,
                     description="core deposit", qty=1, unit_price=50.0,
                     unit_cost=30.0, is_taxable=False, parent_line_id=None)
    db.add(ln); db.flush()
    CoreService(db, _UID).create_core_charge(
        invoice_id=invoice.id, invoice_line_id=ln.id, product_id=product_id,
        qty=1, customer_unit_charge=50.0, vendor_unit_charge=30.0)
    db.commit()
    return ln


# ── #2 — void closes open cores ────────────────────────────────────────────────

def test_void_closes_open_core_charges(db):
    cust = _customer(db)
    prod = _product(db, qoh=5)
    inv = _finalized_invoice(db, cust.id)
    _core_line_on(db, inv, prod.id)

    open_cores = db.query(CoreCharge).join(
        InvoiceLine, CoreCharge.invoice_line_id == InvoiceLine.id
    ).filter(InvoiceLine.invoice_id == inv.id).all()
    assert open_cores and all(c.status == CoreStatus.OPEN for c in open_cores)

    InvoiceService(db, _UID).void_invoice(inv.id, "test void")
    db.expire_all()

    cores = db.query(CoreCharge).join(
        InvoiceLine, CoreCharge.invoice_line_id == InvoiceLine.id
    ).filter(InvoiceLine.invoice_id == inv.id).all()
    assert cores and all(c.status == CoreStatus.CLOSED for c in cores)


def test_void_leaves_returned_core_untouched(db):
    cust = _customer(db)
    prod = _product(db, qoh=5)
    inv = _finalized_invoice(db, cust.id)
    ln = _core_line_on(db, inv, prod.id)
    core = db.query(CoreCharge).filter(CoreCharge.invoice_line_id == ln.id).first()

    # Customer already physically returned the core (HOLD — received, credit
    # deferred so no auto-applied payment blocks the void). This is a real
    # physical event the void must not silently rewrite.
    CoreService(db, _UID).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.HOLD)
    db.commit()
    returned_status = db.query(CoreCharge).get(core.id).status

    InvoiceService(db, _UID).void_invoice(inv.id, "void after return")
    db.expire_all()
    # The void must NOT rewrite a core that already has a return recorded.
    assert db.query(CoreCharge).get(core.id).status == returned_status


# ── #3 — RBAC on void + record_payment ─────────────────────────────────────────

def test_sales_user_cannot_void_invoice(db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id)
    sales = _user(db, UserRole.SALES)
    with pytest.raises(PermissionDeniedError):
        InvoiceService(db, sales.id).void_invoice(inv.id, "nope")


def test_admin_can_void_invoice(db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id)
    admin = _user(db, UserRole.ADMIN)
    InvoiceService(db, admin.id).void_invoice(inv.id, "ok")
    db.expire_all()
    assert db.query(Invoice).get(inv.id).status == InvoiceStatus.VOID


def test_sales_user_cannot_record_payment(db):
    cust = _customer(db)
    sales = _user(db, UserRole.SALES)
    with pytest.raises(PermissionDeniedError):
        PaymentService(db, sales.id).record_payment(
            customer_id=cust.id, amount_received=50.0,
            payment_method="cash", data={})


def test_bookkeeping_user_can_record_payment(db):
    cust = _customer(db)
    book = _user(db, UserRole.BOOKKEEPING)
    pmt = PaymentService(db, book.id).record_payment(
        customer_id=cust.id, amount_received=50.0, payment_method="cash", data={})
    assert pmt.id is not None and pmt.amount_received == 50.0


# ── #5 — VERBAL_ORDER PO is receivable ─────────────────────────────────────────

def test_verbal_order_po_can_be_received(client, db):
    vendor = Vendor(name=f"V-{next(_seq)}")
    db.add(vendor); db.commit(); db.refresh(vendor)
    prod = _product(db, qoh=0)
    po = PurchaseOrder(po_number=f"PO-S21-{next(_seq):04d}", vendor_id=vendor.id,
                       status=POStatus.VERBAL_ORDER)
    db.add(po); db.flush()
    line = POLine(po_id=po.id, product_id=prod.id, qty_ordered=4, unit_cost=20.0)
    db.add(line); db.commit()
    line_id, po_id, pid = line.id, po.id, prod.id

    resp = client.post(f"/purchase-orders/{po_id}/receive",
                       data={f"recv_{line_id}": "4"}, follow_redirects=False)
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "must be" not in loc.lower() and "error" not in loc.lower()

    db.expire_all()
    assert db.query(Product).get(pid).qty_on_hand == 4


# ── #8 — FULL payment mode blocks an uncollected invoice ───────────────────────

def test_full_payment_mode_blocks_uncollected_fulfill(db):
    cust = _customer(db)
    prod = _product(db, qoh=10)
    so = SalesOrderService(db, _UID).create_sales_order(
        customer_id=cust.id, payment_mode=SOPaymentMode.FULL,
        data={"lines": [{
            "product_id": prod.id, "line_type": LineType.PRODUCT,
            "description": "big special order", "qty_ordered": 2,
            "unit_price": 2000.0, "unit_cost": 1200.0, "discount_pct": 0.0,
        }]})
    db.commit()
    line = so.lines[0]
    with pytest.raises(ValueError, match="Pay in Full"):
        SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 2})


# ── #4 — statements include credit memos ───────────────────────────────────────

def test_statement_includes_credit_memos(db):
    cust = _customer(db)
    _finalized_invoice(db, cust.id, amount=300.0)   # +300 charge
    db.add(CreditMemo(cm_number=f"CM-S21-{next(_seq):05d}", customer_id=cust.id,
                      total_amount=120.0, status=CreditMemoStatus.OPEN))
    db.commit()

    today = _dt.date.today()
    stmt = StatementService(db).generate_statement(
        cust.id, period_start=today - _dt.timedelta(days=1),
        period_end=today + _dt.timedelta(days=1))

    assert any(ln["txn_type"] == "credit_memo" for ln in stmt["lines"])
    # Closing balance nets the credit memo: 300 charge − 120 credit = 180.
    assert stmt["closing_balance"] == pytest.approx(180.0, abs=0.01)


# ── #9 — quotes carry tax ──────────────────────────────────────────────────────

def _taxable_customer(db, rate=8.25) -> Customer:
    return _customer(db, is_tax_exempt=False, tax_rate=rate)


def _quote_for(db, customer_id: int):
    return QuoteService(db, _UID).create_quote(customer_id, {
        "lines": [{"product_id": None, "line_type": LineType.PRODUCT,
                   "description": "part", "qty": 1, "unit_price": 1000.0,
                   "unit_cost": 600.0, "discount_pct": 0.0}]})


def test_taxable_customer_quote_has_tax(db):
    cust = _taxable_customer(db, rate=8.25)
    q = _quote_for(db, cust.id)
    db.refresh(q)
    assert q.is_taxable is True
    assert q.tax_rate_snapshot == pytest.approx(8.25)
    assert q.tax_amount == pytest.approx(82.50, abs=0.01)
    assert q.total == pytest.approx(1082.50, abs=0.01)


def test_tax_exempt_customer_quote_is_untaxed(db):
    cust = _customer(db, is_tax_exempt=True, tax_rate=0.0)
    q = _quote_for(db, cust.id)
    db.refresh(q)
    assert q.is_taxable is False
    assert q.tax_amount == 0.0
    assert q.total == pytest.approx(q.subtotal, abs=0.01)


def test_quote_tax_toggle_route(client, db):
    cust = _taxable_customer(db, rate=10.0)
    q = _quote_for(db, cust.id)
    qid = q.id
    # Toggle OFF → no tax.
    client.post(f"/quotes/{qid}/toggle-tax")
    db.expire_all()
    assert db.query(type(q)).get(qid).is_taxable is False
    # Toggle back ON → tax restored from the customer rate.
    client.post(f"/quotes/{qid}/toggle-tax")
    db.expire_all()
    reget = db.query(type(q)).get(qid)
    assert reget.is_taxable is True and reget.tax_amount > 0


def test_quote_tax_carries_to_invoice(db):
    cust = _taxable_customer(db, rate=8.25)
    q = _quote_for(db, cust.id)
    inv = QuoteService(db, _UID).convert_to_invoice(q.id)
    db.expire_all()
    inv = db.query(Invoice).get(inv.id)
    # The invoice total must match the quoted total (tax carried forward).
    assert inv.is_taxable is True
    assert inv.tax_rate_snapshot == pytest.approx(8.25)
    assert inv.total == pytest.approx(db.query(type(q)).get(q.id).total, abs=0.01)
