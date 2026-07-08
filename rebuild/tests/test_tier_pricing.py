"""
tests/test_tier_pricing.py
==========================
The regression that would have caught the go-live "FAKE TIER-PRICING" blocker:
a tiered customer's negotiated rate must ACTUALLY reach their quote/invoice line
price — not just show a tier badge while the line stays at full retail.

How per-customer pricing actually works (verified against the services):
  • the price-carrying field is `customer.discount_pct` (the tier's negotiated
    rate); `pricing_tier` (wholesale/fleet/dealer) is a SEGMENTATION LABEL with
    no price effect on its own.
  • QUOTE: QuoteService.add_line auto-applies customer.discount_pct to each
    discountable line (per-line discount_pct).
  • INVOICE: InvoiceService.create_draft sets invoice.discount_pct =
    customer.discount_pct (header-level); the totals engine discounts the parts.

The sentinel: for the SAME product, the only difference is the customer's rate —
so a tiered customer MUST pay less than a standard one. If add_line / create_draft
ever stops applying the rate (the "fake tier-pricing" regression), tiered ==
standard and these fail.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_tier_pricing.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, LineType, PricingTier
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.quote import Quote
from app.services.invoice_service import InvoiceService
from app.services.quote_service import QuoteService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c   # startup seeds settings + invoice-number counter + admin user


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db, *, discount_pct, pricing_tier) -> Customer:
    c = Customer(company_name=f"Tier Co {next(_seq)}", contact_name="QA",
                 pricing_tier=pricing_tier, discount_pct=discount_pct)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db) -> Product:
    p = Product(sku=f"TIER-{next(_seq)}", title="Reman injector", cost=60.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _quote(db, customer_id) -> Quote:
    q = Quote(quote_number=f"Q-TIER-{next(_seq):05d}", customer_id=customer_id)
    db.add(q); db.commit(); db.refresh(q)
    return q


_LINE = {"qty": 1, "unit_price": 100.0, "unit_cost": 60.0}   # explicit → deterministic


# ── QUOTE: customer rate auto-applies per line ───────────────────────────────

def test_tiered_customer_quote_line_reflects_the_tier_rate(db):
    prod = _product(db)
    std = _customer(db, discount_pct=0.0, pricing_tier=PricingTier.STANDARD)
    whl = _customer(db, discount_pct=15.0, pricing_tier=PricingTier.WHOLESALE)
    qsvc = QuoteService(db, _UID)

    std_line = qsvc.add_line(_quote(db, std.id).id, prod.id, dict(_LINE))[0]
    whl_line = qsvc.add_line(_quote(db, whl.id).id, prod.id, dict(_LINE))[0]

    # standard customer pays full retail; tiered customer's rate auto-applies
    assert std_line.discount_pct == 0.0 and std_line.line_total == 100.0
    assert whl_line.discount_pct == 15.0          # the tier rate landed on the line
    assert whl_line.line_total == 85.0            # 100 × (1 − 0.15)
    # the sentinel: same product, tier customer pays LESS — not "fake"
    assert whl_line.line_total < std_line.line_total


# ── INVOICE: customer rate flows to the invoice + discounts the total ────────

def test_tiered_customer_invoice_total_reflects_the_tier_rate(db):
    prod = _product(db)
    std = _customer(db, discount_pct=0.0, pricing_tier=PricingTier.STANDARD)
    flt = _customer(db, discount_pct=20.0, pricing_tier=PricingTier.FLEET)
    isvc = InvoiceService(db, _UID)

    inv_std = isvc.create_draft(std.id)
    inv_flt = isvc.create_draft(flt.id)
    # the negotiated rate is stamped onto the invoice header at creation
    assert inv_std.discount_pct == 0.0
    assert inv_flt.discount_pct == 20.0

    isvc.add_line(inv_std.id, prod.id, dict(_LINE))
    isvc.add_line(inv_flt.id, prod.id, dict(_LINE))
    db.expire_all()
    inv_std = db.query(Invoice).filter(Invoice.id == inv_std.id).first()
    inv_flt = db.query(Invoice).filter(Invoice.id == inv_flt.id).first()

    assert inv_std.total == 100.0
    assert inv_flt.total == 80.0                  # 100 − 20% on parts
    assert inv_flt.total < inv_std.total          # the sentinel


# ── A standard customer is genuinely undiscounted (not "everyone gets a deal") ─

def test_standard_customer_gets_no_phantom_discount(db):
    prod = _product(db)
    std = _customer(db, discount_pct=0.0, pricing_tier=PricingTier.STANDARD)
    line = QuoteService(db, _UID).add_line(_quote(db, std.id).id, prod.id, dict(_LINE))[0]
    assert line.discount_pct == 0.0 and line.line_total == 100.0
    inv = InvoiceService(db, _UID).create_draft(std.id)
    assert inv.discount_pct == 0.0
