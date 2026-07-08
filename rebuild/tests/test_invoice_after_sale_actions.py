"""
tests/test_invoice_after_sale_actions.py
=========================================
After-Sale Service entry points on the Invoice workspace — start a core return,
warranty claim, or RA directly from a finalized invoice (owner request 2026-06-01).

Covers the two things this feature adds (the create/money paths themselves are
unchanged and already tested):

  1. GET-side seeding — /warranty/new?invoice_id and /returns/new?invoice_id
     pre-fill the customer, carry the invoice number, and seed the invoice's
     PRODUCT lines into the picker.
  2. The After-Sale Service card on the invoice workspace — visible only on
     finalized invoices, surfaces this invoice's open cores with the inline
     "Record Return" form, and the two slide-over buttons.
  3. End-to-end link — POST /warranty/new and POST /returns/new with an
     invoice number attach the new record to the invoice (the path the UI drives).

Harness mirrors tests/test_cores_lifecycle.py: a module TestClient runs startup
against an isolated engine (seeding the admin user + core locations), and a
per-test session is opened on the same engine.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()

_HX = {"HX-Request": "true"}


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


# ── Seed helpers ────────────────────────────────────────────────────────────

_SEQ = {"n": 0}


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"AfterSale Cust {_uniq()}", is_active=True)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    n = _uniq()
    p = Product(sku=f"AS-{n:04d}", title=f"Injector {n}", cost=20.0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _invoice(db, customer, status):
    from app.models.invoice import Invoice
    inv = Invoice(invoice_number=f"INV-AS-{_uniq():04d}",
                  customer_id=customer.id, status=status)
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _line(db, invoice, product, *, line_type, qty=1, unit_price=26.0, is_core=False):
    from app.models.invoice import InvoiceLine
    ln = InvoiceLine(invoice_id=invoice.id, product_id=product.id,
                     line_type=line_type, qty=qty, unit_price=unit_price,
                     is_core_line=is_core)
    db.add(ln); db.commit(); db.refresh(ln)
    return ln


def _open_core(db, invoice_line, product, customer):
    from app.constants import CoreDirection, CoreStatus
    from app.models.core import CoreCharge
    core = CoreCharge(product_id=product.id, customer_id=customer.id,
                      invoice_line_id=invoice_line.id,
                      qty_charged=1, qty_returned=0,
                      customer_unit_charge=15.0, vendor_unit_charge=8.0,
                      direction=CoreDirection.CUSTOMER_OWES_RETURN,
                      status=CoreStatus.OPEN)
    db.add(core); db.commit(); db.refresh(core)
    return core


# ── 1. Warranty picker seeding ──────────────────────────────────────────────

def test_warranty_new_seeds_customer_invoice_and_parts(client, db):
    from app.constants import InvoiceStatus, LineType
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust, InvoiceStatus.PAID)
    _line(db, inv, prod, line_type=LineType.PRODUCT, qty=2)

    resp = client.get(f"/warranty/new?invoice_id={inv.id}", headers=_HX)
    assert resp.status_code == 200
    html = resp.text

    # Customer pre-selected via the Alpine x-data seed
    assert f"customerId: '{cust.id}'" in html
    # Invoice number carried as a hidden field for the POST
    assert f'name="invoice_number" value="{inv.invoice_number}"' in html
    # Parts seeded (non-empty seed JSON containing the product)
    assert 'id="wty-seed-lines">[]' not in html
    assert '"productId"' in html
    assert str(prod.id) in html


def test_warranty_new_without_invoice_is_blank(client, db):
    resp = client.get("/warranty/new", headers=_HX)
    assert resp.status_code == 200
    html = resp.text
    # No invoice → empty seed + no customer pre-selected
    assert 'id="wty-seed-lines">[]' in html
    assert "customerId: ''" in html
    assert 'name="invoice_number"' not in html  # hidden field only when seeded


# ── 2. Returns picker seeding ───────────────────────────────────────────────

def test_returns_new_seeds_customer_invoice_and_parts(client, db):
    from app.constants import InvoiceStatus, LineType
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust, InvoiceStatus.PAID)
    _line(db, inv, prod, line_type=LineType.PRODUCT, qty=1, unit_price=26.0)

    resp = client.get(f"/returns/new?invoice_id={inv.id}", headers=_HX)
    assert resp.status_code == 200
    html = resp.text

    assert f"customerId: '{cust.id}'" in html
    # Returns picker pre-fills the visible invoice_number input
    assert f'name="invoice_number"' in html
    assert inv.invoice_number in html
    assert 'id="ra-seed-lines">[]' not in html
    assert '"productId"' in html


def test_returns_new_seeds_only_product_lines(client, db):
    """Core/fee lines must NOT be seeded — only physical PRODUCT parts."""
    from app.constants import InvoiceStatus, LineType
    cust = _customer(db)
    part = _product(db)
    core_prod = _product(db)
    inv = _invoice(db, cust, InvoiceStatus.PAID)
    _line(db, inv, part, line_type=LineType.PRODUCT, qty=1)
    _line(db, inv, core_prod, line_type=LineType.CORE_CHARGE, qty=1, is_core=True)

    resp = client.get(f"/returns/new?invoice_id={inv.id}", headers=_HX)
    html = resp.text
    # The part is seeded; the core line's product is not in the seed JSON.
    import re
    seed = re.search(r'id="ra-seed-lines">(.*?)</script>', html, re.S).group(1)
    assert f'"productId": {part.id}' in seed or f'"productId":{part.id}' in seed
    assert f'"productId": {core_prod.id}' not in seed and f'"productId":{core_prod.id}' not in seed


# ── 3. Invoice workspace — After-Sale Service card ──────────────────────────

def test_workspace_shows_core_return_for_open_core(client, db):
    from app.constants import InvoiceStatus, LineType
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust, InvoiceStatus.OPEN)
    core_line = _line(db, inv, prod, line_type=LineType.CORE_CHARGE, qty=1, is_core=True)
    core = _open_core(db, core_line, prod, cust)

    resp = client.get(f"/invoices/{inv.id}")
    assert resp.status_code == 200
    html = resp.text

    assert "After-Sale Service" in html
    assert "Core Returns" in html
    assert f"/cores/{core.id}/return" in html          # inline return form action
    assert "Record Return" in html
    assert prod.sku in html
    # The two slide-over entry points are present too
    assert f"/warranty/new?invoice_id={inv.id}" in html
    assert f"/returns/new?invoice_id={inv.id}" in html


def test_workspace_after_sale_card_without_cores(client, db):
    from app.constants import InvoiceStatus, LineType
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust, InvoiceStatus.PAID)
    _line(db, inv, prod, line_type=LineType.PRODUCT, qty=1)

    resp = client.get(f"/invoices/{inv.id}")
    assert resp.status_code == 200
    html = resp.text

    assert "After-Sale Service" in html
    assert "Start Warranty Claim" in html
    assert "Start Return" in html
    # No open cores → no core return sub-block. (The "/cores/" sidebar nav link
    # and the "/returns/new" button are always present, so assert on the
    # core-specific markers — the sub-block label and the form's button text.)
    assert "Core Returns" not in html
    assert "Record Return" not in html


def test_workspace_draft_hides_after_sale_card(client, db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    inv = _invoice(db, cust, InvoiceStatus.DRAFT)

    resp = client.get(f"/invoices/{inv.id}")
    assert resp.status_code == 200
    assert "After-Sale Service" not in resp.text


# ── 4. End-to-end link (the path the new UI drives) ─────────────────────────

def test_post_warranty_links_invoice(client, db):
    from app.constants import InvoiceStatus, LineType
    from app.models.warranty import WarrantyClaim
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust, InvoiceStatus.PAID)
    _line(db, inv, prod, line_type=LineType.PRODUCT, qty=1)

    resp = client.post(
        "/warranty/new",
        data={
            "customer_id": str(cust.id),
            "invoice_number": inv.invoice_number,
            "failure_description": "Injector failed under warranty",
            "product_id[]": str(prod.id),
            "qty_claimed[]": "1",
            "credit_amount[]": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    claim = (db.query(WarrantyClaim)
             .filter(WarrantyClaim.customer_id == cust.id)
             .order_by(WarrantyClaim.id.desc()).first())
    assert claim is not None
    assert claim.invoice_id == inv.id


def test_post_returns_links_invoice(client, db):
    from app.constants import InvoiceStatus, LineType, ReturnDisposition
    from app.models.returns import ReturnAuthorization
    cust = _customer(db)
    prod = _product(db)
    inv = _invoice(db, cust, InvoiceStatus.PAID)
    _line(db, inv, prod, line_type=LineType.PRODUCT, qty=1)

    resp = client.post(
        "/returns/new",
        data={
            "customer_id": str(cust.id),
            "invoice_number": inv.invoice_number,
            "reason": "Wrong part ordered",
            "product_id[]": str(prod.id),
            "qty[]": "1",
            "unit_price[]": "26.00",
            "restocking_fee[]": "0",
            "disposition[]": ReturnDisposition.QUARANTINE,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    ra = (db.query(ReturnAuthorization)
          .filter(ReturnAuthorization.customer_id == cust.id)
          .order_by(ReturnAuthorization.id.desc()).first())
    assert ra is not None
    assert ra.invoice_id == inv.id
