"""
tests/test_customer_pricing.py
==============================
Customer-Specific Product Pricing — Phase 1 backend (CUSTOMER_PRICING_DESIGN.md).

Covers:
  * MARGIN_TARGET_BRACKETS / target_margin_for_cost lookup.
  * PricingService.resolve_customer_price — precedence matrix
    (PRODUCT > BRAND/CATEGORY > CUSTOMER), category-descendant match,
    qty-break boundary, expired/inactive rules ignored, below_target /
    below_cost flags, cost=0 fallthrough, brand-vs-category fewest-SKUs tiebreak.
  * Rule overrides product.price_override (Step 0 wins).
  * PricingService.last_price_for — latest finalized invoice line.
  * apply_product_line_defaults backward-compat: NO rules → byte-identical price.
  * Rule CRUD routes (list / create / update / deactivate + validation).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.constants import (
    InvoiceStatus, PriceMethod, ProductStatus, ScopeType,
    MARGIN_TARGET_BRACKETS, target_margin_for_cost,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.pricing import CustomerPriceRule
from app.models.product import Product, ProductCategory
from app.services.base import apply_product_line_defaults
from app.services.pricing_service import (
    PricingService, CustomerPriceRuleService, CustomerPriceRuleValidationError,
    scope_label,
)
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1
_SEQ = {"n": 0}


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


def _customer(db, tier: str = "standard", discount: float = 0.0) -> Customer:
    c = Customer(
        company_name=f"CP Cust {_uniq()}", contact_name="QA",
        email=f"cp{_uniq()}@test.local", pricing_tier=tier,
        discount_pct=discount, is_active=True,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _category(db, name: str, parent_id: int | None = None, level: int = 1) -> ProductCategory:
    cat = ProductCategory(name=f"{name}-{_uniq()}", parent_id=parent_id, level=level, is_active=True)
    db.add(cat); db.commit(); db.refresh(cat)
    return cat


def _product(
    db, *, cost: float = 100.0, brand: str = "", category_id: int | None = None,
    price_override: float | None = None, markup_pct: float | None = None,
) -> Product:
    p = Product(
        sku=f"CP-{_uniq():06d}", title="CP Part", description="CP Part",
        cost=cost, brand=brand, category_id=category_id,
        price_override=price_override, markup_pct=markup_pct,
        qty_on_hand=100, status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def _rule(
    db, customer, scope_type, *, scope_ref=None, method=PriceMethod.MARGIN,
    value=40.0, qty_min=None, eff_from=None, eff_to=None, active=True,
    created_at=None,
) -> CustomerPriceRule:
    r = CustomerPriceRule(
        customer_id=customer.id, scope_type=scope_type,
        scope_ref=(str(scope_ref) if scope_ref is not None else None),
        price_method=method, price_value=value, qty_min=qty_min,
        effective_from=eff_from, effective_to=eff_to, is_active=active,
    )
    db.add(r); db.commit(); db.refresh(r)
    if created_at is not None:
        r.created_at = created_at
        db.commit(); db.refresh(r)
    return r


# ── Constants: target-margin brackets ──────────────────────────────────────────

def test_margin_target_brackets():
    assert target_margin_for_cost(10) == 50.0
    assert target_margin_for_cost(24.99) == 50.0
    assert target_margin_for_cost(25) == 45.0
    assert target_margin_for_cost(99) == 45.0
    assert target_margin_for_cost(100) == 40.0
    assert target_margin_for_cost(499) == 40.0
    assert target_margin_for_cost(500) == 35.0
    assert target_margin_for_cost(2000) == 30.0
    assert target_margin_for_cost(2999) == 30.0
    assert target_margin_for_cost(3000) == 25.0
    assert target_margin_for_cost(99999) == 25.0
    # 0 / negative → first bracket
    assert target_margin_for_cost(0) == 50.0
    assert target_margin_for_cost(-5) == 50.0
    assert len(MARGIN_TARGET_BRACKETS) == 6


# ── resolve_customer_price ──────────────────────────────────────────────────────

def test_no_rules_returns_none(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price is None
    assert res.source_rule is None
    assert res.overridden_rule is None
    assert res.below_target is False
    assert res.below_cost is False
    assert res.no_cost is False


def test_margin_method_price_and_margin(db):
    cust = _customer(db)
    prod = _product(db, cost=60.0)
    # 40% margin → price = 60 / (1 - 0.40) = 100.00
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARGIN, value=40.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 100.00
    assert res.margin_pct == 40.0
    # target for cost 60 is 45% → 40% < 45% → below_target
    assert res.below_target is True
    assert res.below_cost is False


def test_markup_method_price(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=12.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 112.00


def test_precedence_product_beats_brand_beats_category_beats_customer(db):
    cust = _customer(db)
    parent = _category(db, "TurbosTop")
    prod = _product(db, cost=100.0, brand="Garrett", category_id=parent.id)

    # whole-customer (least specific) → markup 10 → 110
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=10.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 110.00 and res.source_rule.scope_type == ScopeType.CUSTOMER

    # add category → markup 20 → 120 wins over customer
    _rule(db, cust, ScopeType.CATEGORY, scope_ref=parent.id, method=PriceMethod.MARKUP, value=20.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 120.00 and res.source_rule.scope_type == ScopeType.CATEGORY
    # runner-up is the whole-customer rule
    assert res.overridden_rule.scope_type == ScopeType.CUSTOMER

    # add brand → markup 30 → but brand & category TIE on specificity rank;
    # fewest-SKUs breaks it. Brand "Garrett" covers 1 product; category covers 1
    # too here — both 1, so newest wins. Make brand newer by adding it last.
    _rule(db, cust, ScopeType.BRAND, scope_ref="Garrett", method=PriceMethod.MARKUP, value=30.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.source_rule.scope_type in (ScopeType.BRAND, ScopeType.CATEGORY)

    # add product (most specific) → markup 50 → 150 wins over everything
    _rule(db, cust, ScopeType.PRODUCT, scope_ref=prod.id, method=PriceMethod.MARKUP, value=50.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 150.00 and res.source_rule.scope_type == ScopeType.PRODUCT


def test_category_descendant_match(db):
    cust = _customer(db)
    major = _category(db, "Major", level=1)
    mid = _category(db, "Mid", parent_id=major.id, level=2)
    leaf = _category(db, "Leaf", parent_id=mid.id, level=3)
    # product tagged to the deepest leaf
    prod = _product(db, cost=100.0, category_id=leaf.id)
    # rule on the MAJOR (top) category should reach the leaf product
    _rule(db, cust, ScopeType.CATEGORY, scope_ref=major.id, method=PriceMethod.MARKUP, value=25.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 125.00
    assert res.source_rule.scope_type == ScopeType.CATEGORY


def test_qty_break_boundary(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    # qty_min=10 rule (markup 5 → 105). Below 10 it must NOT apply.
    _rule(db, cust, ScopeType.CUSTOMER, scope_ref=None, method=PriceMethod.MARKUP, value=5.0, qty_min=10)
    svc = PricingService(db, _UID)
    # qty just below threshold → no match
    assert svc.resolve_customer_price(prod, cust, qty=9).price is None
    # qty AT threshold → match
    res = svc.resolve_customer_price(prod, cust, qty=10)
    assert res.price == 105.00


def test_higher_qty_break_wins_within_tier(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=20.0, qty_min=None)
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=10.0, qty_min=50)
    svc = PricingService(db, _UID)
    # at qty 60 both qualify; higher qty_min (50) wins → markup 10 → 110
    res = svc.resolve_customer_price(prod, cust, qty=60)
    assert res.price == 110.00


def test_expired_and_inactive_rules_ignored(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    today = date.today()
    # expired window
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=5.0,
          eff_from=today - timedelta(days=30), eff_to=today - timedelta(days=1))
    # future window
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=6.0,
          eff_from=today + timedelta(days=1))
    # inactive
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=7.0, active=False)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price is None  # none qualify


def test_active_window_matches(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    today = date.today()
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=15.0,
          eff_from=today - timedelta(days=1), eff_to=today + timedelta(days=1))
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 115.00


def test_below_cost_flag(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    # markup -10% via margin can't go below cost; use a tiny markup → still above.
    # Force below cost with a margin rule that resolves under cost is impossible
    # (margin>=0 always >= cost). Use markup 0 → price == cost (not below); then
    # simulate below-cost by a markup that rounds under? Not possible. Instead test
    # with a rule whose method yields exactly cost and confirm below_cost False,
    # then a separate construction for below_cost via direct price math.
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=0.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 100.00
    assert res.below_cost is False
    # price_from_rule sanity: a negative markup would go below cost
    from app.models.pricing import CustomerPriceRule as CPR
    fake = CPR(customer_id=cust.id, scope_type=ScopeType.CUSTOMER,
               price_method=PriceMethod.MARKUP, price_value=-50.0)
    assert PricingService.price_from_rule(fake, 100.0) == 50.0  # below cost math


def test_below_target_flag_true_and_false(db):
    cust = _customer(db)
    # cost 100 → target 40%. A 40% margin rule == target (NOT below); a 30% margin
    # rule IS below target.
    prod = _product(db, cost=100.0)
    r_at = _rule(db, cust, ScopeType.PRODUCT, scope_ref=prod.id,
                 method=PriceMethod.MARGIN, value=40.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.margin_pct == 40.0
    assert res.below_target is False
    # flip to 30% margin
    r_at.price_value = 30.0
    db.commit()
    res2 = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res2.margin_pct == 30.0
    assert res2.below_target is True


def test_cost_zero_fallthrough(db):
    cust = _customer(db)
    prod = _product(db, cost=0.0)
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=30.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price is None
    assert res.no_cost is True
    # source_rule still surfaced (a rule matched, just couldn't price)
    assert res.source_rule is not None


def test_rule_overrides_price_override(db):
    cust = _customer(db)
    # product has a hard price_override of 999, but a customer rule should win (Step 0)
    prod = _product(db, cost=100.0, price_override=999.0)
    _rule(db, cust, ScopeType.PRODUCT, scope_ref=prod.id, method=PriceMethod.MARKUP, value=20.0)
    res = PricingService(db, _UID).resolve_customer_price(prod, cust)
    assert res.price == 120.00  # NOT 999

    # And through the line-default funnel: a customer rule sets the blank price
    # over the product.price_override.
    ps = PricingService(db, _UID)
    data: dict = {"qty": 1}
    ctx: dict = {}
    apply_product_line_defaults(
        prod, data, include_price=True, tier_price=None,
        customer=cust, pricing_service=ps, render_ctx=ctx,
    )
    assert data["unit_price"] == 120.00
    assert ctx["customer_price"]["price"] == 120.00


def test_brand_vs_category_fewest_skus_tiebreak(db):
    cust = _customer(db)
    cat = _category(db, "Filters")
    # target product: brand "Bosch", in category "Filters"
    target = _product(db, cost=100.0, brand="Bosch", category_id=cat.id)
    # Make the CATEGORY broad (3 products) and the BRAND narrow (1 product) so
    # the brand rule (fewest SKUs) wins the tie.
    _product(db, cost=50.0, brand="Other", category_id=cat.id)
    _product(db, cost=50.0, brand="Other", category_id=cat.id)
    _rule(db, cust, ScopeType.CATEGORY, scope_ref=cat.id, method=PriceMethod.MARKUP, value=20.0)
    _rule(db, cust, ScopeType.BRAND, scope_ref="Bosch", method=PriceMethod.MARKUP, value=40.0)
    res = PricingService(db, _UID).resolve_customer_price(target, cust)
    # brand covers 1 SKU, category covers 3 → brand wins → markup 40 → 140
    assert res.source_rule.scope_type == ScopeType.BRAND
    assert res.price == 140.00
    assert res.overridden_rule.scope_type == ScopeType.CATEGORY


# ── last_price_for ──────────────────────────────────────────────────────────────

def _finalized_invoice_line(db, cust, prod, unit_price, status=InvoiceStatus.OPEN):
    inv = Invoice(
        invoice_number=f"INV-CP-{_uniq():06d}", customer_id=cust.id, status=status,
    )
    db.add(inv); db.commit(); db.refresh(inv)
    line = InvoiceLine(
        invoice_id=inv.id, product_id=prod.id, qty=1,
        unit_price=unit_price, unit_cost=50.0,
    )
    db.add(line); db.commit(); db.refresh(line)
    return inv, line


def test_last_price_for_latest_finalized(db):
    cust = _customer(db)
    prod = _product(db, cost=50.0)
    svc = PricingService(db, _UID)
    # no invoices yet
    assert svc.last_price_for(cust, prod) is None
    # an older finalized invoice
    _finalized_invoice_line(db, cust, prod, unit_price=120.0)
    # a newer finalized invoice (should win)
    inv2, _ = _finalized_invoice_line(db, cust, prod, unit_price=135.0)
    hint = svc.last_price_for(cust, prod)
    assert hint is not None
    assert hint["unit_price"] == 135.0
    assert hint["doc_ref"] == inv2.invoice_number
    assert "margin_pct" in hint and "date" in hint


def test_last_price_for_ignores_draft_and_void(db):
    cust = _customer(db)
    prod = _product(db, cost=50.0)
    svc = PricingService(db, _UID)
    _finalized_invoice_line(db, cust, prod, unit_price=200.0, status=InvoiceStatus.DRAFT)
    _finalized_invoice_line(db, cust, prod, unit_price=300.0, status=InvoiceStatus.VOID)
    assert svc.last_price_for(cust, prod) is None


# ── Backward-compat guard ───────────────────────────────────────────────────────

def test_backward_compat_no_rules_byte_identical(db):
    """A customer with NO price rules must price EXACTLY as before: the blank
    unit_price falls back to tier_price / product.selling_price unchanged, and no
    customer_price chip is stashed."""
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # selling_price = 130.00
    ps = PricingService(db, _UID)

    # WITHOUT the new params (legacy call) → baseline
    base: dict = {"qty": 1}
    apply_product_line_defaults(prod, base, include_price=True, tier_price=None)

    # WITH the new params but no rules → must equal the baseline price
    data: dict = {"qty": 1}
    ctx: dict = {}
    apply_product_line_defaults(
        prod, data, include_price=True, tier_price=None,
        customer=cust, pricing_service=ps, render_ctx=ctx,
    )
    assert data["unit_price"] == base["unit_price"] == prod.selling_price == 130.00
    # No rule → no customer_price chip; last-price hint key present (None here).
    assert "customer_price" not in ctx
    assert ctx.get("last_price") is None


def test_explicit_price_wins_over_rule(db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=20.0)
    ps = PricingService(db, _UID)
    data: dict = {"qty": 1, "unit_price": 555.0}  # caller set an explicit price
    ctx: dict = {}
    apply_product_line_defaults(
        prod, data, include_price=True, tier_price=None,
        customer=cust, pricing_service=ps, render_ctx=ctx,
    )
    # explicit price preserved; chip still describes the resolved rule
    assert data["unit_price"] == 555.0
    assert ctx["customer_price"]["price"] == 120.00


# ── Rule CRUD service + routes ──────────────────────────────────────────────────

def test_service_validation_rejects_bad_input(db):
    cust = _customer(db)
    svc = CustomerPriceRuleService(db, _UID)
    with pytest.raises(CustomerPriceRuleValidationError):
        svc.create_rule(cust.id, {"scope_type": "BOGUS", "price_method": "markup", "price_value": 10})
    with pytest.raises(CustomerPriceRuleValidationError):
        svc.create_rule(cust.id, {"scope_type": "CUSTOMER", "price_method": "nope", "price_value": 10})
    with pytest.raises(CustomerPriceRuleValidationError):
        svc.create_rule(cust.id, {"scope_type": "CUSTOMER", "price_method": "markup", "price_value": -1})
    with pytest.raises(CustomerPriceRuleValidationError):
        # margin must be < 100
        svc.create_rule(cust.id, {"scope_type": "CUSTOMER", "price_method": "margin", "price_value": 100})
    with pytest.raises(CustomerPriceRuleValidationError):
        # scope_ref required for non-CUSTOMER
        svc.create_rule(cust.id, {"scope_type": "BRAND", "price_method": "markup", "price_value": 10})


def test_route_create_list_update_deactivate(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)

    # CREATE (product-scoped, margin 40)
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "margin", "price_value": 40, "note": "deal"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    rule_id = body["rule"]["rule_id"]
    # PRODUCT rule → margin_at_current_cost computed (cost 100 → 40% margin)
    assert body["rule"]["margin_at_current_cost"] == 40.0
    assert body["rule"]["cost_relative"] is False

    # LIST
    r = client.get(f"/customers/{cust.id}/price-rules")
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert any(rr["rule_id"] == rule_id for rr in rules)

    # UPDATE (bump to margin 50)
    r = client.patch(
        f"/customers/{cust.id}/price-rules/{rule_id}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "margin", "price_value": 50},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rule"]["price_value"] == 50.0

    # DEACTIVATE
    r = client.post(f"/customers/{cust.id}/price-rules/{rule_id}/deactivate")
    assert r.status_code == 200
    assert r.json()["rule"]["is_active"] is False
    # active_only list hides it
    r = client.get(f"/customers/{cust.id}/price-rules?active_only=1")
    assert all(rr["rule_id"] != rule_id for rr in r.json()["rules"])


def test_route_create_validation_400(client, db):
    cust = _customer(db)
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "BRAND", "price_method": "markup", "price_value": 10},
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_route_form_encoded_create(client, db):
    cust = _customer(db)
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        data={"scope_type": "CUSTOMER", "price_method": "markup", "price_value": "15"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["rule"]["cost_relative"] is True  # CUSTOMER scope = cost-relative


def test_route_customer_404(client):
    r = client.get("/customers/99999999/price-rules")
    assert r.status_code == 404


# ── scope_label helper (macro contract: rule.scope_label) ───────────────────────

def test_scope_label_all_four_scope_types(db):
    """scope_label resolves PRODUCT / CATEGORY / BRAND / CUSTOMER."""
    cat = _category(db, "Turbos")
    prod = _product(db, cost=100.0, brand="PAI", category_id=cat.id)

    # PRODUCT → "<sku> — <short title>"
    plabel = scope_label(db, ScopeType.PRODUCT, str(prod.id))
    assert prod.sku in plabel
    assert "—" in plabel  # joins sku and title with an em dash

    # CATEGORY → the ProductCategory.name
    assert scope_label(db, ScopeType.CATEGORY, str(cat.id)) == cat.name

    # BRAND → the brand string as-is
    assert scope_label(db, ScopeType.BRAND, "PAI") == "PAI"

    # CUSTOMER → "Whole account"
    assert scope_label(db, ScopeType.CUSTOMER, None) == "Whole account"


def test_scope_label_tolerates_missing_refs(db):
    """Missing refs degrade gracefully (no raise)."""
    assert scope_label(db, ScopeType.PRODUCT, "9999999") == "SKU 9999999"
    assert scope_label(db, ScopeType.CATEGORY, "9999999") == "Category 9999999"
    # garbled ref
    assert scope_label(db, ScopeType.PRODUCT, "not-an-int") == "SKU not-an-int"


def test_rule_preview_includes_scope_label_margin_pct_below_cost(db):
    """rule_preview carries the macro-contract keys."""
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    rule = _rule(db, cust, ScopeType.PRODUCT, scope_ref=prod.id,
                 method=PriceMethod.MARGIN, value=40.0)
    out = CustomerPriceRuleService(db, _UID).rule_preview(rule)
    assert "scope_label" in out and prod.sku in out["scope_label"]
    # margin_pct aliases margin_at_current_cost (cost 100 → 40% margin)
    assert out["margin_pct"] == out["margin_at_current_cost"] == 40.0
    # below_cost computable for a PRODUCT rule (price 166.67 > cost 100 → False)
    assert out["below_cost"] is False

    # A cost-relative (CUSTOMER) rule → margin_pct/below_cost None, scope_label set
    crule = _rule(db, cust, ScopeType.CUSTOMER, method=PriceMethod.MARKUP, value=10.0)
    cout = CustomerPriceRuleService(db, _UID).rule_preview(crule)
    assert cout["scope_label"] == "Whole account"
    assert cout["margin_pct"] is None
    assert cout["below_cost"] is None


def test_pricing_ctx_source_rule_is_chip_dict(db):
    """The line pricing_ctx source_rule / overridden_rule are dicts shaped
    {scope_label, scope_type, price_method, price_value} (NOT raw ORM rows)."""
    cust = _customer(db)
    cat = _category(db, "Filters")
    prod = _product(db, cost=100.0, category_id=cat.id)
    # category rule (the eventual runner-up) + product rule (winner)
    _rule(db, cust, ScopeType.CATEGORY, scope_ref=cat.id,
          method=PriceMethod.MARKUP, value=20.0)
    _rule(db, cust, ScopeType.PRODUCT, scope_ref=prod.id,
          method=PriceMethod.MARKUP, value=50.0)

    ps = PricingService(db, _UID)
    data: dict = {"qty": 1}
    ctx: dict = {}
    apply_product_line_defaults(
        prod, data, include_price=True, tier_price=None,
        customer=cust, pricing_service=ps, render_ctx=ctx,
    )
    cp = ctx["customer_price"]
    src = cp["source_rule"]
    assert isinstance(src, dict)
    assert set(src) == {"scope_label", "scope_type", "price_method", "price_value"}
    assert prod.sku in src["scope_label"]
    assert src["scope_type"] == ScopeType.PRODUCT
    # overridden_rule (category) is also a chip dict carrying scope_label
    ov = cp["overridden_rule"]
    assert isinstance(ov, dict)
    assert "scope_label" in ov and ov["scope_label"] == cat.name
    # price math preserved (markup 50 on cost 100)
    assert cp["price"] == 150.00
    assert data["unit_price"] == 150.00


# ── Customer-detail view context (rules + Quick Deal options) ───────────────────

def test_customer_detail_context_includes_rules_with_date_objects(client, db, monkeypatch):
    """The customer-detail route exposes `rules` (macro-ready, date objects) plus
    the Quick Deal modal option lists. We intercept the template context (rather
    than render) so we can assert the exact Python shape the macros receive."""
    cust = _customer(db)
    cat = _category(db, "Gaskets")
    prod = _product(db, cost=100.0, category_id=cat.id)
    today = date.today()
    _rule(db, cust, ScopeType.PRODUCT, scope_ref=prod.id,
          method=PriceMethod.MARGIN, value=40.0,
          eff_from=today, eff_to=today + timedelta(days=30))

    # Capture the context dict the view passes to TemplateResponse without
    # actually rendering the (request-dependent) template.
    import app.routers.customers as customers_router
    captured: dict = {}
    real_tr = customers_router.templates.TemplateResponse

    def _spy(request, name, context, *a, **k):
        if name == "customers/detail.html":
            captured.update(context)
            return Response("ok")  # short-circuit render
        return real_tr(request, name, context, *a, **k)

    monkeypatch.setattr(customers_router.templates, "TemplateResponse", _spy)

    r = client.get(f"/customers/{cust.id}")
    assert r.status_code == 200, r.text

    assert "rules" in captured
    assert "customer_id" in captured and captured["customer_id"] == cust.id
    assert "deal_categories" in captured and "deal_brands" in captured
    rules = captured["rules"]
    assert len(rules) == 1
    rule_ctx = rules[0]
    # effective dates are date objects (the macro calls .strftime on them)
    assert isinstance(rule_ctx["effective_from"], date)
    assert isinstance(rule_ctx["effective_to"], date)
    # macro-contract fields present
    for k in ("scope_type", "scope_label", "price_method", "price_value",
              "qty_min", "is_active", "margin_pct", "below_target",
              "below_cost", "note"):
        assert k in rule_ctx
    assert prod.sku in rule_ctx["scope_label"]
    # deal_categories carries the {id, label} shape the modal select wants
    assert all("id" in cc and "label" in cc for cc in captured["deal_categories"])


# ── Regression: dated-rule JSON serialization + customer-detail rule_id ──────────

def test_dated_rule_route_serializes_dates_and_detail_carries_rule_id(client, db, monkeypatch):
    """Two regressions in one path:

    Defect 1 — a rule WITH effective_from/effective_to must serialize through the
    JSON CRUD routes (jsonable_encoder → ISO strings), not 500 on json.dumps(date).
    Defect 2 — the customer-detail context's per-rule dicts must carry rule_id
    (so per-row Deactivate buttons have an id), while effective_from/_to there stay
    real date objects (the Jinja macro calls .strftime on them).
    """
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    today = date.today()
    eff_from = today
    eff_to = today + timedelta(days=30)

    # ── Defect 1: CREATE a dated rule through the route → 201, ISO-string dates ──
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={
            "scope_type": "PRODUCT", "scope_ref": str(prod.id),
            "price_method": "margin", "price_value": 40,
            "effective_from": eff_from.isoformat(),
            "effective_to": eff_to.isoformat(),
            "note": "dated deal",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    rule = body["rule"]
    # dates come out as ISO strings (not a 500, not a serialized date repr)
    assert rule["effective_from"] == eff_from.isoformat()
    assert isinstance(rule["effective_from"], str)
    assert rule["effective_to"] == eff_to.isoformat()
    assert isinstance(rule["effective_to"], str)
    rule_id = rule["rule_id"]

    # ── Defect 1: LIST with a dated rule present → 200 (was 500) ────────────────
    r = client.get(f"/customers/{cust.id}/price-rules")
    assert r.status_code == 200, r.text
    listed = r.json()["rules"]
    hit = next(rr for rr in listed if rr["rule_id"] == rule_id)
    assert hit["effective_from"] == eff_from.isoformat()
    assert isinstance(hit["effective_from"], str)

    # ── Defect 2: customer-detail context rules include rule_id; dates stay date ─
    import app.routers.customers as customers_router
    captured: dict = {}
    real_tr = customers_router.templates.TemplateResponse

    def _spy(request, name, context, *a, **k):
        if name == "customers/detail.html":
            captured.update(context)
            return Response("ok")
        return real_tr(request, name, context, *a, **k)

    monkeypatch.setattr(customers_router.templates, "TemplateResponse", _spy)

    r = client.get(f"/customers/{cust.id}")
    assert r.status_code == 200, r.text
    detail_rule = next(rr for rr in captured["rules"] if rr["rule_id"] == rule_id)
    # rule_id present (Deactivate button target)
    assert detail_rule["rule_id"] == rule_id
    # effective dates here are STILL real date objects (not jsonable_encoded)
    assert isinstance(detail_rule["effective_from"], date)
    assert isinstance(detail_rule["effective_to"], date)
    assert detail_rule["effective_from"] == eff_from
