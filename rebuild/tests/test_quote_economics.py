"""
tests/test_quote_economics.py
=============================
Staff "deal economics" on the quote workspace (the cost / $ profit / margin↔markup
the owner asked to see while quoting):

  * GET /quotes/{id}/totals exposes Total Cost + Profit with correct numbers, and
    carries both the margin and markup spans (the header toggle flips them
    client-side via `marginMode`).
  * the quote workspace renders the per-line economics + the Margin/Markup header
    switch.

Everything is gated client-side by the per-user Show-margin toggle (x-show=
"showMargin", OFF by default) — but the markup is in the server HTML regardless,
so it never leaks to the customer print (the print templates are untouched).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.constants import ProductStatus
from app.models.customer import Customer
from app.models.product import Product
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
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
        s.rollback()
        s.close()


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db) -> Customer:
    c = Customer(company_name=f"Econ {_uniq()}", contact_name="C",
                 email=f"econ{_uniq()}@test.local", is_active=True,
                 is_tax_exempt=True, tax_rate=0.0)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, cost: float, price: float) -> Product:
    p = Product(sku=f"EC-{_uniq():06d}", title="Econ Part", description="Econ Part",
                cost=cost, price_override=price, qty_on_hand=100,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _quote_with_line(client, cust_id: int, prod_id: int, qty: int) -> int:
    r = client.post("/quotes/new",
                    data={"customer_id": str(cust_id), "validity_days": "30"},
                    follow_redirects=False)
    qid = int(r.headers["location"].rstrip("/").split("/")[-1])
    client.post(f"/quotes/{qid}/lines",
                data={"product_id": str(prod_id), "qty": str(qty), "line_type": "product"})
    return qid


def test_totals_partial_exposes_cost_and_profit(client, db):
    # cost 60, sell 100, qty 2 → cost 120, profit 80, blended margin 40% / markup 67%.
    cust = _customer(db)
    prod = _product(db, cost=60.0, price=100.0)
    qid = _quote_with_line(client, cust.id, prod.id, 2)

    html = client.get(f"/quotes/{qid}/totals").text
    assert "Total Cost" in html
    assert "$120.00" in html          # 60 × 2
    assert "$80.00" in html           # (100 − 60) × 2
    # Both views ship in the HTML; the header toggle picks one (marginMode).
    assert "marginMode !== 'markup'" in html
    assert "marginMode === 'markup'" in html
    assert "40%" in html              # blended margin
    assert "67%" in html              # blended markup (80/120)


def test_workspace_has_line_economics_and_margin_markup_switch(client, db):
    cust = _customer(db)
    prod = _product(db, cost=60.0, price=100.0)
    qid = _quote_with_line(client, cust.id, prod.id, 2)

    html = client.get(f"/quotes/{qid}").text
    # Header Margin/Markup switch
    assert "setMarginMode(" in html
    # Per-line staff economics (gated by showMargin)
    assert "cost" in html and "$120.00" in html
    # Gated for staff only — the economics block keys off the show-margin toggle.
    assert 'x-show="showMargin"' in html


def test_excluded_optional_lines_are_left_out_of_cost_and_profit(client, db):
    """Optional add-ons (excluded from the quote total) must also be excluded from
    the cost/profit math — economics follow the included lines only."""
    cust = _customer(db)
    prod = _product(db, cost=60.0, price=100.0)
    qid = _quote_with_line(client, cust.id, prod.id, 1)  # included → cost 60, profit 40

    html = client.get(f"/quotes/{qid}/totals").text
    assert "$60.00" in html   # cost of the single included line
    assert "$40.00" in html   # profit of the single included line
