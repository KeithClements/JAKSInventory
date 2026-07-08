"""
tests/test_review_break_case_guards.py
=======================================
Regression guards for the "break test" cases raised in the 2026-07-07 external
code review. That review ran against a month-stale worktree (commit 9f50216,
2026-06-05) and most of its findings were already fixed on the tip — negative
product cost (see test_product_validation.py), QBO token encryption, real
Alembic migrations, admin-gated demo reset. These tests pin the handful of
items that had genuine residual merit so they can't regress:

  A. Quote line qty can't go negative/zero — this is what closed the review's
     "negative qty × negative price → positive total" exploit at its root
     (validate_line_qty rejects < 1, so the two-negatives trick can't arise).
  B. PO receiving refuses an all-zero/empty submission instead of persisting an
     empty POReceipt header (audit noise, zero inventory effect).
  C. Customer create refuses a record with neither a company nor a contact name
     (an unidentifiable AR row).
  D. /invoices/new opened directly (no HTMX) renders inside the app shell, not
     as a chrome-less fragment; the HTMX slide-over still gets the bare partial.

Run (isolated in-memory engine — never touches jaks.db):
    .venv\\Scripts\\python.exe -m pytest tests/test_review_break_case_guards.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


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


# ════════════════════════════════════════════════════════════════════════════
# A. Line quantity floor — the shared contract behind quote/invoice/SO lines.
#    A negative qty is REJECTED (not silently clamped), so a crafted POST can't
#    pair a negative qty with a negative price to fabricate a positive total.
# ════════════════════════════════════════════════════════════════════════════

def test_validate_line_qty_rejects_zero_and_negative():
    from app.utils import validate_line_qty
    for bad in (0, -1, -3, "-5"):
        with pytest.raises(ValueError):
            validate_line_qty(bad)


def test_validate_line_qty_rejects_non_integer():
    from app.utils import validate_line_qty
    for bad in ("abc", None, "1.5"):
        with pytest.raises(ValueError):
            validate_line_qty(bad)


def test_validate_line_qty_accepts_one_and_positive():
    from app.utils import validate_line_qty
    assert validate_line_qty(1) == 1
    assert validate_line_qty("7") == 7


def test_quote_add_line_rejects_negative_qty(db):
    """Integration: the guard is wired into QuoteService.add_line, so a negative
    qty never reaches the DB even via the service layer."""
    from app.constants import QuoteStatus
    from app.models.customer import Customer
    from app.models.quote import Quote, QuoteLine
    from app.services.quote_service import QuoteService

    n = next(_seq)
    cust = Customer(company_name=f"QtyGuard {n}")
    db.add(cust); db.flush()
    q = Quote(quote_number=f"Q-QTYGUARD-{n:05d}", customer_id=cust.id,
              status=QuoteStatus.DRAFT)
    db.add(q); db.commit()

    with pytest.raises(ValueError):
        QuoteService(db, current_user_id=1).add_line(
            q.id, None, {"description": "gamed", "qty": -3, "unit_price": 100.0},
        )
    db.rollback()
    # Nothing persisted from the rejected add.
    assert db.query(QuoteLine).filter(QuoteLine.quote_id == q.id).count() == 0


def test_quote_add_line_accepts_positive_qty(db):
    from app.constants import QuoteStatus
    from app.models.customer import Customer
    from app.models.quote import Quote
    from app.services.quote_service import QuoteService

    n = next(_seq)
    cust = Customer(company_name=f"QtyOK {n}")
    db.add(cust); db.flush()
    q = Quote(quote_number=f"Q-QTYOK-{n:05d}", customer_id=cust.id,
              status=QuoteStatus.DRAFT)
    db.add(q); db.commit()

    lines = QuoteService(db, current_user_id=1).add_line(
        q.id, None, {"description": "labor", "qty": 2, "unit_price": 100.0},
    )
    assert lines[0].qty == 2


# ════════════════════════════════════════════════════════════════════════════
# B. PO receiving — an all-zero / empty submission is refused, no phantom
#    receipt header is written.
# ════════════════════════════════════════════════════════════════════════════

def _sent_po_with_line(db):
    from app.constants import POStatus, ProductStatus
    from app.models.product import Product
    from app.models.purchase_order import POLine, PurchaseOrder
    from app.models.vendor import Vendor

    n = next(_seq)
    vendor = Vendor(name=f"RecvGuard-{n}")
    db.add(vendor); db.flush()
    product = Product(sku=f"RCVG-{n:04d}", title=f"Recv Part {n}",
                      cost=10.0, last_cost=10.0, markup_pct=50.0,
                      qty_on_hand=0, qty_committed=0,
                      status=ProductStatus.ACTIVE, is_active=True)
    db.add(product); db.flush()
    po = PurchaseOrder(vendor_id=vendor.id, po_number=f"RCVG-PO-{n:04d}",
                       status=POStatus.SENT)
    db.add(po); db.flush()
    line = POLine(po_id=po.id, product_id=product.id, description="test",
                  qty_ordered=5, unit_cost=10.0)
    db.add(line); db.flush()
    db.commit()
    return vendor, line


def test_create_receipt_rejects_all_zero_quantities(db):
    from app.models.purchase_order import POReceipt
    from app.services.po_service import POService

    vendor, line = _sent_po_with_line(db)
    before = db.query(POReceipt).count()

    with pytest.raises(ValueError, match="[Nn]othing to receive"):
        POService(db, current_user_id=1).create_receipt(
            vendor_id=vendor.id, po_line_quantities={line.id: 0}, data={},
        )
    db.rollback()
    # No phantom receipt header persisted.
    assert db.query(POReceipt).count() == before


def test_create_receipt_rejects_empty_dict(db):
    from app.models.purchase_order import POReceipt
    from app.services.po_service import POService

    vendor, line = _sent_po_with_line(db)
    before = db.query(POReceipt).count()

    with pytest.raises(ValueError, match="[Nn]othing to receive"):
        POService(db, current_user_id=1).create_receipt(
            vendor_id=vendor.id, po_line_quantities={}, data={},
        )
    db.rollback()
    assert db.query(POReceipt).count() == before


def test_create_receipt_still_accepts_a_positive_quantity(db):
    """The guard must not block a real receive — one positive qty succeeds and
    moves inventory."""
    from app.models.product import Product
    from app.models.purchase_order import POLine
    from app.services.po_service import POService

    vendor, line = _sent_po_with_line(db)
    receipt = POService(db, current_user_id=1).create_receipt(
        vendor_id=vendor.id, po_line_quantities={line.id: 3}, data={},
    )
    db.commit()
    assert receipt.id is not None
    fresh_line = db.query(POLine).filter(POLine.id == line.id).first()
    assert fresh_line.qty_received == 3
    assert db.query(Product).filter(Product.id == fresh_line.product_id).first().qty_on_hand == 3


# ════════════════════════════════════════════════════════════════════════════
# C. Customer create — at least one of company / contact name is required.
# ════════════════════════════════════════════════════════════════════════════

def test_customer_create_rejects_blank_company_and_contact(client, db):
    from app.models.customer import Customer
    before = db.query(Customer).count()
    r = client.post("/customers/new", data={
        "company_name": "",
        "contact_name": "",
        "email": "",
    })
    assert r.status_code == 422, f"expected 422 for a nameless customer, got {r.status_code}"
    db.expire_all()
    assert db.query(Customer).count() == before, "a nameless customer was created"


def test_customer_create_accepts_company_only(client, db):
    from app.models.customer import Customer
    n = next(_seq)
    r = client.post("/customers/new", data={
        "company_name": f"CompanyOnly {n}",
        "contact_name": "",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    assert db.query(Customer).filter(Customer.company_name == f"CompanyOnly {n}").count() == 1


def test_customer_create_accepts_contact_only(client, db):
    """An individual walk-in with only a contact name is legitimate — allowed."""
    from app.models.customer import Customer
    n = next(_seq)
    r = client.post("/customers/new", data={
        "company_name": "",
        "contact_name": f"Walkin Person {n}",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    assert db.query(Customer).filter(Customer.contact_name == f"Walkin Person {n}").count() == 1


# ════════════════════════════════════════════════════════════════════════════
# D. /invoices/new — full shell on direct navigation, bare partial for HTMX.
# ════════════════════════════════════════════════════════════════════════════

def test_invoices_new_direct_nav_renders_full_shell(client):
    r = client.get("/invoices/new")
    assert r.status_code == 200
    # Full app shell present (base.html), not a chrome-less fragment.
    assert "<html" in r.text.lower()
    # And it is the picker, wrapped.
    assert 'action="/invoices/new"' in r.text


def test_invoices_new_htmx_returns_bare_partial(client):
    r = client.get("/invoices/new", headers={"HX-Request": "true"})
    assert r.status_code == 200
    # HTMX swap target gets the bare form only — no document shell.
    assert "<html" not in r.text.lower()
    assert 'action="/invoices/new"' in r.text
