"""
tests/test_credit_status_seam.py
================================
Phase-2 §4.5 seam — the credit warn-only check wired into the SO + invoice
workspaces (sales_orders.py::so_workspace, invoices.py::_workspace_context).

The SERVICE (CustomerService.credit_status) is guarded by test_customer_credit +
test_phase2_foundations_gaps. This pins the SEAM: that the workspace routes feed
it the right PROSPECTIVE amount —
  • SO:      so.subtotal                     (whole SO is prospective; not invoiced)
  • Invoice: invoice.total if DRAFT else 0   (a POSTED invoice is already in open
             AR — passing its total again would DOUBLE-COUNT the customer)

KNOWN GAP (flagged for UI lane — NOT a failing test): the routes put
`credit_status` in context but neither workspace template renders `credit_warn()`
yet, so the warning is computed and discarded. When the macro is wired in, add an
HTML-level assertion here.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_credit_status_seam.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, LineType, SOLineStatus, SOStatus
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.quote import SalesOrder, SOLine
from app.routers.invoices import _workspace_context
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


def _customer(db, **kw) -> Customer:
    c = Customer(company_name=f"Seam Co {next(_seq)}", contact_name="QA", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _invoice(db, customer_id, amount, status) -> Invoice:
    inv = Invoice(invoice_number=f"INV-SEAM-{next(_seq):05d}", customer_id=customer_id,
                  status=status, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


# ── Invoice seam — exercises the REAL _workspace_context credit_status code ───

def test_invoice_seam_draft_over_limit_warns(db):
    """A DRAFT invoice isn't in open AR yet, so its total IS the prospective
    charge — a draft over the limit must warn."""
    cust = _customer(db, credit_limit=1000.0)
    inv = _invoice(db, cust.id, 1500.0, InvoiceStatus.DRAFT)
    cs = _workspace_context(db, None, inv)["credit_status"]
    assert cs is not None
    assert cs["open_ar"] == 0.0          # the draft itself is not in AR
    assert cs["over_limit"] is True
    assert cs["warn"] is True


def test_invoice_seam_posted_invoice_does_not_double_count(db):
    """A POSTED invoice is already counted in open AR, so the seam passes
    prospective 0. An $800 posted invoice under a $1000 limit must NOT warn —
    if the seam wrongly passed its total, 800 + 800 = 1600 would falsely trip."""
    cust = _customer(db, credit_limit=1000.0)
    inv = _invoice(db, cust.id, 800.0, InvoiceStatus.OPEN)
    cs = _workspace_context(db, None, inv)["credit_status"]
    assert cs["open_ar"] == 800.0        # the posted invoice IS the open AR
    assert cs["over_limit"] is False
    assert cs["warn"] is False


def test_invoice_seam_no_limit_customer_never_warns(db):
    cust = _customer(db, credit_limit=0.0)          # 0 = no limit enforced
    inv = _invoice(db, cust.id, 99999.0, InvoiceStatus.DRAFT)
    cs = _workspace_context(db, None, inv)["credit_status"]
    assert cs["available_credit"] is None
    assert cs["warn"] is False


# ── SO seam — the route wires credit_status(so.customer, so.subtotal) ────────

def test_so_workspace_over_limit_renders_without_error(client, db):
    """so_workspace computes credit_status(so.customer, so.subtotal=1500) over a
    $500 limit. The warn macro isn't wired into the template yet (see module
    docstring), so we can only assert the seam executes cleanly — a 200, not a
    500 from the credit path."""
    cust = _customer(db, credit_limit=500.0)
    so = SalesOrder(so_number=f"SO-SEAM-{next(_seq):05d}", customer_id=cust.id,
                    status=SOStatus.OPEN)
    so.lines.append(SOLine(qty_ordered=1, unit_price=1500.0,
                           line_status=SOLineStatus.STOCK))
    db.add(so); db.commit(); db.refresh(so)

    assert client.get(f"/sales-orders/{so.id}").status_code == 200
