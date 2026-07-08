"""
tests/test_phase3_serial_lookup.py
==================================
§23.3 Phase 3 — ProductSerialNumber lookup screen (cylinder-head chain of
custody). GET /serials/ is READ-ONLY: search by serial or SKU, each row shows
received (PO + vendor + date), status, sold (invoice + customer), and any
warranty claims filed against that serial.
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


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"P3 SL Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"P3 SL Cust {_uniq()}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"P3SL-{_uniq():04d}", title="Cyl Head", description="Cyl Head",
                cost=800.0, markup_pct=30.0, qty_on_hand=1,
                status=ProductStatus.ACTIVE, is_active=True,
                has_serial_number=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _receipt_line(db, *, vendor, product):
    """Minimal PO → receipt chain so the serial has a provenance link."""
    from app.models.purchase_order import (
        POLine, POReceipt, POReceiptLine, PurchaseOrder,
    )
    po = PurchaseOrder(po_number=f"PO-P3SL-{_uniq():05d}", vendor_id=vendor.id)
    db.add(po); db.flush()
    po_line = POLine(po_id=po.id, product_id=product.id, qty_ordered=1,
                     qty_received=1, unit_cost=800.0)
    db.add(po_line); db.flush()
    receipt = POReceipt(vendor_id=vendor.id)
    db.add(receipt); db.flush()
    rl = POReceiptLine(receipt_id=receipt.id, po_id=po.id, po_line_id=po_line.id,
                       qty_received=1)
    db.add(rl); db.commit(); db.refresh(rl)
    return po, rl


def _sold_invoice_line(db, *, customer, product):
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine
    inv = Invoice(invoice_number=f"INV-P3SL-{_uniq():05d}", customer_id=customer.id,
                  status=InvoiceStatus.PAID, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    line = InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT,
                       product_id=product.id, description=product.title,
                       qty=1, unit_price=1100.0, unit_cost=800.0,
                       is_taxable=False)
    db.add(line); db.commit(); db.refresh(line)
    return inv, line


def _serial(db, *, product, serial, status=None, receipt_line=None,
            invoice_line=None):
    from app.constants import SerialNumberStatus
    from app.models.product import ProductSerialNumber
    sn = ProductSerialNumber(
        product_id=product.id,
        serial_number=serial.upper(),
        status=status or SerialNumberStatus.IN_STOCK,
        po_receipt_line_id=receipt_line.id if receipt_line else None,
        invoice_line_id=invoice_line.id if invoice_line else None,
    )
    db.add(sn); db.commit(); db.refresh(sn)
    return sn


# ===========================================================================

def test_lookup_page_renders_without_query(client, db):
    prod = _product(db)
    _serial(db, product=prod, serial=f"SL-EMPTYQ-{_uniq()}")
    r = client.get("/serials/")
    assert r.status_code == 200
    assert "Serial Number Lookup" in r.text


def test_full_chain_of_custody_row(client, db):
    from app.constants import SerialNumberStatus
    ven = _vendor(db); cust = _customer(db); prod = _product(db)
    po, rl = _receipt_line(db, vendor=ven, product=prod)
    inv, inv_line = _sold_invoice_line(db, customer=cust, product=prod)
    serial = f"SL-CHAIN-{_uniq()}"
    _serial(db, product=prod, serial=serial, status=SerialNumberStatus.SOLD,
            receipt_line=rl, invoice_line=inv_line)

    r = client.get(f"/serials/?q={serial}")
    assert r.status_code == 200
    # received side: PO number + vendor
    assert po.po_number in r.text
    assert ven.name in r.text
    # sold side: invoice + customer
    assert inv.invoice_number in r.text
    assert cust.company_name in r.text
    # status chip
    assert "Sold" in r.text


def test_search_by_sku_and_serial_fragment(client, db):
    prod = _product(db)
    serial = f"SL-FRAG-{_uniq()}"
    _serial(db, product=prod, serial=serial)

    by_serial = client.get(f"/serials/?q={serial[:10]}")
    assert by_serial.status_code == 200 and serial in by_serial.text

    by_sku = client.get(f"/serials/?q={prod.sku}")
    assert by_sku.status_code == 200 and serial in by_sku.text


def test_warranty_claim_appears_on_serial(client, db):
    from app.services.warranty_service import WarrantyService
    ven = _vendor(db); cust = _customer(db); prod = _product(db)
    serial = f"SL-WC-{_uniq()}"
    _serial(db, product=prod, serial=serial)
    claim = WarrantyService(db, None).create_claim(
        customer_id=cust.id, invoice_id=None, vendor_id=ven.id,
        failure_description="cracked", esn="ESN-SL-1",
        lines=[{"product_id": prod.id, "qty_claimed": 1,
                "serial_number": serial.lower()}],  # case-insensitive match
    )

    r = client.get(f"/serials/?q={serial}")
    assert r.status_code == 200
    assert claim.claim_number in r.text


def test_no_match_empty_state(client, db):
    r = client.get("/serials/?q=ZZZ-NO-SUCH-SERIAL")
    assert r.status_code == 200
    assert "No serials match" in r.text
