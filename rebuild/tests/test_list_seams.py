"""
tests/test_list_seams.py
========================
Four list/data seams:
  #5 Customer account_number  (column + create/update bind + customer & invoice search)
  #4 List sort param          (apply_sort helper + customers/invoices/quotes/payments)
  #7 Category tree            (ProductService.category_tree for the picker)
  #8 Unified timeline         (CRMService.get_unified_timeline merges docs + activities)
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    InvoiceStatus, LineType, QuoteStatus, SOStatus, PaymentMethod, PaymentStatus,
    ActivityType,
)
from app.models.customer import Customer, CustomerCallLog
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.models.quote import Quote, SalesOrder
from app.models.product import Product, ProductCategory
from app.services.product_service import ProductService
from app.services.crm_service import CRMService
from app.utils import apply_sort
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _cust(db, **kw):
    kw.setdefault("company_name", f"Co {next(_seq)}")
    c = Customer(**kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


# ── #5 account_number ──────────────────────────────────────────────────────────

def test_create_binds_account_number(client, db):
    r = client.post("/customers/new",
                    data={"company_name": "Acct Create Co", "account_number": "AR-7001"},
                    follow_redirects=False)
    assert r.status_code == 303
    c = db.query(Customer).filter(Customer.company_name == "Acct Create Co").first()
    assert c.account_number == "AR-7001"


def test_update_binds_account_number(client, db):
    c = _cust(db)
    r = client.post(f"/customers/{c.id}",
                    data={"company_name": c.company_name, "account_number": "AR-7002"},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Customer).get(c.id).account_number == "AR-7002"


def test_customer_search_by_account_number(client, db):
    c = _cust(db, account_number="ZZACCT9999")
    html = client.get("/customers/?q=ZZACCT9999").text
    assert c.company_name in html


def test_invoice_search_by_account_number(client, db):
    c = _cust(db, account_number="ZZINV8888")
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=c.id,
                  status=InvoiceStatus.OPEN, is_taxable=False)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=10.0,
                                 is_taxable=False))
    db.add(inv); db.commit()
    html = client.get("/invoices/?q=ZZINV8888").text
    assert inv.invoice_number in html


# ── #4 sort ────────────────────────────────────────────────────────────────────

def test_apply_sort_whitelist_and_direction(db):
    allowed = {"created": Invoice.created_at, "number": Invoice.invoice_number}
    # unknown key → default; unknown direction → asc
    _, k, d = apply_sort(db.query(Invoice), allowed, "bogus", "sideways", default="number")
    assert k == "number" and d == "asc"
    _, k, d = apply_sort(db.query(Invoice), allowed, "created", "DESC", default="number")
    assert k == "created" and d == "desc"


@pytest.mark.parametrize("path", ["/customers/", "/invoices/", "/quotes/", "/payments/"])
def test_lists_accept_sort_params(client, path):
    assert client.get(f"{path}?sort=bogus&direction=desc").status_code == 200


# Row-order + direction for ALL four lists (customers/invoices/quotes/payments)
# is asserted ROBUSTLY in tests/test_list_sort_ordering.py — function-scoped
# engine + row-id extraction, mirroring test_product_list_sort. The prior
# html.index()-under-a-module-engine assertion here flaked in the full suite
# (conftest cross-module engine-repoint hazard, "failing set shifts run-to-run");
# the customer-sort feature itself is correct (customers.py applies reverse=desc).
# Removed and superseded, not chased.


# ── #7 category tree ───────────────────────────────────────────────────────────

def test_category_tree_nested(db):
    parent = ProductCategory(name="Engine Parts", level=1, is_active=True)
    db.add(parent); db.commit(); db.refresh(parent)
    db.add(ProductCategory(name="Pistons", parent_id=parent.id, level=2, is_active=True))
    db.add(ProductCategory(name="Bearings", parent_id=parent.id, level=2, is_active=True))
    db.commit()
    tree = ProductService(db).category_tree()
    node = next(n for n in tree if n["name"] == "Engine Parts")
    child_names = sorted(c["name"] for c in node["children"])
    assert child_names == ["Bearings", "Pistons"]
    assert node["children"][0]["full_path"].startswith("Engine Parts → ")


def test_product_accepts_subcategory_id(db):
    parent = ProductCategory(name="Fuel", level=1, is_active=True)
    db.add(parent); db.commit(); db.refresh(parent)
    sub = ProductCategory(name="Injectors", parent_id=parent.id, level=2, is_active=True)
    db.add(sub); db.commit(); db.refresh(sub)
    p = Product(sku=f"SUBCAT-{next(_seq)}", title="part", cost=1.0, category_id=sub.id)
    db.add(p); db.commit(); db.refresh(p)
    assert p.category_id == sub.id            # a subcategory is a valid category_id


# ── #8 unified timeline ────────────────────────────────────────────────────────

def test_unified_timeline_merges_all_kinds(db):
    c = _cust(db)
    db.add(Quote(quote_number=f"Q-{next(_seq)}", customer_id=c.id,
                 created_at=datetime(2026, 1, 1)))
    db.add(SalesOrder(so_number=f"SO-{next(_seq)}", customer_id=c.id,
                      status=SOStatus.OPEN, created_at=datetime(2026, 1, 2)))
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=c.id,
                  status=InvoiceStatus.OPEN, is_taxable=False, created_at=datetime(2026, 1, 3))
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=10.0,
                                 is_taxable=False))
    db.add(inv)
    db.add(Payment(customer_id=c.id, amount_received=25.0, payment_method=PaymentMethod.CASH,
                   status=PaymentStatus.APPLIED, payment_date=datetime(2026, 1, 4)))
    db.add(CustomerCallLog(customer_id=c.id, activity_type=ActivityType.NOTE,
                           notes="called", logged_at=datetime(2026, 1, 5)))
    db.commit()

    tl = CRMService(db).get_unified_timeline(c.id)
    kinds = {e["kind"] for e in tl}
    assert {"quote", "so", "invoice", "payment", "activity"} <= kinds
    # newest first → the 2026-01-05 activity leads, the 2026-01-01 quote trails
    assert tl[0]["kind"] == "activity"
    assert tl[-1]["kind"] == "quote"
    # entries carry when/label/amount
    inv_entry = next(e for e in tl if e["kind"] == "invoice")
    assert inv_entry["label"] == inv.invoice_number
    assert inv_entry["amount"] == inv.total
