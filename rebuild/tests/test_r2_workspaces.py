"""
tests/test_r2_workspaces.py
===========================
R2 workspace bundle — route-level coverage for four verified fixes:

  1. Engine make/model cascading picker on the SO + Invoice workspaces
     (macros/engine_picker.html — previously quote-only). Both workspace
     contexts must expose ENGINE_MAKES / ENGINE_MODELS_BY_MAKE and the editable
     header must render the picker (JSON-in-script-tag + hidden inputs with the
     legacy field names) while preserving stored legacy free-text values.

  2. SO-deposit card surcharge — SalesOrderService.collect_deposit now mirrors
     the invoice take-payment gating (R1-3): CREDIT_CARD method + a
     surcharge-flagged customer (customer.card_surcharge_pct > 0 — the same
     customer-level source invoice creation seeds cc_surcharge_pct from)
     records Payment.surcharge_amount; non-card / unflagged stays 0.

  3. Invoice list N+1 — lines / allocations / sales_order are eager-loaded;
     asserted via content correctness (Total + Balance Due columns render the
     totals-engine values, incl. a partial payment).

  4. Invoice list column sort — sortable_th wired for number / customer /
     due_date / total / balance through app.utils.apply_sort (total + balance
     are computed properties ordered in Python, mirroring quotes' margin sort).

Module TestClient + per-module in-memory engine (tests/conftest.py harness).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()
_UID = 1
_SEQ = {"n": 0}


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


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db, *, card_surcharge_pct: float | None = None):
    """Active, on-hold-free customer (no flags → no credit_warn interference)."""
    from app.models.customer import Customer
    c = Customer(
        company_name=f"R2 Cust {_uniq()}",
        contact_name="QA",
        email="r2@test.local",
        is_active=True,
        card_surcharge_pct=card_surcharge_pct,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, price: float = 100.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"R2-{_uniq():06d}", title="R2 Part", description="R2 Part",
                cost=40.0, price_override=price, qty_on_hand=100,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _open_so(db, customer, **so_fields):
    """An OPEN SO with one product line, built directly (workspace render +
    deposit-route target)."""
    from app.constants import LineType, SOPaymentMode, SOStatus
    from app.models.quote import SalesOrder, SOLine
    n = _uniq()
    prod = _product(db)
    so = SalesOrder(so_number=f"SO-R2-{n:04d}", customer_id=customer.id,
                    status=SOStatus.OPEN, payment_mode=SOPaymentMode.DEPOSIT,
                    deposit_amount=0.0, notes="", internal_notes="", **so_fields)
    db.add(so); db.flush()
    db.add(SOLine(so_id=so.id, product_id=prod.id, line_type=LineType.PRODUCT,
                  description="R2 Part", qty_ordered=2, qty_committed=2,
                  qty_fulfilled=0, qty_invoiced=0, unit_price=250.0))
    db.commit(); db.refresh(so)
    return so


def _draft_invoice(db, *, price: float = 100.0, qty: int = 1, customer=None):
    from app.services.invoice_service import InvoiceService
    cust = customer or _customer(db)
    prod = _product(db, price=price)
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": qty})
    db.commit()
    return inv


def _so_payments(db, so_id):
    from app.models.invoice import Payment
    return (db.query(Payment)
            .filter(Payment.sales_order_id == so_id)
            .order_by(Payment.id)
            .all())


# ── 1. Engine picker on both workspaces ───────────────────────────────────────

def test_so_workspace_renders_engine_picker(client, db):
    from app.constants import ENGINE_MAKES
    so = _open_so(db, _customer(db))

    page = client.get(f"/sales-orders/{so.id}")
    assert page.status_code == 200
    # Macro skeleton: JSON-in-script-tag injection + hidden inputs that keep the
    # legacy field names so the existing header-save route binds unchanged.
    assert "data-engine-json" in page.text
    assert 'name="engine_manufacturer"' in page.text
    assert 'name="engine_model"' in page.text
    # Context exposes constants.ENGINE_MAKES — every preset make is in the JSON.
    for make in ENGINE_MAKES:
        assert make in page.text, f"make {make!r} missing from SO workspace picker JSON"


def test_so_workspace_picker_preserves_legacy_value(client, db):
    """A stored free-text value that matches no preset must be carried into the
    picker (seeded as an 'Other' custom value), never silently dropped."""
    so = _open_so(db, _customer(db),
                  engine_manufacturer="CUMMINZ legacy", engine_model="IZX-15 legacy")
    page = client.get(f"/sales-orders/{so.id}")
    assert page.status_code == 200
    assert 'data-make="CUMMINZ legacy"' in page.text
    assert 'data-model="IZX-15 legacy"' in page.text


def test_invoice_workspace_renders_engine_picker(client, db):
    from app.constants import ENGINE_MAKES
    inv = _draft_invoice(db)

    page = client.get(f"/invoices/{inv.id}")
    assert page.status_code == 200
    assert "data-engine-json" in page.text
    assert 'name="engine_manufacturer"' in page.text
    assert 'name="engine_model"' in page.text
    for make in ENGINE_MAKES:
        assert make in page.text, f"make {make!r} missing from invoice workspace picker JSON"


# ── 2. SO-deposit card surcharge ──────────────────────────────────────────────

def test_so_card_deposit_flagged_customer_records_surcharge(client, db):
    cust = _customer(db, card_surcharge_pct=3.0)   # surcharge-flagged
    so = _open_so(db, cust)

    resp = client.post(
        f"/sales-orders/{so.id}/collect-deposit",
        data={"amount": "200.00", "payment_method": "credit_card"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "error" not in resp.headers["location"]

    db.expire_all()
    pays = _so_payments(db, so.id)
    assert len(pays) == 1
    # 3% of the $200 principal — principal itself is unchanged (R1 semantics).
    assert pays[0].surcharge_amount == pytest.approx(6.0)
    assert pays[0].surcharge_amount > 0
    assert pays[0].amount_received == pytest.approx(200.0)
    from app.models.quote import SalesOrder
    assert db.get(SalesOrder, so.id).deposit_amount == pytest.approx(200.0)


def test_so_noncard_deposit_records_no_surcharge(client, db):
    cust = _customer(db, card_surcharge_pct=3.0)   # flagged, but paying by check
    so = _open_so(db, cust)

    resp = client.post(
        f"/sales-orders/{so.id}/collect-deposit",
        data={"amount": "150.00", "payment_method": "check"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "error" not in resp.headers["location"]

    db.expire_all()
    pays = _so_payments(db, so.id)
    assert len(pays) == 1
    assert pays[0].surcharge_amount == 0.0
    assert pays[0].amount_received == pytest.approx(150.0)


def test_so_card_deposit_unflagged_customer_records_no_surcharge(client, db):
    """customer.card_surcharge_pct NULL = not flagged — mirrors invoices, where
    apply_cc_surcharge also defaults False. No silent new fee."""
    cust = _customer(db, card_surcharge_pct=None)
    so = _open_so(db, cust)

    resp = client.post(
        f"/sales-orders/{so.id}/collect-deposit",
        data={"amount": "100.00", "payment_method": "credit_card"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "error" not in resp.headers["location"]

    db.expire_all()
    pays = _so_payments(db, so.id)
    assert len(pays) == 1
    assert pays[0].surcharge_amount == 0.0


# ── 3+4. Invoice list — sort + preloaded money columns ───────────────────────

def _positions(html: str, needles: list[str]) -> list[int]:
    for n in needles:
        assert n in html, f"{n} missing from the list page"
    return [html.index(n) for n in needles]


def test_invoice_list_sorts_by_due_date(client, db):
    cust = _customer(db)
    inv_mar = _draft_invoice(db, customer=cust)
    inv_jan = _draft_invoice(db, customer=cust)
    inv_feb = _draft_invoice(db, customer=cust)
    inv_mar.due_date = datetime(2031, 3, 15)
    inv_jan.due_date = datetime(2031, 1, 15)
    inv_feb.due_date = datetime(2031, 2, 15)
    db.commit()
    by_due_asc = [inv_jan.invoice_number, inv_feb.invoice_number, inv_mar.invoice_number]

    asc = client.get("/invoices/?sort=due_date&direction=asc")
    assert asc.status_code == 200
    pos = _positions(asc.text, by_due_asc)
    assert pos == sorted(pos), "asc due-date order not honored"

    desc = client.get("/invoices/?sort=due_date&direction=desc")
    assert desc.status_code == 200
    pos = _positions(desc.text, list(reversed(by_due_asc)))
    assert pos == sorted(pos), "desc due-date order not honored"


def test_invoice_list_sorts_by_total(client, db):
    cust = _customer(db)
    inv_big = _draft_invoice(db, price=555.55, customer=cust)
    inv_small = _draft_invoice(db, price=111.11, customer=cust)

    asc = client.get("/invoices/?sort=total&direction=asc")
    assert asc.status_code == 200
    pos = _positions(asc.text, [inv_small.invoice_number, inv_big.invoice_number])
    assert pos == sorted(pos), "asc total order not honored"

    desc = client.get("/invoices/?sort=total&direction=desc")
    assert desc.status_code == 200
    pos = _positions(desc.text, [inv_big.invoice_number, inv_small.invoice_number])
    assert pos == sorted(pos), "desc total order not honored"


def test_invoice_list_renders_preloaded_totals_and_balance(client, db):
    """Content correctness for the eager-loaded money columns: Total walks
    inv.lines, Balance Due additionally walks inv.allocations — a partial
    payment must show through on the list page."""
    from app.services.invoice_service import InvoiceService
    from app.services.payment_service import PaymentService

    cust = _customer(db)
    prod = _product(db, price=137.0)
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 1})
    svc.finalise(inv.id)
    PaymentService(db, _UID).record_payment(
        customer_id=cust.id,
        amount_received=41.25,
        payment_method="check",
        data={},
        invoice_ids=[inv.id],
    )
    db.expire_all()

    page = client.get("/invoices/")
    assert page.status_code == 200
    assert inv.invoice_number in page.text
    assert "$137.00" in page.text          # Total (lines preloaded)
    assert "$95.75" in page.text           # Balance Due (allocations preloaded)

    # Balance is also a sortable key (computed Python-side, like quotes margin).
    by_bal = client.get("/invoices/?sort=balance&direction=desc")
    assert by_bal.status_code == 200
    assert inv.invoice_number in by_bal.text


def test_invoice_list_sort_preserves_tab_and_q_in_header_links(client, db):
    """sortable_th must carry ?tab/?q through the sort links (qs param)."""
    cust = _customer(db)
    _draft_invoice(db, customer=cust)

    page = client.get(f"/invoices/?tab=draft&q={cust.company_name}")
    assert page.status_code == 200
    # autoescape renders the qs separators as &amp; — decode before asserting.
    html = page.text.replace("&amp;", "&")
    assert "?sort=due_date&direction=asc&tab=draft&q=" in html
    assert "?sort=total&direction=asc&tab=draft&q=" in html
    assert "?sort=balance&direction=asc&tab=draft&q=" in html
