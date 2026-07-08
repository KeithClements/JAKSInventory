"""
tests/test_quote_optional_lines.py
====================================
Regression gate for dfbf4ca: adding an OPTIONAL (or UPGRADE_OPTION) line to a
quote must NOT change Quote.subtotal (Bug 6 — options are quoted separately;
the customer opts in).

Owner decision 2026-05-31 "A": optional add-ons are excluded from the base
quoted total by default.  Quote.subtotal sums only is_included=True lines.

Before dfbf4ca both OPTIONAL and UPGRADE_OPTION lines defaulted is_included=True
so they baked into the total — the customer had to actively remove them, which
read as "the price includes things you didn't ask for." Now they default to False
(excluded) and the customer opts in.

This file locks three invariants:
  1. Adding an OPTIONAL line leaves subtotal unchanged.
  2. Adding an UPGRADE_OPTION line leaves subtotal unchanged.
  3. Adding a PRIMARY line DOES raise the subtotal (counter-case).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_quote_optional_lines.py -v
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import LineRole, QuoteStatus
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1


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

_counter = __import__("itertools").count(1)


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"Optional Co {next(_counter)}",
                 contact_name="QA", email=f"opt{next(_counter)}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, cost=10.0, price=20.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    n = next(_counter)
    p = Product(sku=f"OPT-{n:04d}", title=f"Opt Part {n}",
                description="optional test part",
                cost=cost, markup_pct=100.0, qty_on_hand=5,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _quote_with_primary_line(db, customer, product, price=100.0):
    """Return (quote, primary_line) with subtotal == price."""
    from app.models.quote import Quote
    from app.services.quote_service import QuoteService

    quote = Quote(quote_number=f"Q-OPT-{next(_counter)}",
                  customer_id=customer.id, status=QuoteStatus.DRAFT)
    db.add(quote); db.commit(); db.refresh(quote)

    svc = QuoteService(db, _UID)
    lines = svc.add_line(quote.id, product.id, {
        "line_role": LineRole.PRIMARY,
        "unit_price": price,
        "qty": 1,
        "description": "Primary line",
    })
    db.expire_all()
    return db.query(Quote).filter(Quote.id == quote.id).first()


# ---------------------------------------------------------------------------
# 1. OPTIONAL line — subtotal unchanged, is_included == False
# ---------------------------------------------------------------------------

def test_adding_optional_line_does_not_change_subtotal(db):
    """dfbf4ca: OPTIONAL line defaults is_included=False → excluded from subtotal."""
    from app.models.quote import Quote, QuoteLine
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    primary_prod = _product(db)
    opt_prod = _product(db)

    quote = _quote_with_primary_line(db, cust, primary_prod, price=100.0)
    subtotal_before = quote.subtotal
    assert subtotal_before == 100.0, f"Expected primary subtotal 100.0; got {subtotal_before}"

    svc = QuoteService(db, _UID)
    svc.add_line(quote.id, opt_prod.id, {
        "line_role": LineRole.OPTIONAL,
        "unit_price": 50.0,
        "qty": 1,
        "description": "Optional add-on",
    })
    db.expire_all()

    quote_r = db.query(Quote).filter(Quote.id == quote.id).first()
    assert quote_r.subtotal == subtotal_before, (
        f"Adding an OPTIONAL line must NOT change Quote.subtotal "
        f"(expected {subtotal_before}, got {quote_r.subtotal}). "
        "Check dfbf4ca — optional lines must default is_included=False."
    )

    # The OPTIONAL line itself must be marked excluded
    opt_line = (
        db.query(QuoteLine)
        .filter(QuoteLine.quote_id == quote.id,
                QuoteLine.line_role == LineRole.OPTIONAL)
        .first()
    )
    assert opt_line is not None
    assert opt_line.is_included is False, (
        f"OPTIONAL line.is_included should be False; got {opt_line.is_included!r}"
    )


# ---------------------------------------------------------------------------
# 2. UPGRADE_OPTION line — subtotal unchanged, is_included == False
# ---------------------------------------------------------------------------

def test_adding_upgrade_option_line_does_not_change_subtotal(db):
    """UPGRADE_OPTION is also excluded by default (same dfbf4ca ruling)."""
    from app.models.quote import Quote, QuoteLine
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    primary_prod = _product(db)
    upgrade_prod = _product(db)

    quote = _quote_with_primary_line(db, cust, primary_prod, price=200.0)
    subtotal_before = quote.subtotal

    svc = QuoteService(db, _UID)
    svc.add_line(quote.id, upgrade_prod.id, {
        "line_role": LineRole.UPGRADE_OPTION,
        "unit_price": 75.0,
        "qty": 1,
        "description": "Upgrade option",
    })
    db.expire_all()

    quote_r = db.query(Quote).filter(Quote.id == quote.id).first()
    assert quote_r.subtotal == subtotal_before, (
        f"Adding an UPGRADE_OPTION must not change subtotal "
        f"(expected {subtotal_before}, got {quote_r.subtotal})"
    )

    upgrade_line = (
        db.query(QuoteLine)
        .filter(QuoteLine.quote_id == quote.id,
                QuoteLine.line_role == LineRole.UPGRADE_OPTION)
        .first()
    )
    assert upgrade_line is not None
    assert upgrade_line.is_included is False


# ---------------------------------------------------------------------------
# 3. Counter-case: PRIMARY line DOES raise subtotal
# ---------------------------------------------------------------------------

def test_adding_primary_line_raises_subtotal(db):
    """Sanity: a second PRIMARY line IS included — subtotal grows."""
    from app.models.quote import Quote
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    p1 = _product(db)
    p2 = _product(db)

    quote = _quote_with_primary_line(db, cust, p1, price=100.0)
    subtotal_before = quote.subtotal  # 100.0

    svc = QuoteService(db, _UID)
    svc.add_line(quote.id, p2.id, {
        "line_role": LineRole.PRIMARY,
        "unit_price": 40.0,
        "qty": 1,
        "description": "Second primary line",
    })
    db.expire_all()

    quote_r = db.query(Quote).filter(Quote.id == quote.id).first()
    assert quote_r.subtotal == subtotal_before + 40.0, (
        f"Adding a PRIMARY line must increase subtotal by its value "
        f"(expected {subtotal_before + 40.0}, got {quote_r.subtotal})"
    )
