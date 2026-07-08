"""
tests/test_r3_serial_numbers.py
===============================
R3 — serial number tracking write paths (ProductSerialNumber was a dead
scaffold: model existed, zero write paths — for cylinder heads this is
warranty/liability data).

Covers the three lifecycle touchpoints:
  1. PO receive captures serials → IN_STOCK, linked to the POReceiptLine
  2. Invoice finalize FIFO-assigns serials → SOLD, invoice_line_id set
  3. Invoice void releases serials → back to IN_STOCK, link cleared

Plus the tolerance/fail-safe rules:
  - duplicate serial for the same product is skipped (not fatal)
  - receiving with no serials stays allowed
  - serial-count mismatch on receive is allowed but flashed as info note
  - fewer serials than invoice qty → assign what's there, finalize proceeds
  - non-serialized products are never touched
  - a serial failure must NEVER block invoice finalize (money path)

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r3_serial_numbers.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # admin, seeded by startup hook
_SEQ = itertools.count(1)


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

def _vendor(db):
    from app.models.vendor import Vendor
    n = next(_SEQ)
    v = Vendor(name=f"Serial Vendor {n}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _customer(db):
    from app.models.customer import Customer
    n = next(_SEQ)
    c = Customer(company_name=f"Serial Cust {n}", contact_name="QA",
                 email=f"serial{n}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, serialized=True, qty_on_hand=10):
    from app.models.product import Product
    n = next(_SEQ)
    p = Product(
        sku=f"SER-{n:05d}",
        title=f"Cylinder Head {n}",
        cost=100.0,
        qty_on_hand=qty_on_hand,
        qty_committed=0,
        has_serial_number=serialized,
        is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def _sent_po(db, vendor, product, qty=3, unit_cost=100.0):
    """A SENT PO with one line for `product` — ready to receive."""
    from app.constants import POStatus
    from app.models.purchase_order import POLine, PurchaseOrder
    n = next(_SEQ)
    po = PurchaseOrder(po_number=f"PO-R3-{n:04d}", vendor_id=vendor.id,
                       status=POStatus.SENT)
    db.add(po); db.flush()
    line = POLine(po_id=po.id, product_id=product.id,
                  description=product.title, qty_ordered=qty,
                  unit_cost=unit_cost)
    db.add(line); db.commit(); db.refresh(po); db.refresh(line)
    return po, line


def _draft_invoice(db, customer_id, product, qty=1, price=500.0):
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine
    n = next(_SEQ)
    inv = Invoice(
        invoice_number=f"INV-R3-{n:04d}",
        customer_id=customer_id,
        status=InvoiceStatus.DRAFT,
        tax_rate=0.0,
        tax_rate_snapshot=0.0,
        tax_exempt_snapshot=True,
    )
    db.add(inv); db.flush()
    line = InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        product_id=product.id,
        description=product.title,
        qty=qty,
        unit_price=price,
        is_taxable=False,
    )
    db.add(line); db.commit(); db.refresh(inv); db.refresh(line)
    return inv, line


def _serials_for(db, product_id):
    from app.models.product import ProductSerialNumber
    return (
        db.query(ProductSerialNumber)
        .filter(ProductSerialNumber.product_id == product_id)
        .order_by(ProductSerialNumber.id.asc())
        .all()
    )


def _seed_in_stock(db, product_id, serials):
    """Record IN_STOCK serials directly via the service (no receipt context)."""
    from app.services.serial_service import SerialService
    result = SerialService(db, _UID).record_received_serials(
        product_id=product_id, serials=list(serials)
    )
    db.commit()
    return result


# ---------------------------------------------------------------------------
# 1. Receive captures serials → IN_STOCK, linked to the receipt line
# ---------------------------------------------------------------------------

def test_receive_captures_serials_in_stock_linked_to_receipt_line(client, db):
    from app.constants import SerialNumberStatus
    from app.models.purchase_order import POReceiptLine

    vendor = _vendor(db)
    product = _product(db, serialized=True, qty_on_hand=0)
    po, line = _sent_po(db, vendor, product, qty=2)

    resp = client.post(
        f"/purchase-orders/{po.id}/receive",
        data={
            f"recv_{line.id}": "2",
            # mixed comma/newline separation + messy whitespace/case
            f"serials_{line.id}": " sn-001 ,\nsn-002 ",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=received" in resp.headers["location"]
    assert "info=" not in resp.headers["location"]  # counts match → no note

    db.expire_all()
    rows = _serials_for(db, product.id)
    assert [r.serial_number for r in rows] == ["SN-001", "SN-002"]  # trimmed + uppercased
    assert all(r.status == SerialNumberStatus.IN_STOCK for r in rows)

    receipt_line = (
        db.query(POReceiptLine).filter(POReceiptLine.po_line_id == line.id).first()
    )
    assert receipt_line is not None
    assert all(r.po_receipt_line_id == receipt_line.id for r in rows)
    assert all(r.invoice_line_id is None for r in rows)


# ---------------------------------------------------------------------------
# 2. Receiving with NO serials stays allowed (units just unconsumed)
# ---------------------------------------------------------------------------

def test_receive_without_serials_is_allowed(client, db):
    vendor = _vendor(db)
    product = _product(db, serialized=True, qty_on_hand=0)
    po, line = _sent_po(db, vendor, product, qty=2)

    resp = client.post(
        f"/purchase-orders/{po.id}/receive",
        data={f"recv_{line.id}": "2"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=received" in resp.headers["location"]

    db.expire_all()
    assert _serials_for(db, product.id) == []
    # The goods receipt itself happened — inventory went up.
    from app.models.product import Product
    assert db.query(Product).filter(Product.id == product.id).first().qty_on_hand == 2


# ---------------------------------------------------------------------------
# 3. Serial count ≠ qty received → allowed, flashed as info note
# ---------------------------------------------------------------------------

def test_serial_count_mismatch_is_allowed_with_info_note(client, db):
    from app.constants import SerialNumberStatus

    vendor = _vendor(db)
    product = _product(db, serialized=True, qty_on_hand=0)
    po, line = _sent_po(db, vendor, product, qty=3)

    resp = client.post(
        f"/purchase-orders/{po.id}/receive",
        data={
            f"recv_{line.id}": "3",
            f"serials_{line.id}": "MM-1, MM-2",  # 2 serials for 3 units
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "ok=received" in loc      # receive itself still succeeds
    assert "info=" in loc            # ...but the mismatch is surfaced

    db.expire_all()
    rows = _serials_for(db, product.id)
    assert [r.serial_number for r in rows] == ["MM-1", "MM-2"]
    assert all(r.status == SerialNumberStatus.IN_STOCK for r in rows)


# ---------------------------------------------------------------------------
# 3b. Receive form renders the serial textarea ONLY for serialized products
# ---------------------------------------------------------------------------

def test_receive_form_shows_serial_textarea_only_for_serialized(client, db):
    vendor = _vendor(db)
    ser_prod = _product(db, serialized=True, qty_on_hand=0)
    po_ser, line_ser = _sent_po(db, vendor, ser_prod, qty=1)

    page = client.get(f"/purchase-orders/{po_ser.id}")
    assert page.status_code == 200
    assert f'name="serials_{line_ser.id}"' in page.text

    plain_prod = _product(db, serialized=False, qty_on_hand=0)
    po_plain, line_plain = _sent_po(db, vendor, plain_prod, qty=1)

    page = client.get(f"/purchase-orders/{po_plain.id}")
    assert page.status_code == 200
    assert f'name="serials_{line_plain.id}"' not in page.text
    assert f'name="recv_{line_plain.id}"' in page.text  # receive form still renders


# ---------------------------------------------------------------------------
# 4. Duplicate serial for the same product is skipped (not fatal)
# ---------------------------------------------------------------------------

def test_duplicate_serial_same_product_skipped(db):
    product = _product(db, serialized=True)

    first = _seed_in_stock(db, product.id, ["DUP-100"])
    assert first == {"recorded": ["DUP-100"], "skipped": []}

    # Same serial again (different case/whitespace) → skipped, not duplicated
    second = _seed_in_stock(db, product.id, [" dup-100 ", "DUP-101"])
    assert second["skipped"] == ["DUP-100"]
    assert second["recorded"] == ["DUP-101"]

    rows = _serials_for(db, product.id)
    assert [r.serial_number for r in rows] == ["DUP-100", "DUP-101"]

    # The same serial string on a DIFFERENT product is fine (unique per product)
    other = _product(db, serialized=True)
    third = _seed_in_stock(db, other.id, ["DUP-100"])
    assert third == {"recorded": ["DUP-100"], "skipped": []}


# ---------------------------------------------------------------------------
# 5. Finalize FIFO-assigns serials to the invoice line
# ---------------------------------------------------------------------------

def test_finalize_fifo_assigns_serials_to_invoice_line(db):
    from app.constants import InvoiceStatus, SerialNumberStatus
    from app.services.invoice_service import InvoiceService

    product = _product(db, serialized=True, qty_on_hand=10)
    _seed_in_stock(db, product.id, ["FIFO-1", "FIFO-2", "FIFO-3"])

    cust = _customer(db)
    inv, line = _draft_invoice(db, cust.id, product, qty=2)
    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()

    from app.models.invoice import Invoice
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().status == InvoiceStatus.OPEN

    rows = _serials_for(db, product.id)
    # Oldest two (lowest ids) are SOLD + linked; the third stays IN_STOCK
    assert rows[0].status == SerialNumberStatus.SOLD and rows[0].invoice_line_id == line.id
    assert rows[1].status == SerialNumberStatus.SOLD and rows[1].invoice_line_id == line.id
    assert rows[2].status == SerialNumberStatus.IN_STOCK and rows[2].invoice_line_id is None
    assert [r.serial_number for r in rows[:2]] == ["FIFO-1", "FIFO-2"]


# ---------------------------------------------------------------------------
# 6. Fewer serials than qty → tolerated, finalize proceeds
# ---------------------------------------------------------------------------

def test_finalize_tolerates_fewer_serials_than_qty(db):
    from app.constants import InvoiceStatus, SerialNumberStatus
    from app.services.invoice_service import InvoiceService

    product = _product(db, serialized=True, qty_on_hand=10)
    _seed_in_stock(db, product.id, ["ONLY-1"])  # 1 serial, invoicing 3 units

    cust = _customer(db)
    inv, line = _draft_invoice(db, cust.id, product, qty=3)
    InvoiceService(db, _UID).finalise(inv.id)  # must NOT raise
    db.expire_all()

    from app.models.invoice import Invoice
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().status == InvoiceStatus.OPEN

    rows = _serials_for(db, product.id)
    assert len(rows) == 1
    assert rows[0].status == SerialNumberStatus.SOLD
    assert rows[0].invoice_line_id == line.id


# ---------------------------------------------------------------------------
# 7. Void releases serials back to IN_STOCK
# ---------------------------------------------------------------------------

def test_void_releases_serials_back_to_in_stock(db):
    from app.constants import InvoiceStatus, SerialNumberStatus
    from app.services.invoice_service import InvoiceService

    product = _product(db, serialized=True, qty_on_hand=10)
    _seed_in_stock(db, product.id, ["VOID-1", "VOID-2"])

    cust = _customer(db)
    inv, line = _draft_invoice(db, cust.id, product, qty=2)
    svc = InvoiceService(db, _UID)
    svc.finalise(inv.id)
    db.expire_all()

    rows = _serials_for(db, product.id)
    assert all(r.status == SerialNumberStatus.SOLD for r in rows)

    svc.void_invoice(inv.id, reason="test void")
    db.expire_all()

    from app.models.invoice import Invoice
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().status == InvoiceStatus.VOID

    rows = _serials_for(db, product.id)
    assert all(r.status == SerialNumberStatus.IN_STOCK for r in rows)
    assert all(r.invoice_line_id is None for r in rows)


# ---------------------------------------------------------------------------
# 8. Non-serialized products are never touched
# ---------------------------------------------------------------------------

def test_non_serial_product_untouched_by_finalize(db):
    from app.constants import InvoiceStatus, SerialNumberStatus
    from app.services.invoice_service import InvoiceService

    product = _product(db, serialized=False, qty_on_hand=10)
    # Even if stray serial rows exist for it, the has_serial_number gate
    # means finalize must leave them alone.
    _seed_in_stock(db, product.id, ["STRAY-1"])

    cust = _customer(db)
    inv, line = _draft_invoice(db, cust.id, product, qty=1)
    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()

    from app.models.invoice import Invoice
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().status == InvoiceStatus.OPEN

    rows = _serials_for(db, product.id)
    assert len(rows) == 1
    assert rows[0].status == SerialNumberStatus.IN_STOCK
    assert rows[0].invoice_line_id is None


# ---------------------------------------------------------------------------
# 9. A serial failure must NEVER block finalize (money path)
# ---------------------------------------------------------------------------

def test_serial_failure_never_blocks_finalize(db, monkeypatch):
    from app.constants import InvoiceStatus
    from app.services.invoice_service import InvoiceService
    from app.services.serial_service import SerialService

    def _boom(self, invoice):
        raise RuntimeError("simulated serial subsystem failure")

    monkeypatch.setattr(SerialService, "assign_serials_for_invoice", _boom)

    product = _product(db, serialized=True, qty_on_hand=10)
    _seed_in_stock(db, product.id, ["FAIL-1"])

    cust = _customer(db)
    inv, _line = _draft_invoice(db, cust.id, product, qty=1)
    InvoiceService(db, _UID).finalise(inv.id)  # must NOT raise
    db.expire_all()

    from app.models.invoice import Invoice
    inv_r = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv_r.status == InvoiceStatus.OPEN

    # Inventory math ran normally despite the serial failure
    from app.models.product import Product
    assert db.query(Product).filter(Product.id == product.id).first().qty_on_hand == 9
