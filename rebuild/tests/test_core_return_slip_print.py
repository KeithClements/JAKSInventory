"""
tests/test_core_return_slip_print.py
====================================
Feature: when a finalized invoice carries core charges, the Print/PDF view
appends a companion Core Return Slip page (mimics the invoice; lists the cores
the customer owes back; has a sign-off block). Invoices with no cores render
exactly as before — no slip page.

Fixture pattern mirrors tests/test_r1_core_money.py (module-isolated in-memory
engine + TestClient startup that seeds CoreLocation rows + admin user).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


_SEQ = {"n": 0}


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"Slip Cust {_uniq()}", contact_name="QA",
                 email=f"slip{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"SLIP-{_uniq():04d}", title="Turbo Core Part",
                description="Turbo Core Part", cost=30.0, markup_pct=50.0,
                qty_on_hand=0, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _invoice(db, customer):
    from app.constants import InvoiceStatus
    from app.models.invoice import Invoice
    inv = Invoice(invoice_number=f"INV-SLIP-{_uniq()}", customer_id=customer.id,
                  status=InvoiceStatus.OPEN)
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _add_core_to_invoice(db, inv, product, *, cust_charge=50.0, vend_charge=30.0):
    """Mirror what finalize() builds: a product line + a CORE_CHARGE child line,
    plus the CoreCharge stamped with the core line's id (the join the slip uses)."""
    from app.constants import LineType, CoreDirection, CoreStatus
    from app.models.invoice import InvoiceLine
    from app.models.core import CoreCharge

    parent = InvoiceLine(invoice_id=inv.id, product_id=product.id,
                         line_type=LineType.PRODUCT, description=product.title,
                         qty=1, unit_price=300.0, unit_cost=200.0)
    db.add(parent); db.commit(); db.refresh(parent)

    core_line = InvoiceLine(invoice_id=inv.id, product_id=product.id,
                            line_type=LineType.CORE_CHARGE, description="Core charge",
                            qty=1, unit_price=cust_charge, unit_cost=vend_charge,
                            is_core_line=True, parent_line_id=parent.id,
                            is_auto_generated=True, is_locked_to_parent=True)
    db.add(core_line); db.commit(); db.refresh(core_line)

    core = CoreCharge(
        product_id=product.id, customer_id=inv.customer_id,
        invoice_line_id=core_line.id, qty_charged=1, qty_returned=0,
        customer_unit_charge=cust_charge, vendor_unit_charge=vend_charge,
        direction=CoreDirection.CUSTOMER_OWES_RETURN, status=CoreStatus.OPEN,
        return_deadline=datetime.utcnow() + timedelta(days=45),
        grace_days_snapshot=45, credit_invoice_id=inv.id,
    )
    db.add(core); db.commit(); db.refresh(core)
    return core


# ===========================================================================
# Slip appears when the invoice has cores
# ===========================================================================

def test_invoice_with_core_prints_companion_slip(client, db):
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust)
    _add_core_to_invoice(db, inv, prod, cust_charge=50.0)

    resp = client.get(f"/invoices/{inv.id}/print")
    assert resp.status_code == 200
    body = resp.text

    # Companion slip page is actually rendered (not just the CSS for it)...
    assert 'class="page-shell core-slip-page"' in body
    assert "Customer Signature" in body
    assert "Received By (JAKS)" in body
    assert "Potential Credit" in body
    # ...and carries the real core data (sku + credit due)
    assert prod.sku in body
    assert "$50.00" in body


def test_pdf_route_includes_slip_or_falls_back(client, db):
    """The /pdf route renders the same template; with WeasyPrint absent it
    302-redirects to /print (which includes the slip). Either way: not a 500."""
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust)
    _add_core_to_invoice(db, inv, prod)

    resp = client.get(f"/invoices/{inv.id}/pdf", follow_redirects=False)
    assert resp.status_code in (200, 302)


# ===========================================================================
# No cores → no slip page (unchanged behavior)
# ===========================================================================

def test_invoice_without_core_has_no_slip(client, db):
    from app.constants import LineType
    from app.models.invoice import InvoiceLine
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust)
    # A plain product line, no core
    line = InvoiceLine(invoice_id=inv.id, product_id=prod.id,
                       line_type=LineType.PRODUCT, description=prod.title,
                       qty=1, unit_price=300.0, unit_cost=200.0)
    db.add(line); db.commit()

    resp = client.get(f"/invoices/{inv.id}/print")
    assert resp.status_code == 200
    body = resp.text

    assert "Invoice" in body              # sanity: the invoice still renders
    # No companion slip page is rendered. Check slip-SPECIFIC markers, not
    # "Customer Signature": every non-void invoice now carries a customer
    # acknowledgement sign-off block (Received By / Customer Signature), so that
    # phrase is no longer a valid no-slip signal. "Received By (JAKS)" and
    # "Potential Credit" appear ONLY on the core-return slip page.
    assert 'class="page-shell core-slip-page"' not in body
    assert "Received By (JAKS)" not in body
    assert "Potential Credit" not in body
