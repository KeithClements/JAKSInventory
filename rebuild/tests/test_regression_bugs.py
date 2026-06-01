"""
tests/test_regression_bugs.py
==============================
Regression guards for three specific bugs whose fixes are verified here to
prevent silent re-regression.  Each test is minimal, fast, and service-level.

Bug 1 — CORE_CHARGE line on SO after quote→SO conversion, included in subtotal
    QuoteService.convert_to_sales_order passes PRODUCT lines to
    SalesOrderService.create_sales_order, which calls _maybe_add_core_line for
    each product that has has_core=True.  The resulting SO must carry a discrete
    CORE_CHARGE child SOLine, and SalesOrder.subtotal must include its value.

Bug 6 — record_payment persists `notes`
    PaymentService.record_payment reads data["notes"] and persists it to
    Payment.notes.  The route at POST /payments/new builds the data dict from
    the form field of the same name.  Test at service level plus a route smoke.

UX5 — $0.00/empty invoice cannot be finalized
    InvoiceService.validate_for_finalise blocks finalization when invoice.total
    == 0 (all lines are zero-price or no lines at all).  Prevents an accidental
    $0 OPEN invoice from silently polluting A/R.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_regression_bugs.py -v
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # startup seeds admin user #1 with ADMIN role


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name="Bug Guard Co", contact_name="QA",
                 email="bug@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name="Bug Guard Vendor")
    db.add(v); db.commit(); db.refresh(v)
    return v


_product_counter = __import__("itertools").count(1)


def _core_product(db, *, vendor_core=20.0, customer_core=30.0):
    """Product with has_core=True and both charge fields set."""
    from app.constants import ProductStatus
    from app.models.product import Product
    n = next(_product_counter)
    p = Product(
        sku=f"BUG-CORE-{n:03d}", title=f"Core Test Part {n}",
        description="core guard test", cost=50.0, markup_pct=20.0,
        qty_on_hand=5, status=ProductStatus.ACTIVE, is_active=True,
        has_core=True,
        vendor_core_charge=vendor_core,
        customer_core_charge=customer_core,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


# ===========================================================================
# Bug 1 — CORE_CHARGE line on SO + included in SalesOrder.subtotal
# ===========================================================================

def test_bug1_so_has_core_charge_line_after_convert(db):
    """
    Quote (core product) → convert_to_sales_order → SO carries a discrete
    CORE_CHARGE child SOLine.

    Before the fix (create_sales_order calling _maybe_add_core_line) the SO
    only had the PRODUCT line; the customer was billed correctly on the invoice
    but the SO total understated the amount the customer had agreed to pay.
    """
    from app.constants import LineType, QuoteStatus
    from app.models.quote import Quote, SalesOrder, SOLine
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _core_product(db)

    # Build a minimal quote with one core product line
    quote = Quote(quote_number="Q-BUG1-001", customer_id=cust.id,
                  status=QuoteStatus.DRAFT)
    db.add(quote); db.commit(); db.refresh(quote)

    svc = QuoteService(db, _UID)
    svc.add_line(quote.id, prod.id, {
        "qty": 1,
        "unit_price": 60.0,
        "description": prod.title,
    })
    db.expire_all()
    quote_r = db.query(Quote).filter(Quote.id == quote.id).first()

    # Quote must have both the product AND the core charge line
    quote_types = {ln.line_type for ln in quote_r.lines}
    assert LineType.CORE_CHARGE in quote_types, (
        f"Quote must have a CORE_CHARGE line; found types: {quote_types}"
    )
    assert LineType.PRODUCT in quote_types

    # Convert to SO
    so = svc.convert_to_sales_order(quote.id, payment_mode="none")
    db.expire_all()
    so_r = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()

    so_line_types = [ln.line_type for ln in so_r.lines]
    assert LineType.CORE_CHARGE in so_line_types, (
        f"SO must have a CORE_CHARGE line after convert; found: {so_line_types}. "
        "Bug 1: core dropped off SO — _maybe_add_core_line not called."
    )


def test_bug1_core_charge_included_in_so_subtotal(db):
    """
    The CORE_CHARGE SOLine's line_total must be included in SalesOrder.subtotal.
    SalesOrder.subtotal sums ALL lines (no filter), so it includes CORE_CHARGE.
    """
    from app.constants import LineType, QuoteStatus
    from app.models.quote import Quote, SalesOrder
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _core_product(db, customer_core=35.0)

    quote = Quote(quote_number="Q-BUG1-002", customer_id=cust.id,
                  status=QuoteStatus.DRAFT)
    db.add(quote); db.commit(); db.refresh(quote)

    svc = QuoteService(db, _UID)
    svc.add_line(quote.id, prod.id, {"qty": 1, "unit_price": 100.0,
                                      "description": prod.title})
    db.expire_all()

    so = svc.convert_to_sales_order(quote.id, payment_mode="none")
    db.expire_all()
    so_r = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()

    # Subtotal must be product + core = 100 + 35 = 135
    expected = 135.0
    assert so_r.subtotal == expected, (
        f"SO.subtotal should be {expected} (product $100 + core $35); "
        f"got {so_r.subtotal!r}. "
        "Bug 1: core not included in SO subtotal."
    )


# ===========================================================================
# Bug 6 — record_payment persists `notes`
# ===========================================================================

def test_bug6_record_payment_persists_notes_service_level(db):
    """
    PaymentService.record_payment must persist the notes string passed in data.
    """
    from app.constants import PaymentMethod
    from app.models.invoice import Payment
    from app.services.payment_service import PaymentService

    cust = _customer(db)
    svc = PaymentService(db, _UID)
    pmt = svc.record_payment(
        customer_id=cust.id,
        amount_received=50.0,
        payment_method=PaymentMethod.CASH,
        data={"notes": "guard note — Bug 6"},
    )
    db.expire_all()

    pmt_r = db.query(Payment).filter(Payment.id == pmt.id).first()
    assert pmt_r is not None
    assert pmt_r.notes == "guard note — Bug 6", (
        f"Payment.notes must persist the value passed in data['notes']; "
        f"got {pmt_r.notes!r}"
    )


def test_bug6_empty_notes_persists_as_empty_string(db):
    """notes='' (the empty default) must not be None."""
    from app.constants import PaymentMethod
    from app.models.invoice import Payment
    from app.services.payment_service import PaymentService

    cust = _customer(db)
    pmt = PaymentService(db, _UID).record_payment(
        customer_id=cust.id,
        amount_received=25.0,
        payment_method=PaymentMethod.CHECK,
        data={},   # notes omitted → defaults to ""
    )
    db.expire_all()
    pmt_r = db.query(Payment).filter(Payment.id == pmt.id).first()
    assert pmt_r.notes == "", f"Empty notes should persist as '' not None; got {pmt_r.notes!r}"


def test_bug6_route_passes_notes_to_payment(client, db):
    """
    POST /payments/new with a notes field must persist notes on the Payment row.
    The route builds data = {... "notes": form.get("notes") ...} and passes it
    to PaymentService.record_payment — test the full chain.
    """
    from app.models.invoice import Payment

    cust = _customer(db)

    r = client.post("/payments/new", data={
        "customer_id": str(cust.id),
        "amount": "75.00",
        "method": "cash",
        "notes": "route-level Bug 6 guard",
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text[:200]

    pmt_r = (
        db.query(Payment)
        .filter(Payment.customer_id == cust.id)
        .order_by(Payment.id.desc())
        .first()
    )
    assert pmt_r is not None, "No payment was created"
    assert pmt_r.notes == "route-level Bug 6 guard", (
        f"Route must pass the notes form field through to Payment.notes; "
        f"got {pmt_r.notes!r}"
    )


# ===========================================================================
# UX5 — $0.00 / empty invoice cannot be finalized
# ===========================================================================

def test_ux5_zero_total_invoice_blocked_from_finalise(db):
    """
    An invoice whose total is $0.00 must be blocked by validate_for_finalise.
    Every line has unit_price=0 → invoice.total == 0 → validation error.
    """
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine
    from app.services.invoice_service import InvoiceService

    cust = _customer(db)
    inv = Invoice(invoice_number="INV-UX5-001", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT, tax_rate=0.0)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT,
                       description="Zero-price part", qty=1,
                       unit_price=0.0, unit_cost=0.0, is_taxable=False))
    db.commit()

    errors = InvoiceService(db, _UID).validate_for_finalise(inv.id)
    assert any("$0.00" in e or "zero" in e.lower() or "0" in e for e in errors), (
        f"validate_for_finalise must block a $0.00 invoice; errors={errors!r}"
    )


def test_ux5_empty_invoice_already_blocked(db):
    """An invoice with no lines at all is blocked (pre-existing guard)."""
    from app.constants import InvoiceStatus
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    cust = _customer(db)
    inv = Invoice(invoice_number="INV-UX5-002", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT)
    db.add(inv); db.commit()

    errors = InvoiceService(db, _UID).validate_for_finalise(inv.id)
    assert errors, "Empty invoice must produce at least one validation error"
    assert any("no lines" in e.lower() or "line" in e.lower() for e in errors), (
        f"Expected 'no lines' error; got: {errors!r}"
    )


def test_ux5_nonzero_invoice_is_not_blocked(db):
    """Counter-case: a normal invoice with positive total must pass validation."""
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine
    from app.services.invoice_service import InvoiceService

    cust = _customer(db)
    inv = Invoice(invoice_number="INV-UX5-003", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT,
                       description="Paid part", qty=1,
                       unit_price=99.99, unit_cost=50.0, is_taxable=False))
    db.commit()

    errors = InvoiceService(db, _UID).validate_for_finalise(inv.id)
    # Any errors should NOT be about zero total
    zero_errors = [e for e in errors if "$0.00" in e or "zero" in e.lower()]
    assert not zero_errors, (
        f"Non-zero invoice must not trigger the $0 guard; zero_errors={zero_errors!r}"
    )
