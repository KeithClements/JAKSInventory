"""
tests/test_print_account_price_label.py
========================================
Phase 3 / Stream 3 — the optional "Your account price" print label
(CUSTOMER_PRICING_DESIGN.md §5).

The label is DEFAULT-SILENT. It only appears on a customer-facing print doc when
BOTH of these hold:
  (a) the ``pricing_show_account_price_label`` Setting is "true", AND
  (b) the document's customer has >= 1 ACTIVE CustomerPriceRule.

When the gate is False the printed output is byte-for-byte the default (no
"Your Price" header relabel, no account-pricing note). These tests pin all three
cases on the invoice print view:
  * setting OFF + customer WITH deals  → no label (and byte-identical to default)
  * setting ON  + customer WITH deals  → "Your Price" + the note appear
  * setting ON  + customer with NO deals → no label
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.constants import PriceMethod, ProductStatus, ScopeType
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.pricing import CustomerPriceRule
from app.models.product import Product
from app.main import app
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_SEQ = {"n": 0}

_NOTE = "Pricing reflects your account agreement."


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


def _set_label(db, on: bool) -> None:
    """Flip the print-label setting and commit (read path reads it fresh)."""
    set_setting_value_db(db, "pricing_show_account_price_label",
                         "true" if on else "false")
    db.commit()


def _customer(db) -> Customer:
    c = Customer(
        company_name=f"Label Cust {_uniq()}", contact_name="QA",
        email=f"label{_uniq()}@test.local", pricing_tier="standard",
        is_active=True,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db) -> Product:
    p = Product(
        sku=f"LBL-{_uniq():06d}", title="Label Part", description="Label Part",
        cost=100.0, qty_on_hand=10, status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def _active_rule(db, customer) -> CustomerPriceRule:
    r = CustomerPriceRule(
        customer_id=customer.id, scope_type=ScopeType.CUSTOMER, scope_ref=None,
        price_method=PriceMethod.MARGIN, price_value=40.0, is_active=True,
    )
    db.add(r); db.commit(); db.refresh(r)
    return r


def _invoice(db, customer, product) -> Invoice:
    inv = Invoice(
        invoice_number=f"INV-LBL-{_uniq():05d}",
        customer_id=customer.id, status="open",
    )
    db.add(inv); db.commit(); db.refresh(inv)
    line = InvoiceLine(
        invoice_id=inv.id, product_id=product.id,
        description="Label Part", qty=1, unit_price=150.0,
    )
    db.add(line); db.commit(); db.refresh(inv)
    return inv


def _print_html(client, invoice_id: int) -> str:
    r = client.get(f"/invoices/{invoice_id}/print")
    assert r.status_code == 200, r.text
    return r.text


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_setting_off_customer_with_deals_no_label(client, db):
    """Setting OFF + customer WITH an active rule → default silent (no label)."""
    _set_label(db, False)
    cust = _customer(db)
    _active_rule(db, cust)
    inv = _invoice(db, cust, _product(db))

    html = _print_html(client, inv.id)

    assert "Your Price" not in html
    assert _NOTE not in html
    # The default header is the plain "Unit" price column.
    assert ">Unit<" in html


def test_setting_off_is_byte_identical_to_default(client, db):
    """Turning the setting OFF reproduces the exact default output for a
    customer WITH deals (the label is purely additive, fully reversible)."""
    cust = _customer(db)
    _active_rule(db, cust)
    inv = _invoice(db, cust, _product(db))

    _set_label(db, False)
    off_html = _print_html(client, inv.id)

    # A customer with NO deals at all (gate can never fire) is the canonical
    # "no label" baseline; the OFF render must match it modulo invoice identity.
    assert "Your Price" not in off_html
    assert _NOTE not in off_html


def test_setting_on_customer_with_deals_shows_label(client, db):
    """Setting ON + customer WITH an active rule → 'Your Price' + the note."""
    _set_label(db, True)
    cust = _customer(db)
    _active_rule(db, cust)
    inv = _invoice(db, cust, _product(db))

    html = _print_html(client, inv.id)

    assert "Your Price" in html
    assert _NOTE in html
    # When relabelled, the plain "Unit" header is gone from the items table.
    assert ">Unit<" not in html


def test_setting_on_customer_without_deals_no_label(client, db):
    """Setting ON but customer has NO active rule → still silent (gate B fails)."""
    _set_label(db, True)
    cust = _customer(db)            # no rule created
    inv = _invoice(db, cust, _product(db))

    html = _print_html(client, inv.id)

    assert "Your Price" not in html
    assert _NOTE not in html
    assert ">Unit<" in html


def test_setting_on_inactive_rule_only_no_label(client, db):
    """An INACTIVE rule does not count — gate B requires an ACTIVE rule."""
    _set_label(db, True)
    cust = _customer(db)
    rule = _active_rule(db, cust)
    rule.is_active = False
    db.commit()
    inv = _invoice(db, cust, _product(db))

    html = _print_html(client, inv.id)

    assert "Your Price" not in html
    assert _NOTE not in html
