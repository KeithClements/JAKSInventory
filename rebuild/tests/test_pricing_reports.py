"""
tests/test_pricing_reports.py
=============================
Customer-Specific Product Pricing — Phase 3 reports
(CUSTOMER_PRICING_DESIGN.md §7), service + route coverage.

  * Customer Deals  — lists ACTIVE rules; flags expiring-soon against a FIXED
                      `today` (no wall-clock dependence).
  * Margin Leakage  — flags a line sold below its cost-bracket target; EXCLUDES
                      a line at/above target.
  * Both routes return 200 and render WITHOUT the swallowed-error banner.

Each test uses a FRESH isolated in-memory engine (reports read across the whole
DB; isolation keeps the asserted numbers exact). Invoice "total" is a computed
@property, so we seed InvoiceLine rows directly, never a total column.
"""
from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine
from app.services import pricing_reports_service


FIXED_TODAY = datetime.date(2026, 6, 18)


@pytest.fixture()
def db():
    Session = activate(fresh_engine())
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ── Seed helpers ────────────────────────────────────────────────────────────────

def _customer(db, *, name="Deal Cust", email="deal@test.local"):
    from app.models.customer import Customer
    c = Customer(company_name=name, contact_name="QA", email=email)
    db.add(c); db.flush()
    return c


def _product(db, *, sku="DEAL-1", cost=100.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title="Deal Part", description="Deal Part", cost=cost,
                markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.flush()
    return p


def _rule(db, customer, **kw):
    """Create a rule via the service (so validation/normalization matches prod)."""
    from app.services.pricing_service import CustomerPriceRuleService
    data = {
        "scope_type": kw.pop("scope_type", "PRODUCT"),
        "scope_ref": kw.pop("scope_ref", None),
        "price_method": kw.pop("price_method", "margin"),
        "price_value": kw.pop("price_value", 40.0),
    }
    data.update(kw)
    return CustomerPriceRuleService(db).create_rule(customer.id, data)


def _invoice(db, customer, *, status, number="INV-L1", created_at=None):
    from app.models.invoice import Invoice
    inv = Invoice(invoice_number=number, customer_id=customer.id, status=status,
                  created_at=created_at)
    db.add(inv); db.flush()
    return inv


def _line(db, invoice, product=None, *, qty=1, unit_price=0.0, unit_cost=0.0,
          line_type="product"):
    from app.models.invoice import InvoiceLine
    ln = InvoiceLine(invoice_id=invoice.id,
                     product_id=(product.id if product else None),
                     line_type=line_type, qty=qty,
                     unit_price=unit_price, unit_cost=unit_cost)
    db.add(ln); db.flush()
    return ln


# ===========================================================================
# Customer Deals
# ===========================================================================

def test_customer_deals_lists_active_rules_only(db):
    cust = _customer(db)
    prod = _product(db, sku="DEAL-ACTIVE")
    active = _rule(db, cust, scope_type="PRODUCT", scope_ref=str(prod.id),
                   price_method="margin", price_value=40.0)
    inactive = _rule(db, cust, scope_type="CUSTOMER", price_value=10.0)
    # soft-deactivate the second rule
    from app.services.pricing_service import CustomerPriceRuleService
    CustomerPriceRuleService(db).deactivate_rule(inactive.id)
    db.commit()

    rows = pricing_reports_service.customer_deals(db, today=FIXED_TODAY)
    rule_ids = {r["rule_id"] for r in rows}
    assert active.id in rule_ids
    assert inactive.id not in rule_ids, "deactivated rule must not appear"


def test_customer_deals_flags_expiring_soon_with_fixed_today(db):
    cust = _customer(db)
    # Expires 10 days out → within the default 30-day window → flagged.
    soon = _rule(db, cust, scope_type="CUSTOMER", price_value=12.0,
                 effective_to=(FIXED_TODAY + datetime.timedelta(days=10)).isoformat())
    # Expires 90 days out → outside the window → NOT flagged.
    far = _rule(db, cust, scope_type="CUSTOMER", price_value=14.0,
                effective_to=(FIXED_TODAY + datetime.timedelta(days=90)).isoformat())
    # Open-ended → never expiring.
    openrule = _rule(db, cust, scope_type="CUSTOMER", price_value=16.0)
    db.commit()

    rows = pricing_reports_service.customer_deals(
        db, expiring_within_days=30, today=FIXED_TODAY
    )
    by_id = {r["rule_id"]: r for r in rows}
    assert by_id[soon.id]["expiring_soon"] is True
    assert by_id[far.id]["expiring_soon"] is False
    assert by_id[openrule.id]["expiring_soon"] is False
    assert by_id[soon.id]["days_until_expiry"] == 10


def test_customer_deals_product_scope_has_margin_at_cost(db):
    cust = _customer(db)
    prod = _product(db, sku="DEAL-MARGIN", cost=100.0)
    # 40% margin rule on a $100-cost product → margin_at_current_cost ≈ 40%.
    _rule(db, cust, scope_type="PRODUCT", scope_ref=str(prod.id),
          price_method="margin", price_value=40.0)
    db.commit()

    rows = pricing_reports_service.customer_deals(db, today=FIXED_TODAY)
    row = rows[0]
    assert row["margin_at_current_cost"] is not None
    assert abs(row["margin_at_current_cost"] - 40.0) < 0.5
    assert row["cost_relative"] is False


def test_customer_deals_customer_id_filter(db):
    a = _customer(db, name="Cust A", email="a@test.local")
    b = _customer(db, name="Cust B", email="b@test.local")
    _rule(db, a, scope_type="CUSTOMER", price_value=10.0)
    _rule(db, b, scope_type="CUSTOMER", price_value=20.0)
    db.commit()

    rows = pricing_reports_service.customer_deals(db, customer_id=a.id, today=FIXED_TODAY)
    assert rows
    assert all(r["customer_id"] == a.id for r in rows)


# ===========================================================================
# Margin Leakage
# ===========================================================================

def test_margin_leakage_flags_below_and_excludes_at_or_above_target(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    # cost=100 → target bracket is 40% (100..500).
    leak_prod = _product(db, sku="LEAK", cost=100.0)
    ok_prod = _product(db, sku="OK", cost=100.0)

    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, number="INV-LEAK")
    # Below target: price 120 → margin (120-100)/120 = 16.7% < 40% → flagged.
    _line(db, inv, leak_prod, qty=1, unit_price=120.0, unit_cost=100.0)
    # At/above target: price 200 → margin (200-100)/200 = 50% >= 40% → excluded.
    _line(db, inv, ok_prod, qty=1, unit_price=200.0, unit_cost=100.0)
    db.commit()

    rows = pricing_reports_service.margin_leakage(db)
    skus = {r["sku"] for r in rows}
    assert "LEAK" in skus, "below-target line must be flagged"
    assert "OK" not in skus, "at/above-target line must be excluded"

    leak = next(r for r in rows if r["sku"] == "LEAK")
    assert leak["target_pct"] == 40.0
    assert abs(leak["margin_pct"] - 16.7) < 0.2
    assert abs(leak["gap"] - 23.3) < 0.2


def test_margin_leakage_excludes_drafts_voids_zero_cost_and_nonproduct(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="EDGE", cost=100.0)

    draft = _invoice(db, cust, status=InvoiceStatus.DRAFT, number="INV-DRAFT")
    _line(db, draft, prod, qty=1, unit_price=110.0, unit_cost=100.0)
    void = _invoice(db, cust, status=InvoiceStatus.VOID, number="INV-VOID")
    _line(db, void, prod, qty=1, unit_price=110.0, unit_cost=100.0)
    # zero-cost line on a real invoice — no cost → cannot compute leakage.
    zero = _invoice(db, cust, status=InvoiceStatus.OPEN, number="INV-ZERO")
    _line(db, zero, prod, qty=1, unit_price=110.0, unit_cost=0.0)
    # non-product (freight) line below "target" — excluded by line_type.
    freight = _invoice(db, cust, status=InvoiceStatus.OPEN, number="INV-FRT")
    _line(db, freight, None, qty=1, unit_price=5.0, unit_cost=100.0, line_type="freight")
    db.commit()

    rows = pricing_reports_service.margin_leakage(db)
    assert rows == [], "draft/void/zero-cost/non-product lines must all be excluded"


def test_margin_leakage_rule_applies_current_state(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="LEAK-RULE", cost=100.0)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, number="INV-RULE")
    _line(db, inv, prod, qty=1, unit_price=120.0, unit_cost=100.0)
    # A CURRENT product rule for this customer+product → rule_applies True.
    _rule(db, cust, scope_type="PRODUCT", scope_ref=str(prod.id),
          price_method="margin", price_value=20.0)
    db.commit()

    rows = pricing_reports_service.margin_leakage(db)
    row = next(r for r in rows if r["sku"] == "LEAK-RULE")
    assert row["rule_applies"] is True


def test_margin_leakage_sorted_worst_gap_first(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    p1 = _product(db, sku="GAP-SMALL", cost=100.0)
    p2 = _product(db, sku="GAP-BIG", cost=100.0)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, number="INV-GAP")
    # margin 33.3% → gap 6.7
    _line(db, inv, p1, qty=1, unit_price=150.0, unit_cost=100.0)
    # margin 9.1% → gap 30.9
    _line(db, inv, p2, qty=1, unit_price=110.0, unit_cost=100.0)
    db.commit()

    rows = pricing_reports_service.margin_leakage(db)
    assert [r["sku"] for r in rows[:2]] == ["GAP-BIG", "GAP-SMALL"]


# ===========================================================================
# Routes
# ===========================================================================

_PRICING_REPORT_ROUTES = ["/reports/customer-deals", "/reports/margin-leakage"]


def test_pricing_report_routes_render_without_error_banner():
    """Both routes return 200 AND do not show the swallowed-error banner the
    handlers fall back to when the service raises."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.constants import InvoiceStatus

    Session = activate(fresh_engine())
    s = Session()
    cust = _customer(s)
    prod = _product(s, sku="ROUTE-LEAK", cost=100.0)
    _rule(s, cust, scope_type="PRODUCT", scope_ref=str(prod.id),
          price_method="margin", price_value=40.0)
    inv = _invoice(s, cust, status=InvoiceStatus.OPEN, number="INV-ROUTE")
    _line(s, inv, prod, qty=1, unit_price=120.0, unit_cost=100.0)
    s.commit()
    s.close()

    with TestClient(app, raise_server_exceptions=False) as c:
        for route in _PRICING_REPORT_ROUTES:
            r = c.get(route)
            assert r.status_code == 200, f"{route} -> {r.status_code}"
            assert "Check server logs for details" not in r.text, (
                f"{route} rendered the swallowed-error banner (service raised):\n"
                f"{r.text[:300]}")


def test_pricing_report_routes_accept_filters():
    """The documented filters are accepted (200) on each route."""
    from fastapi.testclient import TestClient
    from app.main import app

    Session = activate(fresh_engine())
    s = Session()
    cust = _customer(s)
    s.commit()
    cid = cust.id
    s.close()

    with TestClient(app, raise_server_exceptions=False) as c:
        r1 = c.get(f"/reports/customer-deals?customer_id={cid}&expiring_within_days=14")
        assert r1.status_code == 200
        r2 = c.get("/reports/margin-leakage?since=2026-01-01")
        assert r2.status_code == 200
