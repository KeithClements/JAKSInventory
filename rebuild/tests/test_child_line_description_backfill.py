"""
tests/test_child_line_description_backfill.py
=============================================
Bug 7 — child/optional add-lines must backfill description (and price) from the
product, same as a main add_line. Before the fix, add_upgrade_option /
add_optional_line resolved unit_cost but left description blank when the caller
POSTed only product_id, so an immediate-add upgrade showed an empty line.

Parity target: QuoteService.add_line already calls apply_product_line_defaults;
these two child paths now do too. (SO/Invoice have no separate child methods —
their single add_line already backfills — so this gap was quote-only.)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.constants import LineRole, QuoteStatus
from app.models.product import Product
from app.services.quote_service import QuoteService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _setup(db):
    from app.models.customer import Customer
    from app.models.quote import Quote
    cust = Customer(company_name="Child Co", contact_name="QA", email="c@t.local")
    db.add(cust); db.commit(); db.refresh(cust)
    # Product with a title + sensible cost/markup so backfill has something to copy.
    prod = Product(sku="UPG-1", title="Turbo Upgrade Kit", description="desc",
                   cost=100.0, markup_pct=50.0, qty_on_hand=5, is_active=True)
    db.add(prod); db.commit(); db.refresh(prod)
    q = Quote(quote_number="Q-CHILD-1", customer_id=cust.id, status=QuoteStatus.DRAFT)
    db.add(q); db.commit(); db.refresh(q)
    svc = QuoteService(db, None)
    # A parent product line to hang children off of.
    parent = svc.add_line(q.id, prod.id, {"qty": 1})[0]
    return svc, q, prod, parent


def test_upgrade_option_backfills_description_from_product(db):
    svc, q, prod, parent = _setup(db)
    # Immediate-add: only product_id, no description/price supplied.
    line = svc.add_upgrade_option(parent.id, prod.id, {"qty": 1})
    assert line.description == "Turbo Upgrade Kit"      # backfilled from product.title
    assert line.unit_cost == 100.0                       # preferred/own cost
    assert line.unit_price == prod.selling_price         # 100 * 1.50 = 150.0
    assert line.line_role == LineRole.UPGRADE_OPTION
    assert line.is_included is False                     # children excluded by default


def test_optional_line_backfills_description_from_product(db):
    svc, q, prod, parent = _setup(db)
    line = svc.add_optional_line(parent.id, prod.id, {"qty": 2})
    assert line.description == "Turbo Upgrade Kit"
    assert line.unit_cost == 100.0
    assert line.unit_price == prod.selling_price
    assert line.line_role == LineRole.OPTIONAL
    assert line.is_included is False


def test_explicit_description_still_wins(db):
    # Backfill only fills blanks — a caller-supplied description must NOT be overwritten.
    svc, q, prod, parent = _setup(db)
    line = svc.add_upgrade_option(parent.id, prod.id,
                                  {"qty": 1, "description": "Custom label"})
    assert line.description == "Custom label"


def test_explicit_price_still_wins(db):
    svc, q, prod, parent = _setup(db)
    line = svc.add_optional_line(parent.id, prod.id,
                                 {"qty": 1, "unit_price": 999.0})
    assert line.unit_price == 999.0
